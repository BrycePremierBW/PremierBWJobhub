"""Keep JobHub startup recoverable across short PostgreSQL outages.

Render Postgres can briefly become unavailable during maintenance or instance
recovery.  JobHub's individual pool connections already retry, but the complete
startup bootstrap contains several idempotent schema/seed stages.  A connection
that drops between those stages must restart the bootstrap instead of taking the
whole Streamlit session down on the first failed query.

This guard is deliberately narrow.  It only wraps the cached
``initialise_jobhub_runtime`` function, retries errors already classified as
transient database connectivity failures, and clears cached PostgreSQL pools
between attempts. Authentication, SQL, schema and programming errors still fail
immediately.
"""

from __future__ import annotations

import functools
import sys
import time
from pathlib import Path
from typing import Any, Callable

import streamlit as st

from .database_timeout_guard import _is_transient_connection_failure


PATCH_MARKER = "_pb_startup_database_resilience_guard"
WRAP_MARKER = "_pb_resilient_jobhub_startup"
TARGET_FUNCTION_NAME = "initialise_jobhub_runtime"

# The lower-level pool guard already performs short retries for each physical
# connection. These wider gaps cover the case where PostgreSQL disappears in the
# middle of an idempotent migration/seed sequence and comes back shortly after.
STARTUP_RETRY_DELAYS = (0.0, 2.0, 5.0, 10.0, 20.0, 30.0, 45.0)
DATABASE_CACHE_FUNCTION_NAMES = (
    "get_postgres_pool",
    "scheduler_postgres_pool",
)


def _safe_failure_location(exc: BaseException) -> str:
    """Return only source/function metadata, never exception text or credentials."""
    tb = getattr(exc, "__traceback__", None)
    if tb is None:
        return "unknown"
    while tb.tb_next is not None:
        tb = tb.tb_next
    try:
        filename = Path(tb.tb_frame.f_code.co_filename).name
        function_name = tb.tb_frame.f_code.co_name
        return f"{filename}:{function_name}"
    except Exception:
        return "unknown"


def _clear_database_resource_caches() -> int:
    """Clear cached pool factories so the next attempt builds fresh connections."""
    cleared = 0
    seen: set[int] = set()
    for module in tuple(sys.modules.values()):
        if module is None:
            continue
        for name in DATABASE_CACHE_FUNCTION_NAMES:
            candidate = getattr(module, name, None)
            if candidate is None or id(candidate) in seen:
                continue
            seen.add(id(candidate))
            clear = getattr(candidate, "clear", None)
            if not callable(clear):
                continue
            try:
                clear()
                cleared += 1
            except Exception:
                # Cache cleanup is best-effort. The next startup attempt still
                # gets a chance to create a fresh connection through the pool
                # guard, and cleanup errors must not mask the original outage.
                continue
    return cleared


def _render_database_reconnecting_state() -> None:
    """Show a safe maintenance state instead of the old fatal startup error."""
    st.error("JobHub is temporarily waiting for its PostgreSQL database.")
    st.info(
        "The database connection dropped during startup. JobHub did not switch "
        "to a fallback database and no cleanup was attempted. Refresh this page "
        "once the Render database shows Available."
    )
    st.stop()


def _wrap_startup_function(function: Callable[..., Any]) -> Callable[..., Any]:
    if getattr(function, WRAP_MARKER, False):
        return function

    @functools.wraps(function)
    def resilient_startup(*args: Any, **kwargs: Any) -> Any:
        last_error: BaseException | None = None
        for attempt, delay in enumerate(STARTUP_RETRY_DELAYS, start=1):
            if delay > 0:
                time.sleep(delay)
            try:
                return function(*args, **kwargs)
            except Exception as exc:
                last_error = exc
                if not _is_transient_connection_failure(exc):
                    raise

                _clear_database_resource_caches()
                location = _safe_failure_location(exc)
                if attempt >= len(STARTUP_RETRY_DELAYS):
                    print(
                        "JobHub startup database reconnect attempts exhausted "
                        f"after transient failure at {location}."
                    )
                    _render_database_reconnecting_state()
                    return None  # pragma: no cover - st.stop does not return

                next_delay = STARTUP_RETRY_DELAYS[attempt]
                print(
                    "JobHub startup database connection dropped; restarting "
                    f"bootstrap ({attempt}/{len(STARTUP_RETRY_DELAYS)}) after "
                    f"{next_delay:g}s; failure location={location}."
                )

        if last_error is not None:  # pragma: no cover - loop returns/stops/raises
            raise last_error
        raise RuntimeError("JobHub startup retry loop ended unexpectedly.")

    setattr(resilient_startup, WRAP_MARKER, True)
    return resilient_startup


def _guard_cache_resource(original_cache_resource: Callable[..., Any]) -> Callable[..., Any]:
    """Intercept only the JobHub runtime cache decorator; delegate everything else."""
    if getattr(original_cache_resource, PATCH_MARKER, False):
        return original_cache_resource

    @functools.wraps(original_cache_resource)
    def guarded_cache_resource(function: Any = None, *args: Any, **kwargs: Any) -> Any:
        # Bare form: @st.cache_resource
        if callable(function):
            target = (
                _wrap_startup_function(function)
                if getattr(function, "__name__", "") == TARGET_FUNCTION_NAME
                else function
            )
            return original_cache_resource(target, *args, **kwargs)

        # Configured form: @st.cache_resource(...)
        if function is None:
            decorator = original_cache_resource(*args, **kwargs)
        else:
            decorator = original_cache_resource(function, *args, **kwargs)

        @functools.wraps(decorator)
        def apply_guard(target: Callable[..., Any]) -> Any:
            wrapped_target = (
                _wrap_startup_function(target)
                if getattr(target, "__name__", "") == TARGET_FUNCTION_NAME
                else target
            )
            return decorator(wrapped_target)

        return apply_guard

    setattr(guarded_cache_resource, PATCH_MARKER, True)
    guarded_cache_resource._pb_original_cache_resource = original_cache_resource
    # Preserve Streamlit's global cache clear API for existing callers.
    clear = getattr(original_cache_resource, "clear", None)
    if clear is not None:
        guarded_cache_resource.clear = clear
    return guarded_cache_resource


def install_startup_database_resilience_guard() -> bool:
    """Install the targeted startup retry wrapper before the main app is defined."""
    current = getattr(st, "cache_resource", None)
    if not callable(current) or getattr(current, PATCH_MARKER, False):
        return False
    st.cache_resource = _guard_cache_resource(current)
    return True
