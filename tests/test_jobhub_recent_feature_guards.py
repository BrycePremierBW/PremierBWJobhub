"""Smoke tests for recent JobHub feature guards.

These tests intentionally avoid importing the Streamlit app. They parse and
inspect the source files so a bad guard edit, missing startup install, or lost
feature hook is caught before deployment.
"""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]

GUARD_FILES = [
    "jobhub/__init__.py",
    "jobhub/bulk_delete_guard.py",
    "jobhub/mobile_sidebar_guard.py",
    "jobhub/navigation_state_guard.py",
    "jobhub/notification_wording_guard.py",
    "jobhub/push_configuration_guard.py",
    "jobhub/stage_preset_guard.py",
    "jobhub/stage_preset_selector_fix_guard.py",
    "jobhub/stage_selection_guard.py",
    "jobhub/swms_attach_fallback_guard.py",
    "jobhub/swms_guard.py",
    "jobhub/swms_signature_index_guard.py",
    "jobhub/swms_visibility_guard.py",
    "jobhub/timesheet_area_guard.py",
]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class RecentFeatureGuardSmokeTests(unittest.TestCase):
    def test_recent_guard_sources_parse(self):
        for path in GUARD_FILES:
            with self.subTest(path=path):
                ast.parse(read(path), filename=path)

    def test_startup_installs_recent_guards(self):
        source = read("jobhub/__init__.py")
        expected_install_calls = [
            "install_push_configuration_guard()",
            "install_notification_wording_guard()",
            "install_mobile_sidebar_guard()",
            "install_navigation_state_guard()",
            "install_stage_selection_guard()",
            "install_stage_preset_selector_fix_guard()",
            "install_stage_preset_guard()",
            "install_timesheet_area_guard()",
            "install_bulk_delete_guard()",
            "install_swms_guard()",
            "install_swms_attach_fallback_guard()",
            "install_swms_signature_index_guard()",
            "install_swms_visibility_guard()",
            "install_ai_menu_guard()",
        ]
        positions = []
        for call in expected_install_calls:
            self.assertIn(call, source)
            positions.append(source.index(call))
        self.assertEqual(positions, sorted(positions), "Guard install order changed unexpectedly")

    def test_mobile_sidebar_phone_layout_is_hardened(self):
        source = read("jobhub/mobile_sidebar_guard.py")
        required = [
            "PB_JOBHUB_MOBILE_SIDEBAR_FINAL_FIX_V3",
            "--pb-mobile-sidebar-width: min(78vw, 300px)",
            "--pb-mobile-sidebar-width: min(74vw, 280px)",
            "100dvh",
            "overflow-wrap: anywhere",
            "pb-mobile-sidebar-auto-close-v3",
            "touchend",
            "initial_sidebar_state"]
        for marker in required:
            self.assertIn(marker, source)

    def test_swms_menu_route_is_guarded_against_dashboard_reset(self):
        source = read("jobhub/swms_visibility_guard.py")
        required = [
            'ADMIN_SWMS_LABEL = "SWMS / Safety Sign-off"',
            "_install_session_state_reset_guard",
            "RESET_SAFE_VALUES",
            "_with_swms_option",
            "_show_swms_page(st)",
            "st.stop()",
            "install_swms_visibility_guard",
        ]
        for marker in required:
            self.assertIn(marker, source)

    def test_swms_core_has_create_download_and_signature_paths(self):
        source = read("jobhub/swms_guard.py")
        required = [
            "CREATE TABLE IF NOT EXISTS job_swms",
            "CREATE TABLE IF NOT EXISTS job_swms_signatures",
            "def create_swms_for_job",
            "Generate and attach SWMS to this job",
            "Download selected SWMS PDF",
            "Sign / acknowledge this SWMS",
            "SWMS signature register for this job",
        ]
        for marker in required:
            self.assertIn(marker, source)

    def test_swms_attach_fallback_is_installed(self):
        source = read("jobhub/swms_attach_fallback_guard.py")
        required = [
            "install_swms_attach_fallback_guard",
            "attach_with_fallback",
            "except TypeError",
            "INSERT INTO job_documents",
            "Generic SWMS generated in JobHub.",
        ]
        for marker in required:
            self.assertIn(marker, source)

    def test_timesheet_area_guard_keeps_area_options(self):
        source = read("jobhub/timesheet_area_guard.py")
        self.assertIn('AREA_OPTIONS = ["All", "Internal", "External"]', source)
        self.assertIn('"Area Worked"', source)
        self.assertIn('f"{area_text} — {work_type_text}"', source)
        self.assertIn("DeltaGenerator", source)

    def test_stage_presets_include_required_workflows(self):
        source = read("jobhub/stage_preset_guard.py")
        required = [
            "All / whole job — 100%",
            "Internal — 100%",
            "External — 100%",
            "Internal prep and spray Sealer — 30%",
            "prep and spray finish coats — 30%",
            "cut and roll walls and paint doors — 30%",
            "upper scaff work — 45%",
            "lower — 45%",
            "Save this custom stage for future",
        ]
        for marker in required:
            self.assertIn(marker, source)

    def test_bulk_delete_guard_is_safe_and_nested_table_ready(self):
        source = read("jobhub/bulk_delete_guard.py")
        required = [
            "selectable_staff_requests_admin",
            "selectable_job_purchase_orders_",
            "selectable_job_stages_",
            "_safe_int",
            "DeltaGenerator",
            "stage has linked schedule, timesheet, progress, baseline or claim records",
            "purchase order is linked to stages or claims",
        ]
        for marker in required:
            self.assertIn(marker, source)


if __name__ == "__main__":
    unittest.main()
