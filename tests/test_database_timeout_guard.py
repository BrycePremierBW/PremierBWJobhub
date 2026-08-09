from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "jobhub_database_timeout_guard_test",
    ROOT / "jobhub" / "database_timeout_guard.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class FakeCursor:
    def __init__(self, fail=False, error_message="configuration failed"):
        self.fail = fail
        self.error_message = error_message
        self.statements = []
        self.closed = False

    def execute(self, statement):
        if self.fail:
            raise RuntimeError(self.error_message)
        self.statements.append(statement)

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, fail=False, error_message="configuration failed"):
        self.cursor_instance = FakeCursor(fail=fail, error_message=error_message)
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class FakePool:
    def __init__(self, connection=None):
        self.connection = connection or FakeConnection()
        self.put_calls = []
        self.closed_all = False

    def getconn(self, *args, **kwargs):
        return self.connection

    def putconn(self, connection, *args, **kwargs):
        self.put_calls.append((connection, args, kwargs))

    def closeall(self):
        self.closed_all = True


class SequencePool(FakePool):
    def __init__(self, connections):
        super().__init__(connections[0])
        self.connections = list(connections)

    def getconn(self, *args, **kwargs):
        if not self.connections:
            raise RuntimeError("pool exhausted")
        return self.connections.pop(0)


class DatabaseTimeoutGuardTests(unittest.TestCase):
    def test_factory_adds_connection_timeouts_and_keepalives(self):
        captured = {}
        pool = FakePool()

        def factory(*args, **kwargs):
            captured.update(kwargs)
            return pool

        guarded = MODULE._guard_pool_factory(factory)
        result = guarded(minconn=1, maxconn=8, dsn="postgresql://example")

        self.assertTrue(getattr(result, MODULE.POOL_PROXY_MARKER, False))
        self.assertEqual(captured["connect_timeout"], MODULE.CONNECT_TIMEOUT_SECONDS)
        self.assertEqual(captured["keepalives"], 1)
        self.assertEqual(captured["keepalives_idle"], 10)
        self.assertEqual(captured["keepalives_interval"], 5)
        self.assertEqual(captured["keepalives_count"], 2)

    def test_factory_retries_connection_refused_then_recovers(self):
        calls = {"count": 0}
        pool = FakePool()

        def factory(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] < 3:
                raise RuntimeError("connection refused while opening PostgreSQL")
            return pool

        with patch.object(MODULE, "POOL_CONNECT_RETRY_DELAYS", (0.0, 0.0, 0.0)):
            guarded = MODULE._guard_pool_factory(factory)
            result = guarded(minconn=1, maxconn=8, dsn="postgresql://example")

        self.assertTrue(getattr(result, MODULE.POOL_PROXY_MARKER, False))
        self.assertEqual(calls["count"], 3)

    def test_factory_does_not_retry_sql_programming_failure(self):
        calls = {"count": 0}

        def factory(*args, **kwargs):
            calls["count"] += 1
            raise RuntimeError("syntax error at or near CREATE")

        with patch.object(MODULE, "POOL_CONNECT_RETRY_DELAYS", (0.0, 0.0, 0.0)):
            guarded = MODULE._guard_pool_factory(factory)
            with self.assertRaisesRegex(RuntimeError, "syntax error"):
                guarded(minconn=1, maxconn=8, dsn="postgresql://example")

        self.assertEqual(calls["count"], 1)

    def test_authentication_failure_is_not_classified_by_message_fallback(self):
        self.assertFalse(
            MODULE._is_transient_connection_failure(
                RuntimeError("password authentication failed for user postgres")
            )
        )

    def test_connection_is_configured_once_and_reused_connection_is_probed(self):
        connection = FakeConnection()
        proxy = MODULE._TimeoutPoolProxy(FakePool(connection))

        self.assertIs(proxy.getconn(), connection)
        self.assertIs(proxy.getconn(), connection)

        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(
            connection.cursor_instance.statements,
            [
                f"SET statement_timeout = '{MODULE.STATEMENT_TIMEOUT}'",
                f"SET lock_timeout = '{MODULE.LOCK_TIMEOUT}'",
                (
                    "SET idle_in_transaction_session_timeout = "
                    f"'{MODULE.IDLE_TRANSACTION_TIMEOUT}'"
                ),
                "SELECT 1",
            ],
        )
        self.assertTrue(connection.cursor_instance.closed)

    def test_stale_reused_connection_is_discarded_and_replaced(self):
        stale = FakeConnection(
            fail=True,
            error_message="connection refused while probing PostgreSQL",
        )
        fresh = FakeConnection()
        pool = SequencePool([stale, fresh])
        proxy = MODULE._TimeoutPoolProxy(pool)
        # Simulate a physical connection that was configured before Render
        # restarted PostgreSQL and is now a stale cached socket.
        proxy._configured_connection_ids.add(id(stale))

        with patch.object(MODULE, "POOL_CONNECT_RETRY_DELAYS", (0.0, 0.0)):
            result = proxy.getconn()

        self.assertIs(result, fresh)
        self.assertEqual(len(pool.put_calls), 1)
        self.assertIs(pool.put_calls[0][0], stale)
        self.assertTrue(pool.put_calls[0][2].get("close"))
        self.assertNotIn(id(stale), proxy._configured_connection_ids)
        self.assertIn(id(fresh), proxy._configured_connection_ids)
        self.assertEqual(fresh.commits, 1)

    def test_failed_configuration_discards_the_connection(self):
        connection = FakeConnection(fail=True)
        pool = FakePool(connection)
        proxy = MODULE._TimeoutPoolProxy(pool)

        with self.assertRaises(RuntimeError):
            proxy.getconn()

        self.assertGreaterEqual(connection.rollbacks, 1)
        self.assertEqual(len(pool.put_calls), 1)
        self.assertTrue(pool.put_calls[0][2].get("close"))

    def test_guard_is_installed_before_main_app_runs(self):
        source = (ROOT / "jobhub" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn(
            "from .database_timeout_guard import install_database_timeout_guard",
            source,
        )
        self.assertIn("install_database_timeout_guard()", source)


if __name__ == "__main__":
    unittest.main()
