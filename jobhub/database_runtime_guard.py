"""Runtime database optimisation for JobHub.

The core database module historically created a new ``ThreadedConnectionPool``
whenever ``connect()`` called ``get_postgres_pool()``.  On a Streamlit app that
runs many small queries per rerun, that defeats pooling and repeatedly opens and
tears down PostgreSQL sockets.

This guard is installed before the main JobHub application imports its database
helpers.  It makes the existing pool factory timeout-aware and reuses one pool
per process/configuration while preserving the existing connection adapter API.
"""

from __future__ import annotations

import threading
from typing import Any

from . import database_timeout_guard


PATCH_MARKER = "_pb_database_runtime_guard"


def _normalised_maxconn(module: Any) -> int:
    try:
        value = int(module.os.environ.get("JOBHUB_DB_MAXCONN", "5"))
    except Exception:
        value = 5
    return max(1, min(value, 50))


def install_database_runtime_guard(database_module: Any | None = None) -> bool:
    """Reuse one PostgreSQL pool for the life of the server process.

    ``database_module`` is injectable for focused tests.  Production callers
    leave it as ``None`` so the real ``jobhub.database`` module is patched.
    """
    if database_module is None:
        from . import database as database_module

    original_get_pool = getattr(database_module, "get_postgres_pool", None)
    if not callable(original_get_pool) or getattr(original_get_pool, PATCH_MARKER, False):
        return False

    # The timeout guard is installed very early, before jobhub.database is
    # necessarily imported.  Make the database module's eventual pool factory
    # timeout/retry aware here as well so import order cannot bypass it.
    factory = getattr(database_module, "ThreadedConnectionPool", None)
    if callable(factory) and not getattr(factory, database_timeout_guard.PATCH_MARKER, False):
        setattr(database_module, "ThreadedConnectionPool", database_timeout_guard._guard_pool_factory(factory))

    lock = threading.Lock()
    state: dict[str, Any] = {"key": None, "pool": None}

    def cached_get_postgres_pool() -> Any:
        database_url = str(getattr(database_module, "DATABASE_URL", "") or "")
        if not database_url:
            return None

        key = (database_url, _normalised_maxconn(database_module))
        pool = state.get("pool")
        if pool is not None and state.get("key") == key:
            return pool

        with lock:
            pool = state.get("pool")
            if pool is not None and state.get("key") == key:
                return pool

            new_pool = original_get_pool()
            old_pool = state.get("pool")
            state["pool"] = new_pool
            state["key"] = key

            if old_pool is not None and old_pool is not new_pool:
                try:
                    old_pool.closeall()
                except Exception:
                    pass
            return new_pool

    setattr(cached_get_postgres_pool, PATCH_MARKER, True)
    cached_get_postgres_pool._pb_original_get_postgres_pool = original_get_pool
    cached_get_postgres_pool._pb_pool_state = state
    database_module.get_postgres_pool = cached_get_postgres_pool
    return True
