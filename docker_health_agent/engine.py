from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timezone

from .config import ConfigError, load_config
from .docker_client import DockerRuntime
from .models import AgentConfig, InspectionResult, ServiceConfig, ServiceStatus
from .notifier import AlertNotifier, NotificationError
from .recovery import (
    build_alert,
    manual_intervention_level,
    manual_intervention_message,
    missing_container_message,
    recovery_succeeded_message,
    restart_failed_message,
    restart_limit_message,
    stale_starting_message,
)
from .state import AgentState, ServiceRuntimeState, StateStore


LOGGER = logging.getLogger("docker_health_agent")


class HealthAgent:
    def __init__(
        self,
        runtime: DockerRuntime,
        state_store: StateStore,
        logger: logging.Logger | None = None,
    ) -> None:
        self.runtime = runtime
        self.state_store = state_store
        self.logger = logger or LOGGER

    def run_once(
        self,
        config: AgentConfig,
        notifier: AlertNotifier | None = None,
    ) -> list[InspectionResult]:
        state = self.state_store.load()
        services = self._resolve_services(config)
        container_names = self.runtime.list_container_names()
        reports: list[InspectionResult] = []
        for service in services:
            service_state = state.get_service(service.name)
            inspection = self.runtime.inspect_service(service)
            service_state.record_definition(service, inspection.observed_at)
            service_state.record_status(inspection.status.value, inspection.observed_at)
            self._log_inspection(inspection)
            self._handle_service(
                config=config,
                service=service,
                inspection=inspection,
                service_state=service_state,
                notifier=notifier,
            )
            reports.append(inspection)

        for service in self._missing_discovered_services(
            state=state,
            active_services=services,
            container_names=container_names,
        ):
            service_state = state.get_service(service.name)
            inspection = InspectionResult(
                service_name=service.name,
                container_name=service.container_name,
                status=ServiceStatus.MISSING,
                observed_at=datetime.now(timezone.utc),
                status_source="state_registry",
                details="Container was previously discovered via Docker labels but is no longer present.",
            )
            service_state.record_status(inspection.status.value, inspection.observed_at)
            self._log_inspection(inspection)
            self._handle_service(
                config=config,
                service=service,
                inspection=inspection,
                service_state=service_state,
                notifier=notifier,
            )
            reports.append(inspection)

        self.state_store.save(state)
        return reports

    def _resolve_services(self, config: AgentConfig) -> list[ServiceConfig]:
        services_by_name: dict[str, ServiceConfig] = {
            service.name: service for service in self.runtime.discover_services(config.docker)
        }
        for service in config.services:
            services_by_name[service.name] = service

        services = [services_by_name[name] for name in sorted(services_by_name)]
        if not services:
            self.logger.info("No managed services discovered or configured for this poll.")
        return services

    def _missing_discovered_services(
        self,
        *,
        state: AgentState,
        active_services: list[ServiceConfig],
        container_names: set[str],
    ) -> list[ServiceConfig]:
        active_names = {service.name for service in active_services}
        missing: list[ServiceConfig] = []

        for service_name, service_state in state.services.items():
            if service_name in active_names or service_state.source != "docker_labels":
                continue
            if service_state.container_name in container_names:
                continue
            service = service_state.to_service_config(service_name)
            if service is not None:
                missing.append(service)

        return sorted(missing, key=lambda service: service.name)

    def _handle_service(
        self,
        *,
        config: AgentConfig,
        service: ServiceConfig,
        inspection: InspectionResult,
        service_state: ServiceRuntimeState,
        notifier: AlertNotifier | None,
    ) -> None:
        now = inspection.observed_at
        recovery = config.recovery
        status_age = service_state.duration_seconds(now)

        if inspection.status is ServiceStatus.HEALTHY:
            self.logger.debug("Service %s is healthy; no action required.", service.name)
            return

        if inspection.status is ServiceStatus.STARTING:
            if status_age >= recovery.starting_alert_threshold_seconds:
                signature = "starting:stale"
                alert = build_alert(
                    level=manual_intervention_level(service),
                    service=service,
                    inspection=inspection,
                    action="manual_intervention_required",
                    message=stale_starting_message(inspection, status_age),
                    timestamp=now,
                )
                self._emit_alert_if_needed(
                    service_state=service_state,
                    signature=signature,
                    alert=alert,
                    notifier=notifier,
                    repeat_after_seconds=recovery.alert_repeat_after_seconds,
                )
            return

        if inspection.status is ServiceStatus.MISSING:
            signature = "missing:manual"
            alert = build_alert(
                level=manual_intervention_level(service),
                service=service,
                inspection=inspection,
                action="manual_intervention_required",
                message=missing_container_message(inspection),
                timestamp=now,
            )
            self._emit_alert_if_needed(
                service_state=service_state,
                signature=signature,
                alert=alert,
                notifier=notifier,
                repeat_after_seconds=recovery.alert_repeat_after_seconds,
            )
            return

        if inspection.status is ServiceStatus.UNKNOWN:
            self.logger.warning(
                "Service %s is running without actionable health data: %s",
                service.name,
                inspection.details or "no further detail",
            )
            return

        if inspection.status is ServiceStatus.UNHEALTHY and status_age < recovery.unhealthy_grace_period_seconds:
            self.logger.info(
                "Service %s is unhealthy but still within the %ss grace period.",
                service.name,
                recovery.unhealthy_grace_period_seconds,
            )
            return

        if not recovery.enabled or not service.auto_restart:
            signature = f"{inspection.status.value}:manual"
            alert = build_alert(
                level=manual_intervention_level(service),
                service=service,
                inspection=inspection,
                action="manual_intervention_required",
                message=manual_intervention_message(inspection, status_age),
                timestamp=now,
            )
            self._emit_alert_if_needed(
                service_state=service_state,
                signature=signature,
                alert=alert,
                notifier=notifier,
                repeat_after_seconds=recovery.alert_repeat_after_seconds,
            )
            return

        if service_state.in_restart_cooldown(now, recovery.cooldown_seconds_after_restart):
            self.logger.info(
                "Service %s is in restart cooldown for %ss; skipping recovery.",
                service.name,
                recovery.cooldown_seconds_after_restart,
            )
            return

        if (
            service_state.restart_count_within_window(now)
            >= recovery.max_restarts_per_container_per_hour
        ):
            signature = f"{inspection.status.value}:restart_limit"
            alert = build_alert(
                level="critical",
                service=service,
                inspection=inspection,
                action="restart_suppressed",
                message=restart_limit_message(
                    inspection,
                    recovery.max_restarts_per_container_per_hour,
                ),
                timestamp=now,
            )
            self._emit_alert_if_needed(
                service_state=service_state,
                signature=signature,
                alert=alert,
                notifier=notifier,
                repeat_after_seconds=recovery.alert_repeat_after_seconds,
            )
            return

        try:
            self.runtime.restart_container(service.container_name)
        except Exception as exc:  # pragma: no cover - exercised via tests with fake runtime
            signature = f"{inspection.status.value}:restart_failed"
            alert = build_alert(
                level="critical",
                service=service,
                inspection=inspection,
                action="restart_failed",
                message=restart_failed_message(inspection, status_age, str(exc)),
                timestamp=now,
            )
            self._emit_alert_if_needed(
                service_state=service_state,
                signature=signature,
                alert=alert,
                notifier=notifier,
                repeat_after_seconds=recovery.alert_repeat_after_seconds,
            )
            return

        service_state.record_restart(now)
        signature = f"{inspection.status.value}:restarted"
        alert = build_alert(
            level="info",
            service=service,
            inspection=inspection,
            action="restarted",
            message=recovery_succeeded_message(inspection, status_age),
            timestamp=now,
        )
        self._emit_alert_if_needed(
            service_state=service_state,
            signature=signature,
            alert=alert,
            notifier=notifier,
            repeat_after_seconds=recovery.alert_repeat_after_seconds,
        )

    def _emit_alert_if_needed(
        self,
        *,
        service_state: ServiceRuntimeState,
        signature: str,
        alert,
        notifier: WebhookNotifier | None,
        repeat_after_seconds: int,
    ) -> None:
        if not service_state.should_send_alert(signature, alert.timestamp, repeat_after_seconds):
            return

        try:
            sent = notifier.send(alert) if notifier is not None else False
        except NotificationError as exc:
            self.logger.error("Failed to send alert for %s: %s", alert.service, exc)
            return

        if sent:
            self.logger.info("Alert sent for %s: %s", alert.service, alert.message)
        else:
            self.logger.warning("Alert not delivered for %s: %s", alert.service, alert.message)
        service_state.mark_alert_sent(signature, alert.timestamp, alert.action)

    def _log_inspection(self, inspection: InspectionResult) -> None:
        self.logger.info(
            "service=%s container=%s status=%s source=%s raw_state=%s raw_health=%s restarts=%s details=%s",
            inspection.service_name,
            inspection.container_name,
            inspection.status.value,
            inspection.status_source,
            inspection.raw_state_status or "-",
            inspection.raw_health_status or "-",
            inspection.restart_count,
            inspection.details or "-",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Conservative Docker health watchdog.")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--env-file", default=".env", help="Path to .env file")
    parser.add_argument(
        "--state-file",
        default="state.json",
        help="Path to persisted state.json",
    )
    parser.add_argument("--once", action="store_true", help="Run a single poll and exit")
    parser.add_argument("--log-level", default="INFO", help="Python log level")
    args = parser.parse_args(argv)

    configure_logging(args.log_level)
    runtime = DockerRuntime()
    state_store = StateStore(args.state_file)
    agent = HealthAgent(runtime=runtime, state_store=state_store, logger=LOGGER)
    sleep_seconds = 60

    while True:
        try:
            config = load_config(args.config, env_file=args.env_file)
            sleep_seconds = config.poll_interval_seconds
            notifier = AlertNotifier(config.alerts)
            agent.run_once(config=config, notifier=notifier)
            if args.once:
                return 0
        except ConfigError as exc:
            LOGGER.error("Config error: %s", exc)
            if args.once:
                return 2
        except Exception:
            LOGGER.exception("Docker health agent iteration failed")
            if args.once:
                return 1

        time.sleep(sleep_seconds)


def configure_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
