"""Bound database waits and survive short PostgreSQL availability gaps.

JobHub and the embedded scheduler create PostgreSQL pools during application
startup. A database restart, stale socket, lock or slow query must not leave the
whole Streamlit app frozen or fail permanently on the first refused socket.

This guard wraps pool factories before they are first used, adds conservative
connection keepalives/timeouts, retries only unmistakable transient connection
failures, and configures each physical connection once with server-side query
and lock timeouts. SQL/programming errors are never retried or hidden.
"""

from __future__ import annotations

import functools
import sys
import threading
import time
from typing import Any, Callable


PATCH_MARKER = "_pb_database_timeout_guard"
POOL_PROXY_MARKER = "_pb_database_timeout_pool_proxy"
CONNECT_TIMEOUT_SECONDS = 8
STATEMENT_TIMEOUT = "12s"
LOCK_TIMEOUT = "3s"
IDLE_TRANSACTION_TIMEOUT = "30s"
# Connection-refused failures usually return immediately. These short retries
# bridge a Render Postgres restart without making a real configuration error
# hang for minutes. A connect timeout still bounds each individual attempt.
POOL_CONNECT_RETRY_DELAYS = (0.0, 1.0, 2.0, 4.0, 7.0)


_TRANSIENT_CONNECTION_MARKERS = (
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
    "terminating connection due to administrator command",
    "the database system is starting up",
    "the database system is shutting down",
    "cannot connect now",
)


def _is_transient_connection_failure(exc: BaseException) -> bool:
    """Identify only connection/availability failures worth retrying.

    Keep this deliberately narrow: syntax errors, auth failures and schema
    failures must surface immediately instead of being disguised as downtime.
    """
    exc_type = type(exc)
    module_name = str(getattr(exc_type, "__module__", "") or "").lower()
    class_name = str(getattr(exc_type, "__name__", "") or "").lower()
    if module_name.startswith("psycopg2") and class_name in {
        "operationalerror",
        "interfaceerror",
    }:
        text = str(exc or "").strip().lower()
        # psycopg2 OperationalError can also represent bad credentials. Do not
        # retry authentication/authorization failures.
        auth_markers = (
            "password authentication failed",
            "no password supplied",
            "authentication failed",
            "permission denied",
            "role does not exist",
            "database does not exist",
        )
        if any(marker in text for marker in auth_markers):
            return False
        return True

    text = str(exc or "").strip().lower()
    return any(marker in text for marker in _TRANSIENT_CONNECTION_MARKERS)


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


def _create_pool_with_retry(
    factory: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    last_error: BaseException | None = None
    for attempt, delay in enumerate(POOL_CONNECT_RETRY_DELAYS, start=1):
        if delay > 0:
            time.sleep(delay)
        try:
            return factory(*args, **kwargs)
        except Exception as exc:
            last_error = exc
            if not _is_transient_connection_failure(exc):
                raise
            if attempt >= len(POOL_CONNECT_RETRY_DELAYS):
                raise
            # Do not print the DSN or exception text: DATABASE_URL can contain
            # credentials. Render logs only need the attempt number/type.
            print(
                "JobHub PostgreSQL pool unavailable; retrying "
                f"({attempt}/{len(POOL_CONNECT_RETRY_DELAYS)}) "
                f"after {POOL_CONNECT_RETRY_DELAYS[attempt]:g}s."
            )
    if last_error is not None:  # pragma: no cover - loop always returns/raises
        raise last_error
    raise RuntimeError("PostgreSQL pool retry loop ended unexpectedly.")


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
        pool = _create_pool_with_retry(factory, args, kwargs)
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
