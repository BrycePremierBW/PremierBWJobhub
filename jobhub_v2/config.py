"""Runtime configuration shared by JobHub and its V2 services."""

from __future__ import annotations

from dataclasses import dataclass
import os


TRUE_VALUES = {"1", "true", "yes", "on"}


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in TRUE_VALUES


@dataclass(frozen=True)
class RuntimeConfig:
    environment: str
    database_url: str
    jobhub_url: str
    offline_sync_enabled: bool
    email_notifications_enabled: bool
    email_provider: str
    email_from: str

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.environment not in {"development", "staging", "production", "test"}:
            errors.append("JOBHUB_ENV must be development, staging, production or test.")
        if self.environment in {"staging", "production"} and not self.database_url:
            errors.append("DATABASE_URL is required outside development/test.")
        if self.email_notifications_enabled and not self.email_provider:
            errors.append("JOBHUB_EMAIL_PROVIDER is required when email is enabled.")
        if self.email_notifications_enabled and not self.email_from:
            errors.append("JOBHUB_EMAIL_FROM is required when email is enabled.")
        return errors


def load_runtime_config(environ: dict[str, str] | None = None) -> RuntimeConfig:
    values = os.environ if environ is None else environ
    return RuntimeConfig(
        environment=values.get("JOBHUB_ENV", "development").strip().lower(),
        database_url=values.get("DATABASE_URL", "").strip(),
        jobhub_url=values.get("JOBHUB_URL", "").strip().rstrip("/"),
        offline_sync_enabled=_as_bool(values.get("JOBHUB_OFFLINE_SYNC_ENABLED")),
        email_notifications_enabled=_as_bool(
            values.get("JOBHUB_EMAIL_NOTIFICATIONS_ENABLED")
        ),
        email_provider=values.get("JOBHUB_EMAIL_PROVIDER", "").strip().lower(),
        email_from=values.get("JOBHUB_EMAIL_FROM", "").strip(),
    )
