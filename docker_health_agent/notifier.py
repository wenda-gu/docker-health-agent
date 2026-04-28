from __future__ import annotations

import smtplib
from email.message import EmailMessage

import requests

from .models import AlertEvent, AlertsConfig


class NotificationError(RuntimeError):
    """Raised when every configured alert delivery channel fails."""


class AlertNotifier:
    def __init__(
        self,
        config: AlertsConfig,
        session: requests.Session | None = None,
    ) -> None:
        self.config = config
        self.session = session or requests.Session()

    def send(self, event: AlertEvent) -> bool:
        if not self.config.enabled:
            return False

        delivered = False
        errors: list[str] = []
        if self.config.webhook_url:
            try:
                self._send_webhook(event)
                delivered = True
            except NotificationError as exc:
                errors.append(str(exc))

        if self.config.email.enabled:
            try:
                self._send_email(event)
                delivered = True
            except NotificationError as exc:
                errors.append(str(exc))

        if not delivered:
            if errors:
                raise NotificationError("; ".join(errors))
            raise NotificationError("alerts.enabled is true but no delivery channel is configured.")
        return True

    def _send_webhook(self, event: AlertEvent) -> None:
        response = self.session.post(
            self.config.webhook_url,
            json=event.to_payload(),
            timeout=self.config.request_timeout_seconds,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise NotificationError(f"Webhook returned {response.status_code}: {exc}") from exc

    def _send_email(self, event: AlertEvent) -> None:
        email_config = self.config.email
        if not email_config.username or not email_config.password:
            raise NotificationError("Email alerts are enabled but SMTP credentials are missing.")
        if not email_config.from_address or not email_config.to_addresses:
            raise NotificationError("Email alerts are enabled but sender or recipients are missing.")

        message = EmailMessage()
        message["From"] = email_config.from_address
        message["To"] = ", ".join(email_config.to_addresses)
        message["Subject"] = (
            f"{email_config.subject_prefix} {event.level.upper()} "
            f"{event.service}: {event.action}"
        )
        message.set_content(_format_email_body(event))

        try:
            with smtplib.SMTP(
                email_config.smtp_host,
                email_config.smtp_port,
                timeout=email_config.timeout_seconds,
            ) as smtp:
                if email_config.starttls:
                    smtp.starttls()
                smtp.login(email_config.username, email_config.password)
                smtp.send_message(message)
        except OSError as exc:
            raise NotificationError(f"Email alert delivery failed: {exc}") from exc
        except smtplib.SMTPException as exc:
            raise NotificationError(f"Email alert delivery failed: {exc}") from exc


def _format_email_body(event: AlertEvent) -> str:
    payload = event.to_payload()
    lines = [
        "Docker health agent alert",
        "",
        f"Level: {payload['level']}",
        f"Service: {payload['service']}",
        f"Container: {payload['container']}",
        f"Status: {payload['status']}",
        f"Action: {payload['action']}",
        f"Timestamp: {payload['timestamp']}",
        "",
        str(payload["message"]),
    ]
    return "\n".join(lines) + "\n"


WebhookNotifier = AlertNotifier
