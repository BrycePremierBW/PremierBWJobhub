import ast
import json
import pathlib
import sys
import unittest

sys.dont_write_bytecode = True


ROOT = pathlib.Path(__file__).resolve().parents[1]


class SourceStructureTests(unittest.TestCase):
    def test_application_modules_parse(self):
        for name in ("pb_jobhub_app.py", "pb_jobhub_visual_scheduler.py", "jobhub_core.py"):
            ast.parse((ROOT / name).read_text(encoding="utf-8"), filename=name)

    def test_no_duplicate_top_level_definitions(self):
        tree = ast.parse((ROOT / "pb_jobhub_app.py").read_text(encoding="utf-8"))
        seen = set()
        duplicates = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name in seen:
                    duplicates.add(node.name)
                seen.add(node.name)
        self.assertEqual(duplicates, set())

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
        source = (ROOT / "pb_jobhub_app.py").read_text(encoding="utf-8")
        self.assertIn('initial_sidebar_state="auto"', source)
        self.assertIn("PB_JOBHUB_MOBILE_VIEWPORT_FIX", source)
        self.assertIn('div[data-testid="stHorizontalBlock"]', source)
        self.assertIn("max-width: 100vw !important", source)
        self.assertIn("-webkit-overflow-scrolling: touch !important", source)
        self.assertIn("font-size: 16px !important", source)
        self.assertIn("viewport-fit=cover", source)

    def test_phone_push_runs_in_top_level_page_with_pwa_support(self):
        source = (ROOT / "pb_jobhub_app.py").read_text(encoding="utf-8")
        start = source.index("def render_phone_push_opt_in")
        end = source.index("STAFF_REQUEST_TYPES", start)
        push_source = source[start:end]
        self.assertIn("st.html(", push_source)
        self.assertIn("unsafe_allow_javascript=True", push_source)
        self.assertNotIn("st.iframe(", push_source)
        self.assertIn("OneSignal.Notifications.requestPermission()", push_source)
        self.assertIn("OneSignal.User.PushSubscription.optIn()", push_source)
        self.assertIn("OneSignal.User.PushSubscription.id", push_source)
        self.assertIn("serviceWorkerPath: 'app/static/OneSignalSDKWorker.js'", push_source)
        self.assertIn("Share → Add to Home Screen", push_source)

        manifest = json.loads(
            (ROOT / "static" / "manifest.webmanifest").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"], "/?jobhub-home=1")
        self.assertTrue(manifest["icons"])
        self.assertEqual(manifest["icons"][0]["type"], "image/png")
        self.assertIn("ensure_mobile_web_assets()", source)

    def test_phone_push_has_server_connection_and_delivery_diagnostics(self):
        source = (ROOT / "pb_jobhub_app.py").read_text(encoding="utf-8")
        self.assertIn("def phone_push_provider_status", source)
        self.assertIn('requests.get(\n            "https://api.onesignal.com/notifications"', source)
        self.assertIn("Phone notification diagnostics", source)
        self.assertIn("staff_request_push_diagnostics", source)

    def test_operations_dashboard_contains_requested_widgets_and_settings(self):
        source = (ROOT / "pb_jobhub_app.py").read_text(encoding="utf-8")
        self.assertIn("def render_operational_dashboard", source)
        for label in (
            "Crucial Jobs", "Paint to Order", "Today’s Staff", "Tasks to Complete",
            "Job Progress", "Active Site Blockers", "Timesheets", "Overhead & Profit",
            "Overdue Claims",
        ):
            self.assertIn(label, source)
        self.assertIn("def save_operating_settings", source)
        self.assertIn("overhead_recovery_metrics", source)
        self.assertIn("today_text = jobhub_today().isoformat()", source)
        self.assertIn("def pb_dashboard_navigation_tile", source)
        self.assertIn("def pb_dashboard_widget_link", source)
        for target in (
            '"Jobs", "active_jobs"',
            '"Staff Requests", "staff_tasks"',
            '"Timesheets", "timesheets"',
            '"Material Costs", "paint_orders"',
            '"Job Progress Tracker", "active_blockers"',
        ):
            self.assertIn(target, source)

    def test_job_folder_uses_editable_schedule_and_weighted_progress(self):
        app_source = (ROOT / "pb_jobhub_app.py").read_text(encoding="utf-8")
        scheduler_source = (ROOT / "pb_jobhub_visual_scheduler.py").read_text(encoding="utf-8")
        tracker_source = (ROOT / "jobhub_progress_tracker.py").read_text(encoding="utf-8")
        self.assertIn("render_job_folder_schedule_editor(job_id, get_current_user())", app_source)
        self.assertIn("def render_job_folder_schedule_editor", scheduler_source)
        self.assertIn("replace_conflicts_for_assignment_edit", scheduler_source)
        for field in ("prepped_sealed", "prep_spray_finished", "cut_rolled", "defects"):
            self.assertIn(field, tracker_source)
        self.assertIn("_render_custom_internal_items", tracker_source)

    def test_estimate_pricing_uses_one_simple_daily_target(self):
        app_source = (ROOT / "pb_jobhub_app.py").read_text(encoding="utf-8")
        production_source = (ROOT / "jobhub_production.py").read_text(encoding="utf-8")
        self.assertIn('col6.metric("Labour Rate", "$1,000 / painter per 8-hour day")', app_source)
        self.assertIn("contingency_percent = 0.0", app_source)
        self.assertNotIn('"Contingency % (optional)"', app_source)
        self.assertNotIn('"Low value / day"', app_source)
        self.assertNotIn('"High value / day"', app_source)
        self.assertIn("contingency_amount = 0.0", production_source)

    def test_job_pack_import_supports_one_click_single_and_bulk_matching(self):
        source = (ROOT / "pb_jobhub_app.py").read_text(encoding="utf-8")
        self.assertIn("match_job_pack_to_jobs", source)
        self.assertIn("def _takeoff_render_bulk_import", source)
        self.assertIn("accept_multiple_files=True", source)
        self.assertIn('key="takeoff_job_pack_one_click_import"', source)
        self.assertIn('key="takeoff_job_pack_bulk_one_click_import"', source)
        self.assertIn('"purchase_orders.csv"', source)
        self.assertIn('"job_stages.csv"', source)

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
