from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ServiceStatus(str, Enum):
    HEALTHY = "healthy"
    STARTING = "starting"
    UNHEALTHY = "unhealthy"
    EXITED = "exited"
    MISSING = "missing"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class DiscoveryConfig:
    enabled_label: str = "com.gu.health-agent.enabled"
    name_label: str = "com.gu.health-agent.name"
    auto_restart_label: str = "com.gu.health-agent.auto-restart"
    critical_label: str = "com.gu.health-agent.critical"
    public_url_label: str = "com.gu.health-agent.public-url"
    include_compose_projects: list[str] = field(default_factory=list)
    exclude_container_names: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DockerConfig:
    compose_project_name: str = ""
    compose_file: str = ""
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)


@dataclass(slots=True)
class EmailAlertsConfig:
    enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    username: str | None = None
    password: str | None = None
    from_address: str | None = None
    to_addresses: list[str] = field(default_factory=list)
    subject_prefix: str = "[docker-health-agent]"
    starttls: bool = True
    timeout_seconds: float = 10.0


@dataclass(slots=True)
class AlertsConfig:
    enabled: bool = False
    webhook_url: str | None = None
    request_timeout_seconds: float = 10.0
    email: EmailAlertsConfig = field(default_factory=EmailAlertsConfig)


@dataclass(slots=True)
class RecoveryConfig:
    enabled: bool = True
    unhealthy_grace_period_seconds: int = 120
    max_restarts_per_container_per_hour: int = 3
    cooldown_seconds_after_restart: int = 180
    starting_alert_threshold_seconds: int = 600
    alert_repeat_after_seconds: int = 1800


@dataclass(slots=True)
class ServiceConfig:
    name: str
    container_name: str
    public_url: str | None = None
    auto_restart: bool = False
    critical: bool = False
    source: str = "config"


@dataclass(slots=True)
class AgentConfig:
    poll_interval_seconds: int
    docker: DockerConfig
    alerts: AlertsConfig
    recovery: RecoveryConfig
    services: list[ServiceConfig] = field(default_factory=list)


@dataclass(slots=True)
class InspectionResult:
    service_name: str
    container_name: str
    status: ServiceStatus
    observed_at: datetime
    status_source: str
    raw_state_status: str | None = None
    raw_health_status: str | None = None
    restart_count: int = 0
    details: str = ""
    probe_url: str | None = None
    probe_status_code: int | None = None


@dataclass(slots=True)
class AlertEvent:
    level: str
    service: str
    container: str
    status: str
    action: str
    message: str
    timestamp: datetime

    def to_payload(self) -> dict[str, object]:
        return {
            "level": self.level,
            "service": self.service,
            "container": self.container,
            "status": self.status,
            "action": self.action,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
        }
