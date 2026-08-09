from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "jobhub" / "system_health_v2_guard.py"
SPEC = importlib.util.spec_from_file_location("jobhub_system_health_v2_guard_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class SystemHealthGuardTests(unittest.TestCase):
    def test_source_parses(self):
        ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))

    def test_guard_is_installed_before_mobile_navigation(self):
        source = (ROOT / "jobhub" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("from .system_health_v2_guard import install_system_health_guard", source)
        self.assertIn("install_system_health_guard()", source)
        self.assertLess(
            source.index("install_system_health_guard()"),
            source.index("install_mobile_top_navigation_guard()"),
        )

    def test_management_menu_injection_is_targeted(self):
        self.assertTrue(
            MODULE._should_inject(
                "Management Section",
                "management_menu",
                ["Builders & Clients", "Employees"],
            )
        )
        self.assertFalse(
            MODULE._should_inject(
                "Site Operations Section",
                "site_operations_menu",
                ["Staff Scheduler", "Job Progress Tracker"],
            )
        )

    def test_health_summary_prioritises_critical_then_warning(self):
        original_database = MODULE._database_report
        original_storage = MODULE._storage_report
        original_archive = MODULE._archive_report
        original_restore = MODULE._postgres_restore_report
        original_runtime = MODULE._runtime_report
        try:
            MODULE._database_report = lambda: ([MODULE._check("Database", "Probe", "Healthy", "ok")], {})
            MODULE._storage_report = lambda: ([MODULE._check("Storage", "Disk", "Warning", "low")], {})
            MODULE._archive_report = lambda: ([MODULE._check("Recovery", "Archive", "Healthy", "recent")], {})
            MODULE._postgres_restore_report = lambda: ([MODULE._check("Recovery", "Restore", "Critical", "unverified")], {})
            MODULE._runtime_report = lambda: {"JobHub build": "test"}
            report = MODULE.build_system_health_report()
            self.assertEqual(report["overall_status"], "Critical")
            self.assertEqual(report["status_counts"]["Critical"], 1)
            self.assertEqual(report["status_counts"]["Warning"], 1)
        finally:
            MODULE._database_report = original_database
            MODULE._storage_report = original_storage
            MODULE._archive_report = original_archive
            MODULE._postgres_restore_report = original_restore
            MODULE._runtime_report = original_runtime

    def test_foundation_diagnostics_remain_present(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        required = [
            "System Health",
            "SELECT 1 AS health_probe",
            "current_database()",
            "Duplicate job numbers",
            "Jobs with missing builder/client",
            "Free disk space",
            "Latest JobHub data archive",
            "PostgreSQL restore drill",
            "Unresolved error events",
            "Download health report",
            "managers and administrators only",
        ]
        for marker in required:
            self.assertIn(marker, source)

    def test_byte_formatting(self):
        self.assertEqual(MODULE._format_bytes(1024), "1.0 KB")
        self.assertEqual(MODULE._format_bytes(1024 * 1024), "1.0 MB")


if __name__ == "__main__":
    unittest.main()
