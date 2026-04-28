from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from .models import (
    AgentConfig,
    AlertsConfig,
    DiscoveryConfig,
    DockerConfig,
    EmailAlertsConfig,
    RecoveryConfig,
    ServiceConfig,
)


class ConfigError(ValueError):
    """Raised when config.yaml is missing required or invalid values."""


def load_config(config_path: str | Path, env_file: str | Path | None = None) -> AgentConfig:
    config_file = Path(config_path)
    if not config_file.exists():
        raise ConfigError(f"Config file not found: {config_file}")

    if env_file is not None:
        env_path = Path(env_file)
        if env_path.exists():
            load_dotenv(env_path, override=False)
    else:
        default_env = config_file.with_name(".env")
        if default_env.exists():
            load_dotenv(default_env, override=False)

    rendered = os.path.expandvars(config_file.read_text(encoding="utf-8"))
    raw = yaml.safe_load(rendered) or {}
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a YAML mapping.")

    poll_interval = _require_positive_int(raw.get("poll_interval_seconds", 60), "poll_interval_seconds")

    docker_raw = _mapping(raw.get("docker", {}), "docker")
    alerts_raw = _mapping(raw.get("alerts", {}), "alerts")
    email_raw = _mapping(alerts_raw.get("email", {}), "alerts.email")
    recovery_raw = _mapping(raw.get("recovery", {}), "recovery")
    discovery_raw = _mapping(docker_raw.get("discovery", {}), "docker.discovery")
    services_raw = raw.get("services", [])
    if not isinstance(services_raw, list):
        raise ConfigError("services must be a YAML list when present.")

    email_username = _optional_string(email_raw.get("username"))
    email = EmailAlertsConfig(
        enabled=_bool(email_raw.get("enabled", False)),
        smtp_host=_optional_string(email_raw.get("smtp_host")) or "smtp.gmail.com",
        smtp_port=_require_positive_int(email_raw.get("smtp_port", 587), "alerts.email.smtp_port"),
        username=email_username,
        password=_optional_string(email_raw.get("password")),
        from_address=_optional_string(email_raw.get("from_address")) or email_username,
        to_addresses=_string_list_or_csv(
            email_raw.get("to_addresses", []),
            "alerts.email.to_addresses",
        ),
        subject_prefix=_optional_string(email_raw.get("subject_prefix")) or "[docker-health-agent]",
        starttls=_bool(email_raw.get("starttls", True)),
        timeout_seconds=_require_positive_float(
            email_raw.get("timeout_seconds", 10),
            "alerts.email.timeout_seconds",
        ),
    )
    alerts = AlertsConfig(
        enabled=_bool(alerts_raw.get("enabled", False)),
        webhook_url=_optional_string(alerts_raw.get("webhook_url")),
        request_timeout_seconds=_require_positive_float(
            alerts_raw.get("request_timeout_seconds", 10),
            "alerts.request_timeout_seconds",
        ),
        email=email,
    )
    if alerts.enabled:
        if not alerts.webhook_url and not alerts.email.enabled:
            raise ConfigError(
                "alerts.enabled is true but no webhook_url or alerts.email channel is configured."
            )
        _reject_unresolved_env(alerts.webhook_url, "alerts.webhook_url")
        if alerts.email.enabled:
            _validate_email_alerts(alerts.email)

    include_compose_projects = _string_list(
        discovery_raw.get("include_compose_projects", []),
        "docker.discovery.include_compose_projects",
    )
    compose_project_name = _optional_string(docker_raw.get("compose_project_name")) or ""
    if compose_project_name and not include_compose_projects:
        include_compose_projects = [compose_project_name]

    services: list[ServiceConfig] = []
    seen_names: set[str] = set()
    seen_containers: set[str] = set()
    for index, item in enumerate(services_raw):
        service_raw = _mapping(item, f"services[{index}]")
        name = _require_non_empty_string(service_raw.get("name"), f"services[{index}].name")
        container_name = _require_non_empty_string(
            service_raw.get("container_name"),
            f"services[{index}].container_name",
        )
        if name in seen_names:
            raise ConfigError(f"Duplicate service name: {name}")
        if container_name in seen_containers:
            raise ConfigError(f"Duplicate container_name: {container_name}")
        seen_names.add(name)
        seen_containers.add(container_name)
        services.append(
            ServiceConfig(
                name=name,
                container_name=container_name,
                public_url=_optional_string(service_raw.get("public_url")),
                auto_restart=bool(service_raw.get("auto_restart", False)),
                critical=bool(service_raw.get("critical", False)),
                source="config",
            )
        )

    return AgentConfig(
        poll_interval_seconds=poll_interval,
        docker=DockerConfig(
            compose_project_name=compose_project_name,
            compose_file=_optional_string(docker_raw.get("compose_file")) or "",
            discovery=DiscoveryConfig(
                enabled_label=_require_non_empty_string(
                    discovery_raw.get("enabled_label", "com.gu.health-agent.enabled"),
                    "docker.discovery.enabled_label",
                ),
                name_label=_require_non_empty_string(
                    discovery_raw.get("name_label", "com.gu.health-agent.name"),
                    "docker.discovery.name_label",
                ),
                auto_restart_label=_require_non_empty_string(
                    discovery_raw.get("auto_restart_label", "com.gu.health-agent.auto-restart"),
                    "docker.discovery.auto_restart_label",
                ),
                critical_label=_require_non_empty_string(
                    discovery_raw.get("critical_label", "com.gu.health-agent.critical"),
                    "docker.discovery.critical_label",
                ),
                public_url_label=_require_non_empty_string(
                    discovery_raw.get("public_url_label", "com.gu.health-agent.public-url"),
                    "docker.discovery.public_url_label",
                ),
                include_compose_projects=include_compose_projects,
                exclude_container_names=_string_list(
                    discovery_raw.get("exclude_container_names", []),
                    "docker.discovery.exclude_container_names",
                ),
            ),
        ),
        alerts=alerts,
        recovery=RecoveryConfig(
            enabled=bool(recovery_raw.get("enabled", True)),
            unhealthy_grace_period_seconds=_require_non_negative_int(
                recovery_raw.get("unhealthy_grace_period_seconds", 120),
                "recovery.unhealthy_grace_period_seconds",
            ),
            max_restarts_per_container_per_hour=_require_non_negative_int(
                recovery_raw.get("max_restarts_per_container_per_hour", 3),
                "recovery.max_restarts_per_container_per_hour",
            ),
            cooldown_seconds_after_restart=_require_non_negative_int(
                recovery_raw.get("cooldown_seconds_after_restart", 180),
                "recovery.cooldown_seconds_after_restart",
            ),
            starting_alert_threshold_seconds=_require_non_negative_int(
                recovery_raw.get("starting_alert_threshold_seconds", 600),
                "recovery.starting_alert_threshold_seconds",
            ),
            alert_repeat_after_seconds=_require_non_negative_int(
                recovery_raw.get("alert_repeat_after_seconds", 1800),
                "recovery.alert_repeat_after_seconds",
            ),
        ),
        services=services,
    )


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{field_name} must be a YAML mapping.")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _require_non_empty_string(value: Any, field_name: str) -> str:
    text = _optional_string(value)
    if not text:
        raise ConfigError(f"{field_name} is required.")
    return text


