from __future__ import annotations

from datetime import datetime

from .models import AlertEvent, InspectionResult, ServiceConfig


def build_alert(
    *,
    level: str,
    service: ServiceConfig,
    inspection: InspectionResult,
    action: str,
    message: str,
    timestamp: datetime,
) -> AlertEvent:
    return AlertEvent(
        level=level,
        service=service.name,
        container=service.container_name,
        status=inspection.status.value,
        action=action,
        message=message,
        timestamp=timestamp,
    )


def manual_intervention_level(service: ServiceConfig) -> str:
    return "critical" if service.critical else "warning"


def recovery_succeeded_message(inspection: InspectionResult, unhealthy_for_seconds: int) -> str:
    return (
        f"Container {inspection.container_name} was {inspection.status.value} for "
        f"{unhealthy_for_seconds}s and has been restarted."
    )


def manual_intervention_message(
    inspection: InspectionResult,
    unhealthy_for_seconds: int | None = None,
) -> str:
    if unhealthy_for_seconds is None:
        return (
            f"Container {inspection.container_name} is {inspection.status.value}. "
            "Manual intervention is required."
        )
    return (
        f"Container {inspection.container_name} has been {inspection.status.value} for "
        f"{unhealthy_for_seconds}s. Manual intervention is required."
    )


def restart_limit_message(inspection: InspectionResult, max_restarts_per_hour: int) -> str:
    return (
        f"Container {inspection.container_name} is still {inspection.status.value}, "
        f"but restart protection blocked recovery after {max_restarts_per_hour} restart attempts in the last hour."
    )


def restart_failed_message(
    inspection: InspectionResult,
    unhealthy_for_seconds: int,
    error_message: str,
) -> str:
    return (
        f"Container {inspection.container_name} was {inspection.status.value} for "
        f"{unhealthy_for_seconds}s, but restart failed: {error_message}"
    )


def missing_container_message(inspection: InspectionResult) -> str:
    return f"Container {inspection.container_name} is missing. The agent will not recreate it automatically."


def stale_starting_message(inspection: InspectionResult, starting_for_seconds: int) -> str:
    return (
        f"Container {inspection.container_name} has been stuck in {inspection.status.value} for "
        f"{starting_for_seconds}s."
    )

