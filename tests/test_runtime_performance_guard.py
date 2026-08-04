from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "jobhub_runtime_performance_guard_test",
    ROOT / "jobhub" / "runtime_performance_guard.py",
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


class RuntimePerformanceGuardTests(unittest.TestCase):
    def test_unkeyed_legacy_dataframe_becomes_display_only(self):
        st = FakeStreamlit()
        MODULE._install_dataframe_guard(st)
        guarded = st.dataframe

        def legacy_selectable_wrapper(*args, **kwargs):
            if "on_select" not in kwargs:
                kwargs["on_select"] = "rerun"
                kwargs["selection_mode"] = "single-row"
            return guarded(*args, **kwargs)

        st.dataframe = legacy_selectable_wrapper
        st.dataframe([])
        self.assertEqual(st.calls[-1].get("on_select"), "ignore")
        self.assertNotIn("selection_mode", st.calls[-1])

    def test_keyed_editable_dataframe_keeps_selection(self):
        st = FakeStreamlit()
        MODULE._install_dataframe_guard(st)
        guarded = st.dataframe

        def legacy_selectable_wrapper(*args, **kwargs):
            if "on_select" not in kwargs:
                kwargs["on_select"] = "rerun"
                kwargs["selection_mode"] = "single-row"
            return guarded(*args, **kwargs)

        st.dataframe = legacy_selectable_wrapper
        st.dataframe([], key="jobs_table")
        self.assertEqual(st.calls[-1].get("on_select"), "rerun")
        self.assertEqual(st.calls[-1].get("selection_mode"), "single-row")

    def test_scheduler_sync_runs_only_when_links_are_dirty(self):
        calls = []
        scheduler = types.SimpleNamespace(scalar=lambda *args, **kwargs: 0)
        previous = sys.modules.get("pb_jobhub_visual_scheduler")
        sys.modules["pb_jobhub_visual_scheduler"] = scheduler
        try:
            wrapped = MODULE._wrap_scheduler_sync(lambda: calls.append(1) or 4)
            self.assertEqual(wrapped(), 0)
            self.assertEqual(calls, [])
            scheduler.scalar = lambda *args, **kwargs: 1
            self.assertEqual(wrapped(), 4)
            self.assertEqual(calls, [1])
        finally:
            if previous is None:
                sys.modules.pop("pb_jobhub_visual_scheduler", None)
            else:
                sys.modules["pb_jobhub_visual_scheduler"] = previous

    def test_progress_sync_skips_unchanged_source_signature(self):
        st = FakeStreamlit()
        calls = []
        frame = pd.DataFrame(
            [{
                "linked_jobs": 1,
                "line_count": 2,
                "max_line_id": 10,
                "latest_estimate_update": "2026-08-05T01:00:00",
                "total_qty": 100.0,
                "total_value": 5000.0,
                "text_size": 80,
            }]
        )
        context = {"df_query": lambda sql: frame}
        wrapped = MODULE._wrap_progress_sync(st, lambda ctx: calls.append(1) or 3)
        self.assertEqual(wrapped(context), 3)
        self.assertEqual(wrapped(context), 0)
        self.assertEqual(calls, [1])

        frame.loc[0, "total_qty"] = 101.0
        self.assertEqual(wrapped(context), 3)
        self.assertEqual(calls, [1, 1])


if __name__ == "__main__":
    unittest.main()
