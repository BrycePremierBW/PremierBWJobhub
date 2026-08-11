"""Keep transient PostgreSQL outages from crashing JobHub during import.

The Setup/Scheduler crew bridge historically created its compatibility tables as
soon as the ``jobhub`` package was imported.  Creating the scheduler connection
pool is therefore part of application startup.  If Render PostgreSQL is
restarting or temporarily refuses a socket, that one optional schema check can
prevent the entire Streamlit application from reaching its UI.

This guard changes only that startup schema call.  PostgreSQL connection-level
errors are deferred and retried the next time the bridge needs the schema.  SQL
programming errors and non-connectivity failures still raise normally.
"""

from __future__ import annotations

from datetime import datetime
from jobhub_time import jobhub_now
from typing import Any, Callable

from . import setup_scheduler_crew_bridge_guard as bridge_guard

try:
    import psycopg2
except Exception:  # pragma: no cover - SQLite/local-only environments
    psycopg2 = None  # type: ignore[assignment]


PATCH_MARKER = "_pb_setup_scheduler_startup_resilience_guard"
LAST_ERROR_ATTR = "_pb_setup_scheduler_schema_deferred_error"
LAST_ERROR_AT_ATTR = "_pb_setup_scheduler_schema_deferred_at"


def _scheduler_uses_postgres() -> bool:
    scheduler = bridge_guard._scheduler_module()
    return bool(scheduler is not None and getattr(scheduler, "USE_POSTGRES", False))


def _is_connection_failure(exc: BaseException) -> bool:
    """Return True only for errors that represent a broken/unavailable DB link."""
    if not _scheduler_uses_postgres():
        return False

    if psycopg2 is not None:
        operational = getattr(psycopg2, "OperationalError", ())
        interface = getattr(psycopg2, "InterfaceError", ())
        error_types = tuple(
            item for item in (operational, interface) if isinstance(item, type)
        )
        if error_types and isinstance(exc, error_types):
            return True

    # Some tests/wrappers re-raise driver failures as RuntimeError while keeping
    # the original connection message.  Restrict the fallback to unmistakable
    # connectivity wording so SQL/schema errors still surface.
    text = str(exc or "").strip().lower()
    markers = (
        "connection refused",
        "could not connect to server",
        "connection timed out",
        "connection timeout",
        "server closed the connection unexpectedly",
        "connection already closed",
        "network is unreachable",
        "name or service not known",
        "temporary failure in name resolution",
        "could not translate host name",
        "ssl connection has been closed unexpectedly",
    )
    return any(marker in text for marker in markers)


def install_setup_scheduler_startup_resilience_guard() -> bool:
    """Patch the crew-bridge schema initializer before its installer runs."""
    original: Callable[[Any], Any] | None = getattr(
        bridge_guard, "_ensure_setup_schema", None
    )
    if not callable(original) or getattr(original, PATCH_MARKER, False):
        return False

    def failsoft_ensure_setup_schema(scheduler: Any) -> Any:
        try:
            result = original(scheduler)
        except Exception as exc:
            if not _is_connection_failure(exc):
                raise
            setattr(bridge_guard, LAST_ERROR_ATTR, str(exc))
            setattr(
                bridge_guard,
                LAST_ERROR_AT_ATTR,
                jobhub_now().isoformat(timespec="seconds"),
            )
            # The bridge installer can continue patching its runtime helpers.
            # A later read/save calls this initializer again and therefore
            # retries the schema creation automatically once Postgres returns.
            return None
        else:
            if hasattr(bridge_guard, LAST_ERROR_ATTR):
                setattr(bridge_guard, LAST_ERROR_ATTR, "")
            if hasattr(bridge_guard, LAST_ERROR_AT_ATTR):
                setattr(bridge_guard, LAST_ERROR_AT_ATTR, "")
            return result

    setattr(failsoft_ensure_setup_schema, PATCH_MARKER, True)
    setattr(failsoft_ensure_setup_schema, "_pb_original_ensure_setup_schema", original)
    bridge_guard._ensure_setup_schema = failsoft_ensure_setup_schema
    return True
