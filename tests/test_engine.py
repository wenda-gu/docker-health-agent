from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from docker_health_agent.engine import HealthAgent
from docker_health_agent.models import (
    AgentConfig,
    AlertsConfig,
    DiscoveryConfig,
    DockerConfig,
    InspectionResult,
    RecoveryConfig,
    ServiceConfig,
    ServiceStatus,
)
from docker_health_agent.state import AgentState, ServiceRuntimeState, StateStore


class FakeRuntime:
    def __init__(
        self,
        inspections: dict[str, InspectionResult],
        restart_error: Exception | None = None,
        discovered_services: list[ServiceConfig] | None = None,
        container_names: set[str] | None = None,
    ) -> None:
        self.inspections = inspections
        self.restart_error = restart_error
        self.discovered_services = discovered_services or []
        self.container_names = container_names or set()
        self.restarted: list[str] = []

    def inspect_service(self, service: ServiceConfig) -> InspectionResult:
        return self.inspections[service.name]

    def discover_services(self, docker_config: DockerConfig) -> list[ServiceConfig]:
        return list(self.discovered_services)

    def list_container_names(self) -> set[str]:
        return set(self.container_names)

    def restart_container(self, container_name: str) -> None:
        if self.restart_error is not None:
            raise self.restart_error
        self.restarted.append(container_name)


class FakeNotifier:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def send(self, event) -> bool:
        self.events.append(event.to_payload())
        return True


def build_config(services: list[ServiceConfig] | None = None) -> AgentConfig:
    return AgentConfig(
        poll_interval_seconds=60,
        docker=DockerConfig(discovery=DiscoveryConfig()),
        alerts=AlertsConfig(enabled=True, webhook_url="https://alerts.example.test"),
        recovery=RecoveryConfig(
            enabled=True,
            unhealthy_grace_period_seconds=120,
            max_restarts_per_container_per_hour=3,
            cooldown_seconds_after_restart=180,
            starting_alert_threshold_seconds=600,
            alert_repeat_after_seconds=1800,
        ),
        services=services or [],
    )


def build_inspection(
    *,
    service_name: str,
    container_name: str,
    status: ServiceStatus,
    observed_at: datetime,
) -> InspectionResult:
    return InspectionResult(
        service_name=service_name,
        container_name=container_name,
        status=status,
        observed_at=observed_at,
        status_source="docker_healthcheck",
    )


def test_unhealthy_service_restarts_after_grace_period(tmp_path: Path) -> None:
    now = datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc)
    service = ServiceConfig(
        name="app",
        container_name="app-1",
        auto_restart=True,
        critical=True,
    )
    runtime = FakeRuntime(
        inspections={
            "app": build_inspection(
                service_name="app",
                container_name="app-1",
                status=ServiceStatus.UNHEALTHY,
                observed_at=now,
            )
        }
    )
    store = StateStore(tmp_path / "state.json")
    store.save(
        AgentState(
            services={
                "app": ServiceRuntimeState(
                    last_status="unhealthy",
                    status_since=now - timedelta(seconds=180),
                )
            }
        )
    )
    notifier = FakeNotifier()
    agent = HealthAgent(runtime=runtime, state_store=store)

    agent.run_once(config=build_config([service]), notifier=notifier)

    assert runtime.restarted == ["app-1"]
    assert notifier.events[0]["action"] == "restarted"
    assert notifier.events[0]["level"] == "info"


def test_restart_limit_blocks_further_restarts(tmp_path: Path) -> None:
    now = datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc)
    service = ServiceConfig(
        name="worker",
        container_name="worker-1",
        auto_restart=True,
        critical=False,
    )
    runtime = FakeRuntime(
        inspections={
            "worker": build_inspection(
                service_name="worker",
                container_name="worker-1",
                status=ServiceStatus.UNHEALTHY,
                observed_at=now,
            )
        }
    )
    store = StateStore(tmp_path / "state.json")
    store.save(
        AgentState(
            services={
                "worker": ServiceRuntimeState(
                    last_status="unhealthy",
                    status_since=now - timedelta(seconds=500),
                    restart_timestamps=[
                        now - timedelta(minutes=10),
                        now - timedelta(minutes=20),
                        now - timedelta(minutes=30),
                    ],
                )
            }
        )
    )
    notifier = FakeNotifier()
    agent = HealthAgent(runtime=runtime, state_store=store)

    agent.run_once(config=build_config([service]), notifier=notifier)

    assert runtime.restarted == []
    assert notifier.events[0]["action"] == "restart_suppressed"
    assert notifier.events[0]["level"] == "critical"


def test_duplicate_manual_alerts_are_deduped_until_status_changes(tmp_path: Path) -> None:
    now = datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc)
    service = ServiceConfig(
        name="db",
        container_name="db-1",
        auto_restart=False,
        critical=True,
    )
    inspection = build_inspection(
        service_name="db",
        container_name="db-1",
        status=ServiceStatus.UNHEALTHY,
        observed_at=now,
    )
    runtime = FakeRuntime(inspections={"db": inspection})
    store = StateStore(tmp_path / "state.json")
    store.save(
        AgentState(
            services={
                "db": ServiceRuntimeState(
                    last_status="unhealthy",
                    status_since=now - timedelta(seconds=500),
                    last_alert_signature="unhealthy:manual",
                    last_alert_at=now - timedelta(seconds=60),
                )
            }
        )
    )
    notifier = FakeNotifier()
    agent = HealthAgent(runtime=runtime, state_store=store)

    agent.run_once(config=build_config([service]), notifier=notifier)

    assert notifier.events == []


def test_previously_discovered_label_managed_service_alerts_when_container_disappears(tmp_path: Path) -> None:
    now = datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc)
    runtime = FakeRuntime(
        inspections={},
        discovered_services=[],
        container_names=set(),
    )
    store = StateStore(tmp_path / "state.json")
    store.save(
        AgentState(
            services={
                "edge-proxy": ServiceRuntimeState(
                    last_status="healthy",
                    status_since=now - timedelta(seconds=60),
                    container_name="edge-proxy-proxy-1",
                    auto_restart=True,
                    critical=True,
                    source="docker_labels",
                )
            }
        )
    )
    notifier = FakeNotifier()
    agent = HealthAgent(runtime=runtime, state_store=store)

    agent.run_once(config=build_config(), notifier=notifier)

    assert notifier.events[0]["service"] == "edge-proxy"
    assert notifier.events[0]["status"] == "missing"
    assert notifier.events[0]["action"] == "manual_intervention_required"
