from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage

from docker_health_agent.models import AlertEvent, AlertsConfig, EmailAlertsConfig
from docker_health_agent.notifier import AlertNotifier


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.login_args: tuple[str, str] | None = None
        self.messages: list[EmailMessage] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.login_args = (username, password)

    def send_message(self, message: EmailMessage) -> None:
        self.messages.append(message)


def test_alert_notifier_sends_gmail_smtp_message(monkeypatch) -> None:
    FakeSMTP.instances = []
    monkeypatch.setattr("docker_health_agent.notifier.smtplib.SMTP", FakeSMTP)
    config = AlertsConfig(
        enabled=True,
        email=EmailAlertsConfig(
            enabled=True,
            smtp_host="smtp.gmail.com",
            smtp_port=587,
            username="alerts@example.test",
            password="app-password",
            from_address="alerts@example.test",
            to_addresses=["ops@example.test"],
            subject_prefix="[watchdog]",
        ),
    )
    event = AlertEvent(
        level="critical",
        service="rms-postgres",
        container="rms-postgres",
        status="unhealthy",
        action="manual_intervention_required",
        message="Container rms-postgres has been unhealthy for 120s.",
        timestamp=datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc),
    )

    sent = AlertNotifier(config).send(event)

    assert sent is True
    smtp = FakeSMTP.instances[0]
    assert smtp.host == "smtp.gmail.com"
    assert smtp.port == 587
    assert smtp.started_tls is True
    assert smtp.login_args == ("alerts@example.test", "app-password")
    assert len(smtp.messages) == 1
    message = smtp.messages[0]
    assert message["From"] == "alerts@example.test"
    assert message["To"] == "ops@example.test"
    assert message["Subject"] == "[watchdog] CRITICAL rms-postgres: manual_intervention_required"
    assert "Container rms-postgres has been unhealthy for 120s." in message.get_content()
