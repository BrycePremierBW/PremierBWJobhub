"""Add secret-safe deployment integration checks to JobHub System Health.

The base System Health page verifies the database, storage, archives, recovery
readiness and core data. This guard extends the same report with configuration
readiness for optional services that cannot be exercised safely by repository
tests: public URLs, push, email, offline sync, external AI and BrightHR/Blip.

Only configured/not-configured state is reported. Secret values are never
included in the report or rendered to the page.
"""

from __future__ import annotations

import functools
import os
from typing import Any

from . import system_health_v2_guard as system_health_guard


PATCH_MARKER = "_pb_integration_health_guard"
TRUE_VALUES = {"1", "true", "yes", "on"}


def _enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return bool(default)
    return str(value).strip().lower() in TRUE_VALUES


def _configured(name: str) -> bool:
    return bool(str(os.getenv(name, "") or "").strip())


def _configured_any(*names: str) -> bool:
    return any(_configured(name) for name in names)


def _check(name: str, status: str, detail: str) -> dict[str, str]:
    return system_health_guard._check("Integrations", name, status, detail)


def _bright_hr_configuration() -> tuple[str, str, dict[str, str]]:
    """Return BrightHR readiness without reading secret values into the report."""
    required = (
        "BRIGHTHR_CLIENT_ID",
        "BRIGHTHR_CLIENT_SECRET",
        "BRIGHTHR_TOKEN_URL",
        "BRIGHTHR_EMPLOYEES_URL",
        "BRIGHTHR_BLIP_ATTENDANCE_URL",
    )
    configured = {name: _configured(name) for name in required}
    count = sum(configured.values())
    if count == len(required):
        status = "Healthy"
        detail = "BrightHR/Blip credentials and required API endpoints are configured."
    elif count:
        status = "Critical"
        missing = [name for name, present in configured.items() if not present]
        detail = "BrightHR/Blip is only partly configured; missing: " + ", ".join(missing) + "."
    else:
        status = "Info"
        detail = "BrightHR/Blip integration is not configured in this runtime."
    metrics = {
        "BrightHR client configured": "Yes" if configured["BRIGHTHR_CLIENT_ID"] else "No",
        "BrightHR client secret configured": "Yes" if configured["BRIGHTHR_CLIENT_SECRET"] else "No",
        "BrightHR employee endpoint configured": "Yes" if configured["BRIGHTHR_EMPLOYEES_URL"] else "No",
        "BrightHR Blip endpoint configured": "Yes" if configured["BRIGHTHR_BLIP_ATTENDANCE_URL"] else "No",
    }
    return status, detail, metrics


