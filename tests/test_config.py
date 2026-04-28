from pathlib import Path

import pytest

from docker_health_agent.config import ConfigError, load_config


def test_load_config_expands_environment_variables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://alerts.example.test/webhook")
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
poll_interval_seconds: 30
docker:
  discovery:
    include_compose_projects:
      - ink2score
alerts:
  enabled: true
  webhook_url: "${ALERT_WEBHOOK_URL}"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.poll_interval_seconds == 30
    assert config.alerts.enabled is True
    assert config.alerts.webhook_url == "https://alerts.example.test/webhook"
    assert config.docker.discovery.include_compose_projects == ["ink2score"]
    assert config.services == []


def test_load_config_expands_email_alert_environment_variables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GMAIL_SMTP_USERNAME", "alerts@example.test")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password")
    monkeypatch.setenv("ALERT_EMAIL_FROM", "alerts@example.test")
    monkeypatch.setenv("ALERT_EMAIL_TO", "ops@example.test")
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
alerts:
  enabled: true
  email:
    enabled: true
    username: "${GMAIL_SMTP_USERNAME}"
    password: "${GMAIL_APP_PASSWORD}"
    from_address: "${ALERT_EMAIL_FROM}"
    to_addresses:
      - "${ALERT_EMAIL_TO}"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.alerts.enabled is True
    assert config.alerts.webhook_url is None
    assert config.alerts.email.enabled is True
    assert config.alerts.email.smtp_host == "smtp.gmail.com"
    assert config.alerts.email.smtp_port == 587
    assert config.alerts.email.username == "alerts@example.test"
    assert config.alerts.email.password == "app-password"
    assert config.alerts.email.from_address == "alerts@example.test"
    assert config.alerts.email.to_addresses == ["ops@example.test"]


def test_load_config_allows_disabled_email_with_empty_placeholder_values(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
alerts:
  enabled: false
  email:
    enabled: false
    username: ""
    password: ""
    from_address: ""
    to_addresses:
      - ""
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.alerts.enabled is False
    assert config.alerts.email.enabled is False
    assert config.alerts.email.to_addresses == []


def test_load_config_rejects_enabled_alerts_without_delivery_channel(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
alerts:
  enabled: true
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_config(config_file)


def test_load_config_rejects_enabled_email_with_unresolved_secret(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
alerts:
  enabled: true
  email:
    enabled: true
    username: "${GMAIL_SMTP_USERNAME}"
    password: "${GMAIL_APP_PASSWORD}"
    to_addresses:
      - "ops@example.test"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_config(config_file)


def test_load_config_supports_optional_static_service_overrides(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
docker:
  compose_project_name: ink2score
services:
  - name: edge-proxy
    container_name: edge-proxy-proxy-1
    auto_restart: true
    critical: true
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.docker.discovery.include_compose_projects == ["ink2score"]
    assert config.services[0].name == "edge-proxy"
    assert config.services[0].source == "config"
