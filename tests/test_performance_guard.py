from __future__ import annotations

import importlib.util
from pathlib import Path
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "jobhub_performance_guard_test",
    ROOT / "jobhub" / "performance_guard.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class FakeState(dict):
    pass


class FakeStreamlit(types.SimpleNamespace):
    def __init__(self):
        super().__init__(session_state=FakeState())


class PerformanceGuardTests(unittest.TestCase):
    def test_sync_is_throttled_without_losing_first_run(self):
        st = FakeStreamlit()
        calls = []

        def sync():
            calls.append(1)
            return 7

        wrapped = MODULE._throttled_sync(st, "example", sync)
        self.assertEqual(wrapped(), 7)
        self.assertEqual(wrapped(), 0)
        self.assertEqual(len(calls), 1)

    def test_failed_sync_is_not_marked_as_completed(self):
        st = FakeStreamlit()
        calls = []

        def sync():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("temporary")
            return 3

        wrapped = MODULE._throttled_sync(st, "retry", sync)
        with self.assertRaises(RuntimeError):
            wrapped()
        self.assertEqual(wrapped(), 3)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
