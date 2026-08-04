import ast
import json
import pathlib
import sys
import unittest

sys.dont_write_bytecode = True


ROOT = pathlib.Path(__file__).resolve().parents[1]
LEAN = ROOT / "jobhub_lean"


class SourceStructureTests(unittest.TestCase):
    def test_application_modules_parse(self):
        paths = [
            ROOT / "pb_jobhub_app.py",
            ROOT / "pb_jobhub_visual_scheduler.py",
            ROOT / "jobhub_core.py",
            *sorted(LEAN.glob("*.py")),
        ]
        for path in paths:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path.relative_to(ROOT)))

    def test_entry_point_is_small_and_modular(self):
        source = (ROOT / "pb_jobhub_app.py").read_text(encoding="utf-8")
        self.assertLessEqual(len(source.splitlines()), 10)
        self.assertIn("from jobhub_lean.app import run", source)
        self.assertIn("run()", source)

    def test_no_duplicate_top_level_definitions(self):
        duplicates = {}
        for path in sorted(LEAN.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            seen = set()
            repeated = set()
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name in seen:
                        repeated.add(node.name)
                    seen.add(node.name)
            if repeated:
                duplicates[path.name] = sorted(repeated)
        self.assertEqual(duplicates, {})

    def test_sensitive_backup_files_are_not_part_of_release_package(self):
        unsafe = [
            str(item.relative_to(ROOT))
            for item in ROOT.rglob("*")
            if (
                item.name.endswith(".bak")
                or item.name.startswith(("PB_JobHub_Install_", "RUN_INSTALL_"))
                or item.name == ".testdeps"
            )
        ]
        self.assertEqual(unsafe, [])

    def test_main_app_has_mobile_viewport_guards(self):
        app = (LEAN / "app.py").read_text(encoding="utf-8")
        mobile = (LEAN / "mobile.py").read_text(encoding="utf-8")
        self.assertIn('initial_sidebar_state="auto"', app)
        self.assertIn("install_mobile_shell()", app)
        self.assertIn("PB_JOBHUB_MOBILE_VIEWPORT_FIX", mobile)
        self.assertIn('div[data-testid=\\"stHorizontalBlock\\"]', mobile)
        self.assertIn("max-width: 100vw !important", mobile)
        self.assertIn("-webkit-overflow-scrolling: touch !important", mobile)
        self.assertIn("font-size: 16px !important", mobile)
        self.assertIn("viewport-fit=cover", mobile)

    def test_phone_push_runs_in_top_level_page_with_pwa_support(self):
        app = (LEAN / "app.py").read_text(encoding="utf-8")
        mobile = (LEAN / "mobile.py").read_text(encoding="utf-8")
        self.assertIn("render_phone_push_opt_in()", app)
        self.assertIn("def render_phone_push_opt_in", mobile)
        self.assertIn("st.html(", mobile)
        self.assertIn("unsafe_allow_javascript=True", mobile)
        self.assertNotIn("st.iframe(", mobile)
        self.assertIn("OneSignal.Notifications.requestPermission()", mobile)
        self.assertIn("OneSignal.User.PushSubscription.optIn()", mobile)
        self.assertIn("OneSignal.User.PushSubscription.id", mobile)
        self.assertIn("serviceWorkerPath: 'app/static/OneSignalSDKWorker.js'", mobile)
        self.assertIn("Share → Add to Home Screen", mobile)

        manifest = json.loads(
            (ROOT / "static" / "manifest.webmanifest").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"], "/?jobhub-home=1")
        self.assertTrue(manifest["icons"])
        self.assertEqual(manifest["icons"][0]["type"], "image/png")

    def test_phone_push_has_server_connection_and_delivery_diagnostics(self):
        source = (LEAN / "mobile.py").read_text(encoding="utf-8")
        self.assertIn("def phone_push_provider_status", source)
        self.assertIn('requests.get(', source)
        self.assertIn('"https://api.onesignal.com/notifications"', source)
        self.assertIn('"connected"', source)
        self.assertIn('"status_code"', source)

    def test_operations_dashboard_contains_requested_widgets(self):
        source = (LEAN / "directory.py").read_text(encoding="utf-8")
        self.assertIn("def dashboard_page", source)
        for label in (
            "Crucial Jobs", "Paint to Order", "Today’s Staff", "Tasks to Complete",
            "Job Progress", "Active Site Blockers", "Timesheets", "Overhead & Profit",
            "Overdue Claims",
        ):
            self.assertIn(label, source)
        self.assertIn("today_text = date.today().isoformat()", source)

    def test_job_folder_uses_editable_schedule_and_weighted_progress(self):
        records = (LEAN / "records.py").read_text(encoding="utf-8")
        scheduler = (ROOT / "pb_jobhub_visual_scheduler.py").read_text(encoding="utf-8")
        tracker = (ROOT / "jobhub_progress_tracker.py").read_text(encoding="utf-8")
        self.assertIn("render_job_folder_schedule_editor(job_id, ctx.user)", records)
        self.assertIn('st.tabs(["Documents", "Schedule", "Progress"])', records)
        self.assertIn('"prepped_sealed": 30.0', records)
        self.assertIn('"prep_spray_finished": 30.0', records)
        self.assertIn('"cut_rolled": 30.0', records)
        self.assertIn('"defects": 10.0', records)
        self.assertIn("def render_job_folder_schedule_editor", scheduler)
        self.assertIn("replace_conflicts_for_assignment_edit", scheduler)
        self.assertIn("_render_custom_internal_items", tracker)

    def test_estimate_pricing_uses_one_simple_daily_target(self):
        estimating = (LEAN / "estimating.py").read_text(encoding="utf-8")
        packs = (LEAN / "job_packs.py").read_text(encoding="utf-8")
        production = (ROOT / "jobhub_production.py").read_text(encoding="utf-8")
        self.assertIn("value=125.0", estimating)
        self.assertIn('"painter_day_hours": 8', packs)
        self.assertIn('"painter_day_value_ex_gst": 1000', packs)
        self.assertNotIn('"Contingency % (optional)"', estimating)
        self.assertIn("contingency_amount = 0.0", production)

    def test_job_pack_import_supports_one_click_single_and_bulk_matching(self):
        source = (LEAN / "job_packs.py").read_text(encoding="utf-8")
        self.assertIn("match_job_pack_to_jobs", source)
        self.assertIn("expand_nested_job_pack_uploads", source)
        self.assertIn("accept_multiple_files=True", source)
        self.assertIn('key="takeoff_job_pack_one_click_import"', source)
        self.assertIn('key="takeoff_job_pack_bulk_one_click_import"', source)
        self.assertIn('"purchase_orders.csv"', source)
        self.assertIn('"job_stages.csv"', source)
        self.assertIn("def import_job_pack", source)

    def test_operations_hub_calculates_contract_hours_automatically(self):
        source = (ROOT / "jobhub_enterprise.py").read_text(encoding="utf-8")
        production_source = (ROOT / "jobhub_production.py").read_text(encoding="utf-8")
        self.assertIn("def remaining_contract_labour", production_source)
        self.assertIn('result["Material Commitment"]', source)
        self.assertIn('result["Forecast Remaining Labour Hours"] = contract_labour.map', source)
        self.assertIn('h1.metric("Hours remaining"', source)
        self.assertIn('h5.metric("Work target", "$125 / hour")', source)


if __name__ == "__main__":
    unittest.main()
