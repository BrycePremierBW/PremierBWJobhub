"""Bound database waits so a slow query cannot freeze the whole JobHub page.

JobHub and the embedded scheduler create PostgreSQL pools during application
startup.  A database lock, stale socket or unexpectedly expensive query could
previously leave Streamlit waiting without a useful error.  This guard wraps the
pool factories before they are first used, adds conservative connection
keepalives, and configures each physical connection once with server-side query
and lock timeouts.
"""

from __future__ import annotations

import functools
import sys
import threading
from typing import Any, Callable


PATCH_MARKER = "_pb_database_timeout_guard"
POOL_PROXY_MARKER = "_pb_database_timeout_pool_proxy"
CONNECT_TIMEOUT_SECONDS = 8
STATEMENT_TIMEOUT = "12s"
LOCK_TIMEOUT = "3s"
IDLE_TRANSACTION_TIMEOUT = "30s"


class _TimeoutPoolProxy:
    """Delegate to a psycopg2 pool while configuring each connection once."""

    def __init__(self, pool: Any):
        self._pool = pool
        self._configured_connection_ids: set[int] = set()
        self._configuration_lock = threading.Lock()
        setattr(self, POOL_PROXY_MARKER, True)

    def _configure(self, connection: Any) -> None:
        connection_id = id(connection)
        with self._configuration_lock:
            if connection_id in self._configured_connection_ids:
                return

        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'")
            cursor.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
            cursor.execute(
                f"SET idle_in_transaction_session_timeout = '{IDLE_TRANSACTION_TIMEOUT}'"
            )
            connection.commit()
        except Exception:
            try:
                connection.rollback()
            except Exception:
                pass
            try:
                self._pool.putconn(connection, close=True)
            except Exception:
                pass
            raise
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass

        with self._configuration_lock:
            self._configured_connection_ids.add(connection_id)

    def getconn(self, *args: Any, **kwargs: Any) -> Any:
        connection = self._pool.getconn(*args, **kwargs)
        self._configure(connection)
        return connection

    def putconn(self, connection: Any, *args: Any, **kwargs: Any) -> Any:
        close_requested = bool(kwargs.get("close", False))
        if len(args) >= 2:
            close_requested = close_requested or bool(args[1])
        if close_requested or bool(getattr(connection, "closed", False)):
            with self._configuration_lock:
                self._configured_connection_ids.discard(id(connection))
        return self._pool.putconn(connection, *args, **kwargs)

    def closeall(self) -> Any:
        with self._configuration_lock:
            self._configured_connection_ids.clear()
        return self._pool.closeall()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._pool, name)


def _guard_pool_factory(factory: Callable[..., Any]) -> Callable[..., Any]:
    if getattr(factory, PATCH_MARKER, False):
        return factory

    @functools.wraps(factory)
    def guarded_factory(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("connect_timeout", CONNECT_TIMEOUT_SECONDS)
        kwargs.setdefault("keepalives", 1)
        kwargs.setdefault("keepalives_idle", 10)
        kwargs.setdefault("keepalives_interval", 5)
        kwargs.setdefault("keepalives_count", 2)
        pool = factory(*args, **kwargs)
        if getattr(pool, POOL_PROXY_MARKER, False):
            return pool
        return _TimeoutPoolProxy(pool)

    setattr(guarded_factory, PATCH_MARKER, True)
    guarded_factory._pb_original_pool_factory = factory
    return guarded_factory


def _candidate_modules() -> list[Any]:
    candidates: list[Any] = []
    seen: set[int] = set()
    for module in tuple(sys.modules.values()):
        if module is None or id(module) in seen:
            continue
        if not hasattr(module, "ThreadedConnectionPool"):
            continue
        seen.add(id(module))
        candidates.append(module)
    return candidates


def install_database_timeout_guard() -> bool:
    """Patch every already-loaded PostgreSQL pool factory used by JobHub."""
    installed = False
    for module in _candidate_modules():
        factory = getattr(module, "ThreadedConnectionPool", None)
        if not callable(factory) or getattr(factory, PATCH_MARKER, False):
            continue
        try:
            setattr(module, "ThreadedConnectionPool", _guard_pool_factory(factory))
            installed = True
        except (AttributeError, TypeError):
            continue
    return installed
