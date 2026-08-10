from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "jobhub_page_render_freeze_guard_test",
    ROOT / "jobhub" / "page_render_freeze_guard.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class Spinner:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeStreamlit(types.SimpleNamespace):
    def __init__(self):
        super().__init__()
        self.session_state = {}
        self.button_result = False
        self.messages = []

    def button(self, *args, **kwargs):
        return self.button_result

    def spinner(self, *args, **kwargs):
        return Spinner()

    def toast(self, message, **kwargs):
        self.messages.append(("toast", message))

    def warning(self, message, **kwargs):
        self.messages.append(("warning", message))

    def success(self, message, **kwargs):
        self.messages.append(("success", message))

    def error(self, message, **kwargs):
        self.messages.append(("error", message))


class PageRenderFreezeGuardTests(unittest.TestCase):
    def test_daily_backup_is_deferred_during_normal_page_render(self):
        st = FakeStreamlit()
        calls = []

        def original(context):
            calls.append(context)

        wrapped = MODULE._wrap_daily_backup(original, st)
        with tempfile.TemporaryDirectory() as directory:
            wrapped({"DATA_DIR": directory})

        self.assertEqual(calls, [])
        self.assertTrue(st.session_state[MODULE.DAILY_BACKUP_DUE_KEY])

    def test_existing_daily_backup_is_detected_without_exporting_tables(self):
        st = FakeStreamlit()
        calls = []

        def original(context):
            calls.append(context)

        wrapped = MODULE._wrap_daily_backup(original, st)
        with tempfile.TemporaryDirectory() as directory:
            backup_dir = Path(directory) / "backups"
            backup_dir.mkdir()
            prefix = f"PB_JobHub_Daily_Data_{MODULE.jobhub_today().strftime('%Y%m%d')}"
            (backup_dir / f"{prefix}.zip").write_bytes(b"backup")
            wrapped({"DATA_DIR": directory})

        self.assertEqual(calls, [])
        self.assertFalse(st.session_state[MODULE.DAILY_BACKUP_DUE_KEY])

    def test_startup_sync_is_a_fast_noop(self):
        calls = []
        wrapped = MODULE._skip_startup_sync(lambda *args, **kwargs: calls.append(1) or 7, "progress")

        self.assertEqual(wrapped({"df_query": object()}), 0)
        self.assertEqual(calls, [])
        self.assertTrue(callable(getattr(wrapped, MODULE.ORIGINAL_SYNC_ATTR)))

    def test_scheduler_sync_runs_only_when_scheduler_view_opens_and_is_throttled(self):
        st = FakeStreamlit()
        sync_calls = []
        render_calls = []

        wrapped = MODULE._wrap_scheduler_renderer(
            lambda *args, **kwargs: render_calls.append((args, kwargs)) or "rendered",
            lambda: sync_calls.append(1) or 2,
            st,
        )

        self.assertEqual(wrapped(user={"role": "admin"}), "rendered")
        self.assertEqual(wrapped(user={"role": "admin"}), "rendered")
        self.assertEqual(sync_calls, [1])
        self.assertEqual(len(render_calls), 2)
        self.assertTrue(any(level == "toast" for level, _ in st.messages))

    def test_progress_sync_runs_only_after_explicit_button_click(self):
        st = FakeStreamlit()
        sync_calls = []
        render_calls = []
        successes = []
        context = {"pb_success": successes.append, "pb_error": self.fail}

        wrapped = MODULE._wrap_progress_renderer(
            lambda ctx: render_calls.append(ctx) or "rendered",
            lambda ctx: sync_calls.append(ctx) or 3,
            st,
        )

        self.assertEqual(wrapped(context), "rendered")
        self.assertEqual(sync_calls, [])

        st.button_result = True
        self.assertEqual(wrapped(context), "rendered")
        self.assertEqual(sync_calls, [context])
        self.assertIn("3 new external line(s)", successes[-1])
        self.assertEqual(len(render_calls), 2)

    def test_guard_is_installed_before_main_app_runs(self):
        source = (ROOT / "jobhub" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn(
            "from .page_render_freeze_guard import install_page_render_freeze_guard",
            source,
        )
        self.assertIn("install_page_render_freeze_guard()", source)


if __name__ == "__main__":
    unittest.main()
