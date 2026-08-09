from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "jobhub_startup_guard_testpkg"

# Load the two guard modules without executing jobhub/__init__.py and all of its
# production monkeypatch installers.
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(ROOT / "jobhub")]
sys.modules[PACKAGE_NAME] = package

DB_SPEC = importlib.util.spec_from_file_location(
    f"{PACKAGE_NAME}.database_timeout_guard",
    ROOT / "jobhub" / "database_timeout_guard.py",
)
DB_MODULE = importlib.util.module_from_spec(DB_SPEC)
assert DB_SPEC and DB_SPEC.loader
sys.modules[DB_SPEC.name] = DB_MODULE
DB_SPEC.loader.exec_module(DB_MODULE)

SPEC = importlib.util.spec_from_file_location(
    f"{PACKAGE_NAME}.startup_database_resilience_guard",
    ROOT / "jobhub" / "startup_database_resilience_guard.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class StopSignal(BaseException):
    pass


class FakeStreamlit:
    def __init__(self):
        self.errors = []
        self.infos = []

    def error(self, value):
        self.errors.append(value)

    def info(self, value):
        self.infos.append(value)

    def stop(self):
        raise StopSignal()


class FakeCachedFunction:
    def __init__(self, function):
        self.function = function
        self.clear_calls = 0

    def __call__(self, *args, **kwargs):
        return self.function(*args, **kwargs)

    def clear(self):
        self.clear_calls += 1


class FakeCacheResource:
    def __init__(self):
        self.global_clear_calls = 0

    def __call__(self, function=None, *args, **kwargs):
        if callable(function):
            return FakeCachedFunction(function)

        def decorate(target):
            return FakeCachedFunction(target)

        return decorate

    def clear(self):
        self.global_clear_calls += 1


class StartupDatabaseResilienceGuardTests(unittest.TestCase):
    def test_transient_startup_failure_retries_complete_bootstrap(self):
        calls = {"count": 0}

        def initialise_jobhub_runtime(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] < 3:
                raise RuntimeError("connection refused while running startup")
            return "ready"

        with (
            patch.object(MODULE, "STARTUP_RETRY_DELAYS", (0.0, 0.0, 0.0)),
            patch.object(MODULE, "_clear_database_resource_caches", return_value=2) as clear,
        ):
            wrapped = MODULE._wrap_startup_function(initialise_jobhub_runtime)
            self.assertEqual(wrapped(), "ready")

        self.assertEqual(calls["count"], 3)
        self.assertEqual(clear.call_count, 2)

    def test_non_transient_startup_failure_is_not_retried(self):
        calls = {"count": 0}

        def initialise_jobhub_runtime():
            calls["count"] += 1
            raise RuntimeError("syntax error at or near CREATE")

        with (
            patch.object(MODULE, "STARTUP_RETRY_DELAYS", (0.0, 0.0, 0.0)),
            patch.object(MODULE, "_clear_database_resource_caches") as clear,
        ):
            wrapped = MODULE._wrap_startup_function(initialise_jobhub_runtime)
            with self.assertRaisesRegex(RuntimeError, "syntax error"):
                wrapped()

        self.assertEqual(calls["count"], 1)
        clear.assert_not_called()

    def test_exhausted_transient_outage_shows_recoverable_state_and_stops(self):
        fake_st = FakeStreamlit()

        def initialise_jobhub_runtime():
            raise RuntimeError("connection refused while running startup")

        with (
            patch.object(MODULE, "STARTUP_RETRY_DELAYS", (0.0, 0.0)),
            patch.object(MODULE, "_clear_database_resource_caches", return_value=1),
            patch.object(MODULE, "st", fake_st),
        ):
            wrapped = MODULE._wrap_startup_function(initialise_jobhub_runtime)
            with self.assertRaises(StopSignal):
                wrapped()

        self.assertEqual(len(fake_st.errors), 1)
        self.assertIn("temporarily waiting", fake_st.errors[0])
        self.assertEqual(len(fake_st.infos), 1)
        self.assertIn("did not switch", fake_st.infos[0])

    def test_cache_resource_guard_wraps_only_named_startup_function(self):
        original = FakeCacheResource()
        guarded = MODULE._guard_cache_resource(original)

        @guarded(show_spinner=False)
        def initialise_jobhub_runtime():
            return "ready"

        @guarded(show_spinner=False)
        def some_other_cached_resource():
            return "other"

        self.assertTrue(
            getattr(initialise_jobhub_runtime.function, MODULE.WRAP_MARKER, False)
        )
        self.assertFalse(
            getattr(some_other_cached_resource.function, MODULE.WRAP_MARKER, False)
        )
        self.assertIs(guarded.clear, original.clear)

    def test_database_cache_clear_targets_main_and_scheduler_pool_factories(self):
        fake_module_name = "pb_startup_guard_fake_module"
        fake_module = types.ModuleType(fake_module_name)
        main_pool = FakeCachedFunction(lambda: None)
        scheduler_pool = FakeCachedFunction(lambda: None)
        fake_module.get_postgres_pool = main_pool
        fake_module.scheduler_postgres_pool = scheduler_pool
        sys.modules[fake_module_name] = fake_module
        try:
            cleared = MODULE._clear_database_resource_caches()
        finally:
            sys.modules.pop(fake_module_name, None)

        self.assertGreaterEqual(cleared, 2)
        self.assertEqual(main_pool.clear_calls, 1)
        self.assertEqual(scheduler_pool.clear_calls, 1)

    def test_guard_installed_after_pool_guard_and_before_main_app_runs(self):
        source = (ROOT / "jobhub" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn(
            "from .startup_database_resilience_guard import "
            "install_startup_database_resilience_guard",
            source,
        )
        pool_index = source.index("install_database_timeout_guard()")
        startup_index = source.index("install_startup_database_resilience_guard()")
        runtime_index = source.index("install_runtime_performance_guard()")
        self.assertLess(pool_index, startup_index)
        self.assertLess(startup_index, runtime_index)


if __name__ == "__main__":
    unittest.main()