def integration_health_report() -> tuple[list[dict[str, str]], dict[str, Any]]:
    checks: list[dict[str, str]] = []
    metrics: dict[str, Any] = {}

    use_postgres = bool(system_health_guard._app_attr("USE_POSTGRES", False))
    database_url = _configured("DATABASE_URL")
    if use_postgres:
        checks.append(
            _check(
                "Production database configuration",
                "Healthy" if database_url else "Critical",
                "DATABASE_URL is configured."
                if database_url
                else "PostgreSQL is selected but DATABASE_URL is missing.",
            )
        )
    else:
        checks.append(
            _check(
                "Production database configuration",
                "Info",
                "JobHub is currently using SQLite; DATABASE_URL is not required for this runtime.",
            )
        )
    metrics["Database URL configured"] = "Yes" if database_url else "No"

    # APP_BASE_URL is the explicit production setting in render.yaml. Keep the
    # Render-provided aliases too so staging/preview services are diagnosed.
    public_url = _configured_any(
        "APP_BASE_URL",
        "JOBHUB_PUBLIC_URL",
        "RENDER_EXTERNAL_URL",
        "RENDER_SERVICE_URL",
        "RENDER_EXTERNAL_HOSTNAME",
    )
    checks.append(
        _check(
            "Public JobHub URL",
            "Healthy" if public_url else "Warning",
            "A public URL is configured for notification links and callbacks."
            if public_url
            else "No public JobHub URL was detected; push links and external callbacks may not open the app.",
        )
    )
    metrics["Public URL configured"] = "Yes" if public_url else "No"

    one_signal_app = _configured_any(
        "ONESIGNAL_APP_ID", "ONE_SIGNAL_APP_ID", "ONESIGNAL_WEB_APP_ID"
    )
    one_signal_key = _configured_any(
        "ONESIGNAL_REST_API_KEY",
        "ONESIGNAL_API_KEY",
        "ONESIGNAL_REST_KEY",
        "ONESIGNAL_AUTH_KEY",
    )
    if one_signal_app and one_signal_key:
        push_status = "Healthy"
        push_detail = "OneSignal app and server credentials are configured."
    elif one_signal_app or one_signal_key:
        push_status = "Critical"
        push_detail = "OneSignal is only partly configured; both the app ID and REST API key are required."
    else:
        push_status = "Warning"
        push_detail = "OneSignal is not configured, so device push notifications are unavailable."
    checks.append(_check("Device push notifications", push_status, push_detail))
    metrics["OneSignal app configured"] = "Yes" if one_signal_app else "No"
    metrics["OneSignal server key configured"] = "Yes" if one_signal_key else "No"

    email_enabled = _enabled("JOBHUB_EMAIL_NOTIFICATIONS_ENABLED")
    email_provider = _configured("JOBHUB_EMAIL_PROVIDER")
    email_from = _configured("JOBHUB_EMAIL_FROM")
    if not email_enabled:
        email_status = "Info"
        email_detail = "Email notifications are disabled by configuration."
    elif email_provider and email_from:
        email_status = "Healthy"
        email_detail = "Email notifications are enabled and required sender settings are configured."
    else:
        email_status = "Critical"
        missing = []
        if not email_provider:
            missing.append("JOBHUB_EMAIL_PROVIDER")
        if not email_from:
            missing.append("JOBHUB_EMAIL_FROM")
        email_detail = "Email notifications are enabled but missing: " + ", ".join(missing) + "."
    checks.append(_check("Email notifications", email_status, email_detail))
    metrics["Email notifications enabled"] = "Yes" if email_enabled else "No"
    metrics["Email sender configured"] = "Yes" if email_provider and email_from else "No"

    offline_enabled = _enabled("JOBHUB_OFFLINE_SYNC_ENABLED")
    checks.append(
        _check(
            "Offline synchronisation",
            "Healthy" if offline_enabled else "Info",
            "Offline synchronisation is enabled."
            if offline_enabled
            else "Offline synchronisation is disabled by configuration.",
        )
    )
    metrics["Offline sync enabled"] = "Yes" if offline_enabled else "No"

    external_ai_enabled = _enabled("JOBHUB_ALLOW_EXTERNAL_AI")
    ai_provider = str(os.getenv("AI_PROVIDER", "none") or "none").strip().lower()
    if external_ai_enabled and ai_provider in {"", "none", "disabled", "off"}:
        ai_status = "Critical"
        ai_detail = "External AI is enabled but no AI provider is configured."
    elif external_ai_enabled:
        ai_status = "Healthy"
        ai_detail = f"External AI is enabled with provider '{ai_provider}'."
    else:
        ai_status = "Healthy"
        ai_detail = "External AI access is disabled by design."
    checks.append(_check("External AI", ai_status, ai_detail))
    metrics["External AI enabled"] = "Yes" if external_ai_enabled else "No"
    metrics["AI provider"] = ai_provider or "none"

    bright_status, bright_detail, bright_metrics = _bright_hr_configuration()
    checks.append(_check("BrightHR / Blip", bright_status, bright_detail))
    metrics.update(bright_metrics)

    self_edit_enabled = _enabled("JOBHUB_ENABLE_SELF_EDIT")
    checks.append(
        _check(
            "Self-editing code",
            "Warning" if self_edit_enabled else "Healthy",
            "Self-editing is enabled and should only be used in an isolated staging environment."
            if self_edit_enabled
            else "Self-editing is disabled.",
        )
    )
    metrics["Self-edit enabled"] = "Yes" if self_edit_enabled else "No"

    return checks, metrics


def _recalculate_status(report: dict[str, Any]) -> dict[str, Any]:
    checks = list(report.get("checks") or [])
    status_counts = {
        status: sum(1 for row in checks if row.get("Status") == status)
        for status in ("Healthy", "Warning", "Critical", "Info")
    }
    overall = "Critical" if status_counts["Critical"] else (
        "Warning" if status_counts["Warning"] else "Healthy"
    )
    report["checks"] = checks
    report["status_counts"] = status_counts
    report["overall_status"] = overall
    return report


def install_integration_health_guard() -> bool:
    original = getattr(system_health_guard, "build_system_health_report", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    @functools.wraps(original)
    def build_with_integrations(*args: Any, **kwargs: Any) -> dict[str, Any]:
        report = dict(original(*args, **kwargs) or {})
        integration_checks, integration_metrics = integration_health_report()
        report["checks"] = list(report.get("checks") or []) + integration_checks
        report["metrics"] = {**dict(report.get("metrics") or {}), **integration_metrics}
        return _recalculate_status(report)

    setattr(build_with_integrations, PATCH_MARKER, True)
    build_with_integrations._pb_original_build_system_health_report = original
    system_health_guard.build_system_health_report = build_with_integrations
    return True
