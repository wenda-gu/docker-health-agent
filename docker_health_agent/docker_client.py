from __future__ import annotations

from datetime import datetime, timezone

import docker
import requests
from docker.errors import DockerException, NotFound

from .models import DockerConfig, InspectionResult, ServiceConfig, ServiceStatus


class DockerRuntime:
    def __init__(
        self,
        client: docker.DockerClient | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.client = client or docker.from_env()
        self.session = session or requests.Session()

    def inspect_service(self, service: ServiceConfig) -> InspectionResult:
        observed_at = datetime.now(timezone.utc)
        try:
            container = self.client.containers.get(service.container_name)
        except NotFound:
            return InspectionResult(
                service_name=service.name,
                container_name=service.container_name,
                status=ServiceStatus.MISSING,
                observed_at=observed_at,
                status_source="docker_missing",
                details="Container was not found.",
            )
        except DockerException as exc:
            return InspectionResult(
                service_name=service.name,
                container_name=service.container_name,
                status=ServiceStatus.UNKNOWN,
                observed_at=observed_at,
                status_source="docker_error",
                details=f"Docker inspect failed: {exc}",
            )

        attrs = container.attrs or {}
        state = attrs.get("State", {}) or {}
        raw_state_status = _text_or_none(state.get("Status"))
        running = bool(state.get("Running"))
        health = state.get("Health", {}) if isinstance(state.get("Health"), dict) else {}
        raw_health_status = _text_or_none(health.get("Status"))
        restart_count = int(attrs.get("RestartCount", 0) or 0)

        if raw_health_status == "healthy":
            return InspectionResult(
                service_name=service.name,
                container_name=service.container_name,
                status=ServiceStatus.HEALTHY,
                observed_at=observed_at,
                status_source="docker_healthcheck",
                raw_state_status=raw_state_status,
                raw_health_status=raw_health_status,
                restart_count=restart_count,
            )

        if raw_health_status == "unhealthy":
            return InspectionResult(
                service_name=service.name,
                container_name=service.container_name,
                status=ServiceStatus.UNHEALTHY,
                observed_at=observed_at,
                status_source="docker_healthcheck",
                raw_state_status=raw_state_status,
                raw_health_status=raw_health_status,
                restart_count=restart_count,
            )

        if raw_health_status == "starting" or raw_state_status in {"created", "restarting"}:
            return InspectionResult(
                service_name=service.name,
                container_name=service.container_name,
                status=ServiceStatus.STARTING,
                observed_at=observed_at,
                status_source="docker_state",
                raw_state_status=raw_state_status,
                raw_health_status=raw_health_status,
                restart_count=restart_count,
            )

        if raw_state_status in {"exited", "dead", "removing"} or not running:
            return InspectionResult(
                service_name=service.name,
                container_name=service.container_name,
                status=ServiceStatus.EXITED,
                observed_at=observed_at,
                status_source="docker_state",
                raw_state_status=raw_state_status,
                raw_health_status=raw_health_status,
                restart_count=restart_count,
            )

        if service.public_url:
            return self._probe_public_url(
                service=service,
                observed_at=observed_at,
                raw_state_status=raw_state_status,
                raw_health_status=raw_health_status,
                restart_count=restart_count,
            )

        return InspectionResult(
            service_name=service.name,
            container_name=service.container_name,
            status=ServiceStatus.UNKNOWN,
            observed_at=observed_at,
            status_source="docker_state",
            raw_state_status=raw_state_status,
            raw_health_status=raw_health_status,
            restart_count=restart_count,
            details="Container is running but has no Docker healthcheck or HTTP probe configured.",
        )

    def discover_services(self, docker_config: DockerConfig) -> list[ServiceConfig]:
        discovery = docker_config.discovery
        try:
            containers = self.client.containers.list(
                all=True,
                filters={"label": [discovery.enabled_label]},
            )
        except DockerException as exc:
            raise RuntimeError(f"Failed to discover managed containers: {exc}") from exc

        include_projects = set(discovery.include_compose_projects)
        exclude_container_names = set(discovery.exclude_container_names)
        services: list[ServiceConfig] = []
        seen_names: dict[str, str] = {}

        for container in containers:
            attrs = container.attrs or {}
            labels = _labels(attrs)
            if not _bool_from_label(labels.get(discovery.enabled_label), default=False):
                continue

            container_name = _container_name(container, attrs)
            if not container_name or container_name in exclude_container_names:
                continue

            compose_project = _text_or_none(labels.get("com.docker.compose.project"))
            if include_projects and compose_project not in include_projects:
                continue

            service_name = _service_name(labels, container_name, discovery.name_label)
            previous_container = seen_names.get(service_name)
            if previous_container is not None and previous_container != container_name:
                raise RuntimeError(
                    f"Discovered duplicate health-agent service name '{service_name}' "
                    f"for containers '{previous_container}' and '{container_name}'."
                )

            seen_names[service_name] = container_name
            services.append(
                ServiceConfig(
                    name=service_name,
                    container_name=container_name,
                    public_url=_text_or_none(labels.get(discovery.public_url_label)),
                    auto_restart=_bool_from_label(
                        labels.get(discovery.auto_restart_label),
                        default=False,
                    ),
                    critical=_bool_from_label(
                        labels.get(discovery.critical_label),
                        default=False,
                    ),
                    source="docker_labels",
                )
            )

        return sorted(services, key=lambda service: service.name)

    def list_container_names(self) -> set[str]:
        try:
            containers = self.client.containers.list(all=True)
        except DockerException as exc:
            raise RuntimeError(f"Failed to list Docker containers: {exc}") from exc

        names: set[str] = set()
        for container in containers:
            container_name = _container_name(container, container.attrs or {})
            if container_name:
                names.add(container_name)
        return names

    def restart_container(self, container_name: str) -> None:
        container = self.client.containers.get(container_name)
        container.restart(timeout=10)

    def read_recent_logs(self, container_name: str, tail: int = 100) -> str:
        container = self.client.containers.get(container_name)
        return container.logs(tail=tail).decode("utf-8", errors="replace")

    def _probe_public_url(
        self,
        service: ServiceConfig,
        observed_at: datetime,
        raw_state_status: str | None,
        raw_health_status: str | None,
        restart_count: int,
    ) -> InspectionResult:
        try:
            response = self.session.get(service.public_url, timeout=10)
            if 200 <= response.status_code < 400:
                return InspectionResult(
                    service_name=service.name,
                    container_name=service.container_name,
                    status=ServiceStatus.HEALTHY,
                    observed_at=observed_at,
                    status_source="http_probe",
                    raw_state_status=raw_state_status,
                    raw_health_status=raw_health_status,
                    restart_count=restart_count,
                    probe_url=service.public_url,
                    probe_status_code=response.status_code,
                )

            return InspectionResult(
                service_name=service.name,
                container_name=service.container_name,
                status=ServiceStatus.UNHEALTHY,
                observed_at=observed_at,
                status_source="http_probe",
                raw_state_status=raw_state_status,
                raw_health_status=raw_health_status,
                restart_count=restart_count,
                probe_url=service.public_url,
                probe_status_code=response.status_code,
                details=f"HTTP probe returned status {response.status_code}.",
            )
        except requests.RequestException as exc:
            return InspectionResult(
                service_name=service.name,
                container_name=service.container_name,
                status=ServiceStatus.UNHEALTHY,
                observed_at=observed_at,
                status_source="http_probe",
                raw_state_status=raw_state_status,
                raw_health_status=raw_health_status,
                restart_count=restart_count,
                probe_url=service.public_url,
                details=f"HTTP probe failed: {exc}",
            )


def _text_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _labels(attrs: dict[str, object]) -> dict[str, str]:
    config = attrs.get("Config", {})
    if not isinstance(config, dict):
        return {}
    labels = config.get("Labels", {})
    if not isinstance(labels, dict):
        return {}
    return {str(key): str(value) for key, value in labels.items()}


def _container_name(container: object, attrs: dict[str, object]) -> str | None:
    name = _text_or_none(attrs.get("Name"))
    if not name:
        name = _text_or_none(getattr(container, "name", None))
    if not name:
        return None
    return name.lstrip("/")


def _service_name(labels: dict[str, str], container_name: str, name_label: str) -> str:
    explicit_name = _text_or_none(labels.get(name_label))
    if explicit_name:
        return explicit_name

    compose_project = _text_or_none(labels.get("com.docker.compose.project"))
    compose_service = _text_or_none(labels.get("com.docker.compose.service"))
    if compose_project and compose_service:
        return f"{compose_project}/{compose_service}"

    return container_name


def _bool_from_label(value: object, *, default: bool) -> bool:
    text = _text_or_none(value)
    if text is None:
        return default
    return text.lower() in {"1", "true", "yes", "on"}