def _require_positive_int(value: Any, field_name: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ConfigError(f"{field_name} must be greater than 0.")
    return parsed


def _require_non_negative_int(value: Any, field_name: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise ConfigError(f"{field_name} must be 0 or greater.")
    return parsed


def _require_positive_float(value: Any, field_name: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise ConfigError(f"{field_name} must be greater than 0.")
    return parsed


def _string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{field_name} must be a YAML list.")

    items: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        text = _require_non_empty_string(item, f"{field_name}[{index}]")
        if text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def _string_list_or_csv(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if not isinstance(value, list):
        raise ConfigError(f"{field_name} must be a YAML list or comma-separated string.")

    items: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _optional_string(item)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = _optional_string(value)
    if text is None:
        return False
    return text.lower() in {"1", "true", "yes", "on"}


def _reject_unresolved_env(value: str | None, field_name: str) -> None:
    if value and "${" in value:
        raise ConfigError(f"{field_name} still contains an unresolved environment variable.")


def _validate_email_alerts(email: EmailAlertsConfig) -> None:
    required_fields = {
        "alerts.email.smtp_host": email.smtp_host,
        "alerts.email.username": email.username,
        "alerts.email.password": email.password,
        "alerts.email.from_address": email.from_address,
        "alerts.email.subject_prefix": email.subject_prefix,
    }
    for field_name, value in required_fields.items():
        if not value:
            raise ConfigError(f"{field_name} is required when alerts.email.enabled is true.")
        _reject_unresolved_env(value, field_name)

    if not email.to_addresses:
        raise ConfigError("alerts.email.to_addresses is required when alerts.email.enabled is true.")
    for index, address in enumerate(email.to_addresses):
        _reject_unresolved_env(address, f"alerts.email.to_addresses[{index}]")
