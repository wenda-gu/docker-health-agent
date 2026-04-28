from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from .models import ServiceConfig


@dataclass(slots=True)
class ServiceRuntimeState:
    last_status: str | None = None
    status_since: datetime | None = None
    last_action: str | None = None
    last_restart_at: datetime | None = None
    restart_timestamps: list[datetime] = field(default_factory=list)
    last_alert_signature: str | None = None
    last_alert_at: datetime | None = None
    container_name: str | None = None
    public_url: str | None = None
    auto_restart: bool = False
    critical: bool = False
    source: str | None = None
    last_seen_at: datetime | None = None

    def record_status(self, status: str, now: datetime) -> bool:
        if self.last_status != status:
            self.last_status = status
            self.status_since = now
            if status == "healthy":
                self.clear_alert_state()
            return True

        if self.status_since is None:
            self.status_since = now
        if status == "healthy":
            self.clear_alert_state()
        return False

    def duration_seconds(self, now: datetime) -> int:
        if self.status_since is None:
            return 0
        return max(0, int((now - self.status_since).total_seconds()))

    def prune_restart_history(self, now: datetime, window_seconds: int = 3600) -> None:
        cutoff = now - timedelta(seconds=window_seconds)
        self.restart_timestamps = [ts for ts in self.restart_timestamps if ts >= cutoff]

    def restart_count_within_window(self, now: datetime, window_seconds: int = 3600) -> int:
        self.prune_restart_history(now, window_seconds=window_seconds)
        return len(self.restart_timestamps)

    def in_restart_cooldown(self, now: datetime, cooldown_seconds: int) -> bool:
        if self.last_restart_at is None:
            return False
        return (now - self.last_restart_at).total_seconds() < cooldown_seconds

    def record_restart(self, now: datetime) -> None:
        self.last_restart_at = now
        self.restart_timestamps.append(now)
        self.last_action = "restarted"

    def should_send_alert(self, signature: str, now: datetime, repeat_after_seconds: int) -> bool:
        if self.last_alert_signature != signature:
            return True
        if self.last_alert_at is None:
            return True
        return (now - self.last_alert_at).total_seconds() >= repeat_after_seconds

    def mark_alert_sent(self, signature: str, now: datetime, action: str) -> None:
        self.last_alert_signature = signature
        self.last_alert_at = now
        self.last_action = action

    def clear_alert_state(self) -> None:
        self.last_alert_signature = None
        self.last_alert_at = None

    def record_definition(self, service: ServiceConfig, now: datetime) -> None:
        self.container_name = service.container_name
        self.public_url = service.public_url
        self.auto_restart = service.auto_restart
        self.critical = service.critical
        self.source = service.source
        self.last_seen_at = now

    def has_definition(self) -> bool:
        return self.container_name is not None and self.source is not None

    def to_service_config(self, service_name: str) -> ServiceConfig | None:
        if not self.container_name:
            return None
        return ServiceConfig(
            name=service_name,
            container_name=self.container_name,
            public_url=self.public_url,
            auto_restart=self.auto_restart,
            critical=self.critical,
            source=self.source or "docker_labels",
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "last_status": self.last_status,
            "status_since": _dt_to_text(self.status_since),
            "last_action": self.last_action,
            "last_restart_at": _dt_to_text(self.last_restart_at),
            "restart_timestamps": [_dt_to_text(ts) for ts in self.restart_timestamps],
            "last_alert_signature": self.last_alert_signature,
            "last_alert_at": _dt_to_text(self.last_alert_at),
            "container_name": self.container_name,
            "public_url": self.public_url,
            "auto_restart": self.auto_restart,
            "critical": self.critical,
            "source": self.source,
            "last_seen_at": _dt_to_text(self.last_seen_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ServiceRuntimeState":
        return cls(
            last_status=_text_or_none(data.get("last_status")),
            status_since=_dt_from_text(data.get("status_since")),
            last_action=_text_or_none(data.get("last_action")),
            last_restart_at=_dt_from_text(data.get("last_restart_at")),
            restart_timestamps=[
                parsed
                for parsed in (
                    _dt_from_text(item) for item in data.get("restart_timestamps", [])
                )
                if parsed is not None
            ],
            last_alert_signature=_text_or_none(data.get("last_alert_signature")),
            last_alert_at=_dt_from_text(data.get("last_alert_at")),
            container_name=_text_or_none(data.get("container_name")),
            public_url=_text_or_none(data.get("public_url")),
            auto_restart=_bool_or_false(data.get("auto_restart")),
            critical=_bool_or_false(data.get("critical")),
            source=_text_or_none(data.get("source")),
            last_seen_at=_dt_from_text(data.get("last_seen_at")),
        )


@dataclass(slots=True)
class AgentState:
    services: dict[str, ServiceRuntimeState] = field(default_factory=dict)

    def get_service(self, service_name: str) -> ServiceRuntimeState:
        if service_name not in self.services:
            self.services[service_name] = ServiceRuntimeState()
        return self.services[service_name]

    def to_dict(self) -> dict[str, object]:
        return {
            "services": {
                name: service_state.to_dict()
                for name, service_state in sorted(self.services.items())
            }
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "AgentState":
        raw_services = data.get("services", {})
        if not isinstance(raw_services, dict):
            return cls()
        return cls(
            services={
                name: ServiceRuntimeState.from_dict(raw_state)
                for name, raw_state in raw_services.items()
                if isinstance(name, str) and isinstance(raw_state, dict)
            }
        )


class StateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> AgentState:
        if not self.path.exists():
            return AgentState()

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"State file {self.path} must contain a JSON object.")
        return AgentState.from_dict(payload)

    def save(self, state: AgentState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state.to_dict(), indent=2, sort_keys=True)
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=self.path.parent,
            delete=False,
        ) as handle:
            handle.write(payload)
            handle.flush()
            temp_path = Path(handle.name)
        temp_path.replace(self.path)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_to_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _dt_from_text(value: object) -> datetime | None:
    text = _text_or_none(value)
    if not text:
        return None
    return datetime.fromisoformat(text)


def _text_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool_or_false(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = _text_or_none(value)
    if text is None:
        return False
    return text.lower() in {"1", "true", "yes", "on"}
