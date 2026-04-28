from __future__ import annotations

from dataclasses import dataclass

from docker.errors import NotFound

from docker_health_agent.docker_client import DockerRuntime
from docker_health_agent.models import DiscoveryConfig, DockerConfig, ServiceConfig, ServiceStatus


@dataclass
class FakeResponse:
    status_code: int


class FakeSession:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def get(self, url: str, timeout: int = 10) -> FakeResponse:
        return FakeResponse(status_code=self.status_code)


class FakeContainer:
    def __init__(self, attrs: dict[str, object], name: str | None = None) -> None:
        self.attrs = attrs
        self.name = name or str(attrs.get("Name", "")).lstrip("/")


class FakeContainerCollection:
    def __init__(self, container: FakeContainer | None = None, containers: list[FakeContainer] | None = None) -> None:
        self.container = container
        self._containers = containers or ([] if container is None else [container])

    def get(self, name: str) -> FakeContainer:
        if self.container is None:
            raise NotFound("missing")
        return self.container

    def list(self, all: bool = False, filters: dict[str, list[str]] | None = None) -> list[FakeContainer]:
        return list(self._containers)


class FakeDockerClient:
    def __init__(self, container: FakeContainer | None = None, containers: list[FakeContainer] | None = None) -> None:
        self.containers = FakeContainerCollection(container=container, containers=containers)


def test_inspect_service_uses_http_probe_when_healthcheck_is_missing() -> None:
    container = FakeContainer(
        attrs={
            "RestartCount": 1,
            "State": {
                "Running": True,
                "Status": "running",
            },
        }
    )
    runtime = DockerRuntime(
        client=FakeDockerClient(container),
        session=FakeSession(status_code=200),
    )

    inspection = runtime.inspect_service(
        ServiceConfig(
            name="frontend",
            container_name="frontend-1",
            public_url="https://example.test/healthz",
            auto_restart=True,
            critical=True,
        )
    )

    assert inspection.status is ServiceStatus.HEALTHY
    assert inspection.status_source == "http_probe"
    assert inspection.probe_status_code == 200


def test_inspect_service_marks_missing_container() -> None:
    runtime = DockerRuntime(
        client=FakeDockerClient(container=None),
        session=FakeSession(status_code=200),
    )

    inspection = runtime.inspect_service(
        ServiceConfig(
            name="frontend",
            container_name="frontend-1",
        )
    )

    assert inspection.status is ServiceStatus.MISSING
    assert inspection.status_source == "docker_missing"


def test_discover_services_reads_the_label_contract() -> None:
    runtime = DockerRuntime(
        client=FakeDockerClient(
            containers=[
                FakeContainer(
                    attrs={
                        "Name": "/ink2score-api-1",
                        "Config": {
                            "Labels": {
                                "com.gu.health-agent.enabled": "true",
                                "com.gu.health-agent.name": "ink2score-api",
                                "com.gu.health-agent.auto-restart": "true",
                                "com.gu.health-agent.critical": "true",
                                "com.docker.compose.project": "ink2score",
                            }
                        },
                    }
                ),
                FakeContainer(
                    attrs={
                        "Name": "/ignored-service",
                        "Config": {
                            "Labels": {
                                "com.gu.health-agent.enabled": "false",
                            }
                        },
                    }
                ),
            ]
        ),
        session=FakeSession(status_code=200),
    )

    services = runtime.discover_services(
        DockerConfig(
            discovery=DiscoveryConfig(
                include_compose_projects=["ink2score"],
            )
        )
    )

    assert [service.name for service in services] == ["ink2score-api"]
    assert services[0].container_name == "ink2score-api-1"
    assert services[0].auto_restart is True
    assert services[0].critical is True
    assert services[0].source == "docker_labels"
