from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReleaseIntegrityTests(unittest.TestCase):
    """Protect the verified live JobHub from accidental incomplete rewrites."""

    def test_live_application_has_required_feature_surface(self):
        app_path = ROOT / "pb_jobhub_app.py"
        source = app_path.read_text(encoding="utf-8")
        line_count = source.count("\n") + 1

        self.assertGreater(
            line_count,
            20_000,
            "pb_jobhub_app.py was replaced or heavily truncated. Do not merge an app rewrite "
            "until every existing JobHub route and compatibility test is deliberately migrated.",
        )

        required_markers = {
            "operations hub": "render_operations_hub",
            "staff scheduler": "render_jobhub_staff_scheduler",
            "progress tracker": "render_progress_tracker",
            "device notifications": "render_phone_push_opt_in",
            "job-pack matching": "match_job_pack_to_jobs",
            "estimate working sheet": "estimate_working_sheet_page",
            "enterprise context": "jobhub_enterprise_context",
            "build marker": "PB_JOBHUB_BUILD",
        }
        missing = [label for label, marker in required_markers.items() if marker not in source]
        self.assertFalse(missing, f"Live JobHub feature markers are missing: {', '.join(missing)}")

    def test_runtime_stability_guards_remain_installed(self):
        package_source = (ROOT / "jobhub" / "__init__.py").read_text(encoding="utf-8")
        performance_source = (ROOT / "jobhub" / "runtime_performance_guard.py").read_text(
            encoding="utf-8"
        )
        permission_source = (ROOT / "jobhub" / "permission_policy_guard.py").read_text(
            encoding="utf-8"
        )
        workflow_source = (ROOT / ".github" / "workflows" / "jobhub-tests.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("install_runtime_performance_guard", package_source)
        self.assertIn("install_runtime_performance_guard()", package_source)
        self.assertIn("install_permission_policy_guard", package_source)
        self.assertIn("install_permission_policy_guard()", package_source)
        self.assertIn("_install_dataframe_guard", performance_source)
        self.assertIn("_wrap_scheduler_sync", performance_source)
        self.assertIn("_wrap_progress_sync", performance_source)
        self.assertIn("ROUTE_REQUIREMENTS", permission_source)
        self.assertIn("Permissions & Access Audit", permission_source)
        self.assertIn("tests/run_stage_control_ci.py", workflow_source)


if __name__ == "__main__":
    unittest.main()
