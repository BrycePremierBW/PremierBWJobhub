from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
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
        self.calls = []
        self.session_state = FakeState()

        def dataframe(*args, **kwargs):
            self.calls.append(kwargs.copy())
            return kwargs

        super().__init__(dataframe=dataframe, session_state=self.session_state)


class PerformanceGuardTests(unittest.TestCase):
    def test_unkeyed_implicit_selection_is_display_only(self):
        st = FakeStreamlit()
        MODULE._install_dataframe_guard(st)
        st.dataframe([], on_select="rerun", selection_mode="single-row")
        self.assertNotIn("on_select", st.calls[-1])
        self.assertNotIn("selection_mode", st.calls[-1])

    def test_explicit_keyed_selection_is_preserved(self):
        st = FakeStreamlit()
        MODULE._install_dataframe_guard(st)
        st.dataframe([], key="jobs", on_select="rerun", selection_mode="single-row")
        self.assertEqual(st.calls[-1]["on_select"], "rerun")
        self.assertEqual(st.calls[-1]["selection_mode"], "single-row")

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


if __name__ == "__main__":
    unittest.main()
