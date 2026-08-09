from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from jobhub import setup_scheduler_crew_bridge_guard as bridge_guard
from jobhub import setup_scheduler_startup_resilience_guard as resilience


class SetupSchedulerStartupResilienceGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_ensure = bridge_guard._ensure_setup_schema
        self.original_scheduler_module = bridge_guard._scheduler_module

    def tearDown(self) -> None:
        bridge_guard._ensure_setup_schema = self.original_ensure
        bridge_guard._scheduler_module = self.original_scheduler_module

    def test_connection_refused_is_deferred_and_retried(self) -> None:
        calls = {"count": 0}

        def transient_schema(_scheduler):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("connection refused while opening PostgreSQL")
            return "ready"

        scheduler = SimpleNamespace(USE_POSTGRES=True)
        bridge_guard._scheduler_module = lambda: scheduler
        bridge_guard._ensure_setup_schema = transient_schema

        self.assertTrue(resilience.install_setup_scheduler_startup_resilience_guard())
        wrapped = bridge_guard._ensure_setup_schema

        self.assertIsNone(wrapped(scheduler))
        self.assertIn("connection refused", getattr(bridge_guard, resilience.LAST_ERROR_ATTR))
        self.assertEqual(wrapped(scheduler), "ready")
        self.assertEqual(getattr(bridge_guard, resilience.LAST_ERROR_ATTR), "")
        self.assertEqual(calls["count"], 2)

    def test_sql_programming_error_is_not_hidden(self) -> None:
        def bad_schema(_scheduler):
            raise RuntimeError("syntax error at or near CREATE")

        scheduler = SimpleNamespace(USE_POSTGRES=True)
        bridge_guard._scheduler_module = lambda: scheduler
        bridge_guard._ensure_setup_schema = bad_schema

        self.assertTrue(resilience.install_setup_scheduler_startup_resilience_guard())
        with self.assertRaisesRegex(RuntimeError, "syntax error"):
            bridge_guard._ensure_setup_schema(scheduler)

    def test_sqlite_failure_is_not_reclassified_as_postgres_outage(self) -> None:
        scheduler = SimpleNamespace(USE_POSTGRES=False)
        bridge_guard._scheduler_module = lambda: scheduler
        self.assertFalse(
            resilience._is_connection_failure(
                RuntimeError("connection refused while opening PostgreSQL")
            )
        )


if __name__ == "__main__":
    unittest.main()
