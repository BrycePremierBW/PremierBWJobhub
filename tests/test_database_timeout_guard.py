from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "jobhub_database_timeout_guard_test",
    ROOT / "jobhub" / "database_timeout_guard.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class FakeCursor:
    def __init__(self, fail=False):
        self.fail = fail
        self.statements = []
        self.closed = False

    def execute(self, statement):
        if self.fail:
            raise RuntimeError("configuration failed")
        self.statements.append(statement)

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, fail=False):
        self.cursor_instance = FakeCursor(fail=fail)
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


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

    def test_connection_is_configured_once_with_server_side_limits(self):
        connection = FakeConnection()
        proxy = MODULE._TimeoutPoolProxy(FakePool(connection))

        self.assertIs(proxy.getconn(), connection)
        self.assertIs(proxy.getconn(), connection)

        self.assertEqual(connection.commits, 1)
        self.assertEqual(
            connection.cursor_instance.statements,
            [
                f"SET statement_timeout = '{MODULE.STATEMENT_TIMEOUT}'",
                f"SET lock_timeout = '{MODULE.LOCK_TIMEOUT}'",
                (
                    "SET idle_in_transaction_session_timeout = "
                    f"'{MODULE.IDLE_TRANSACTION_TIMEOUT}'"
                ),
            ],
        )
        self.assertTrue(connection.cursor_instance.closed)

    def test_failed_configuration_discards_the_connection(self):
        connection = FakeConnection(fail=True)
        pool = FakePool(connection)
        proxy = MODULE._TimeoutPoolProxy(pool)

        with self.assertRaises(RuntimeError):
            proxy.getconn()

        self.assertEqual(connection.rollbacks, 1)
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
