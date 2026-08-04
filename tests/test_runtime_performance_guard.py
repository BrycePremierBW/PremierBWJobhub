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


def legacy_selectable_wrapper(function):
    def wrapper(*args, **kwargs):
        if "on_select" not in kwargs:
            kwargs["on_select"] = "rerun"
            kwargs["selection_mode"] = "single-row"
        return function(*args, **kwargs)

    wrapper._pb_selectable_wrapper = True
    wrapper._pb_original_dataframe = function
    return wrapper


class RuntimePerformanceGuardTests(unittest.TestCase):
    def test_unkeyed_legacy_dataframe_becomes_display_only(self):
        st = FakeStreamlit()
        MODULE._install_dataframe_guard(st)
        guarded = st.dataframe
        st.dataframe = legacy_selectable_wrapper(guarded)
        st.dataframe([])
        self.assertEqual(st.calls[-1].get("on_select"), "ignore")
        self.assertNotIn("selection_mode", st.calls[-1])

    def test_protected_wrapper_makes_keyed_report_display_only(self):
        calls = []

        def base_dataframe(*args, **kwargs):
            calls.append(kwargs.copy())
            return kwargs

        protected = MODULE._protect_legacy_dataframe_wrapper(
            legacy_selectable_wrapper(base_dataframe)
        )
        protected([], key="job_dashboard_timesheet_details")
        self.assertEqual(calls[-1].get("on_select"), "ignore")
        self.assertNotIn("selection_mode", calls[-1])

    def test_explicit_keyed_selection_remains_interactive(self):
        calls = []

        def base_dataframe(*args, **kwargs):
            calls.append(kwargs.copy())
            return kwargs

        protected = MODULE._protect_legacy_dataframe_wrapper(
            legacy_selectable_wrapper(base_dataframe)
        )
        protected(
            [],
            key="employee_requests_table_14",
            on_select="rerun",
            selection_mode="single-row",
        )
        self.assertEqual(calls[-1].get("on_select"), "rerun")
        self.assertEqual(calls[-1].get("selection_mode"), "single-row")

    def test_streamlit_assignment_is_intercepted_before_legacy_wrapper_runs(self):
        st = types.ModuleType("fake_streamlit_for_freeze_test")
        st.calls = []
        st.session_state = FakeState()

        def base_dataframe(*args, **kwargs):
            st.calls.append(kwargs.copy())
            return kwargs

        st.dataframe = base_dataframe
        MODULE._install_dataframe_guard(st)
        legacy = legacy_selectable_wrapper(st.dataframe)
        st.dataframe = legacy

        self.assertTrue(getattr(st.dataframe, MODULE.DISPLAY_ONLY_MARKER, False))
        self.assertTrue(getattr(st.dataframe, "_pb_selectable_wrapper", False))

        st.dataframe([], key="keyed_report_only")
        self.assertEqual(st.calls[-1].get("on_select"), "ignore")
        self.assertNotIn("selection_mode", st.calls[-1])

        st.dataframe(
            [],
            key="keyed_edit_table",
            on_select="rerun",
            selection_mode="single-row",
        )
        self.assertEqual(st.calls[-1].get("on_select"), "rerun")
        self.assertEqual(st.calls[-1].get("selection_mode"), "single-row")

    def test_protected_wrapper_does_not_stack_on_rerun(self):
        calls = []

        def base_dataframe(*args, **kwargs):
            calls.append(kwargs.copy())
            return kwargs

        first = MODULE._protect_legacy_dataframe_wrapper(
            legacy_selectable_wrapper(base_dataframe)
        )
        second = MODULE._protect_legacy_dataframe_wrapper(first)
        self.assertIs(first, second)
        self.assertTrue(getattr(first, "_pb_selectable_wrapper", False))

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
