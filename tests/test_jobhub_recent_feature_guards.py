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
    "jobhub/job_folder_uploaded_documents_guard.py",
    "jobhub/mobile_sidebar_guard.py",
    "jobhub/navigation_state_guard.py",
    "jobhub/notification_wording_guard.py",
    "jobhub/po_upload_guard.py",
    "jobhub/po_upload_native_guard.py",
    "jobhub/po_upload_scope_return_guard.py",
    "jobhub/progress_external_options_guard.py",
    "jobhub/push_configuration_guard.py",
    "jobhub/sidebar_readability_guard.py",
    "jobhub/stage_preset_guard.py",
    "jobhub/stage_preset_selector_fix_guard.py",
    "jobhub/stage_selection_guard.py",
    "jobhub/swms_attach_fallback_guard.py",
    "jobhub/swms_guard.py",
    "jobhub/swms_signature_index_guard.py",
    "jobhub/swms_visibility_guard.py",
    "jobhub/timesheet_area_guard.py",
    "jobhub_core.py",
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
            "install_sidebar_readability_guard()",
            "install_navigation_state_guard()",
            "install_po_upload_guard()",
            "install_po_upload_performance_guard()",
            "install_po_upload_native_guard()",
            "install_job_folder_uploaded_documents_guard()",
            "install_progress_external_options_guard()",
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
        self.assertNotIn("install_po_upload_scope_return_guard()", source)
        self.assertNotIn("install_po_upload_split_guard()", source)

    def test_jobhub_core_keeps_required_app_imports(self):
        source = read("jobhub_core.py")
        required = [
            "def calculate_shift_hours",
            "def next_scoped_number",
            "def hash_password",
            "def verify_password",
            "def password_strength_errors",
            "def validate_public_http_url",
            "def calculate_estimate_pricing",
            "return round(net_minutes / 60, 2)",
        ]
        for marker in required:
            self.assertIn(marker, source)

    def test_password_policy_requires_fifteen_characters_without_symbol_or_number_rules(self):
        source = read("jobhub_core.py")
        self.assertIn("MIN_PASSWORD_LENGTH = 15", source)
        self.assertNotIn('"Include a number."', source)
        self.assertNotIn('"Include a symbol."', source)
        self.assertNotIn('re.search(r"\\d"', source)
        self.assertNotIn('re.search(r"[^A-Za-z0-9]"', source)

    def test_sidebar_readability_and_lower_toggle_are_installed(self):
        source = read("jobhub/sidebar_readability_guard.py")
        required = [
            "PB_JOBHUB_SIDEBAR_READABILITY_V1",
            "font-weight: 700",
            "[data-testid=\"collapsedControl\"]",
            "top: calc(4.25rem",
            "install_sidebar_readability_guard",
        ]
        for marker in required:
            self.assertIn(marker, source)

    def test_mobile_sidebar_does_not_hijack_desktop_routing(self):
        source = read("jobhub/mobile_sidebar_guard.py")
        required = [
            "PB_JOBHUB_MOBILE_PHONE_NAVIGATION_FIX_V5",
            "Do not patch or override Streamlit radio return values",
            "pb-mobile-sidebar-autoclose-v5",
            "body.pb-mobile-sidebar-closing",
            "section[data-testid=\"stSidebar\"]",
            "install_mobile_sidebar_guard",
        ]
        forbidden = [
            "_install_mobile_quick_menu_guard",
            "_render_quick_menu",
            "_patch_radio",
            "pb_mobile_quick_radio",
            "return quick_choice",
        ]
        for marker in required:
            self.assertIn(marker, source)
        for marker in forbidden:
            self.assertNotIn(marker, source)

    def test_upload_po_route_is_guarded_against_dashboard_reset(self):
        source = read("jobhub/po_upload_guard.py")
        required = [
            'PO_UPLOAD_LABEL = "Upload PO"',
            "_install_session_state_reset_guard",
            "RESET_SAFE_VALUES",
            "MENU_MARKERS",
            "_session_value",
            "_show_page(st)",
            "st.stop()",
            "SERIAL PRIMARY KEY",
            "INTEGER PRIMARY KEY AUTOINCREMENT",
            "install_po_upload_guard",
        ]
        forbidden = ["PO_UPLOAD_DOC_TYPES"]
        for marker in required:
            self.assertIn(marker, source)
        for marker in forbidden:
            self.assertNotIn(marker, source)

    def test_upload_po_scope_return_guard_adds_return_and_internal_external_lines(self):
        source = read("jobhub/po_upload_scope_return_guard.py")
        required = [
            "Return to start / Dashboard",
            "DISPLAY_INTERNAL",
            "DISPLAY_EXTERNAL",
            "Calculate % from",
            "Area / scope name",
            "Area / stage value ex GST",
            "_clear_po_route",
            "install_po_upload_scope_return_guard",
        ]
        for marker in required:
            self.assertIn(marker, source)

    def test_upload_po_handles_live_schema_differences(self):
        source = read("jobhub/po_upload_guard.py")
        required = [
            "_table_columns",
            "information_schema.columns",
            "_ensure_table_column",
            "_insert_existing_columns",
            "Older JobHub databases have slightly different document-column names",
            "job_documents",
            "uploaded_at",
            "doc_type",
            "upload_date",
            "date_uploaded",
        ]
        for marker in required:
            self.assertIn(marker, source)

    def test_upload_po_calculates_amount_and_percentage(self):
        source = read("jobhub/po_upload_guard.py")
        required = [
            "Enter PO amount → calculate %",
            "Enter % → calculate PO amount",
            "Whole job value",
            "Manual area / stage value",
            "_calculate_po_values",
            "po_scope_label",
            "po_scope_base_ex_gst",
            "po_scope_percent",
            "po_percent_of_job",
            "po_calculation_mode",
            "Area / stage value ex GST",
            "% of selected area / scope",
            "% of whole job",
        ]
        for marker in required:
            self.assertIn(marker, source)

    def test_job_folders_have_uploaded_documents_section(self):
        source = read("jobhub/job_folder_uploaded_documents_guard.py")
        required = [
            "Uploaded Documents",
            "job_documents",
            "job_purchase_orders",
            "render_uploaded_documents_panel",
            "Download",
            "install_job_folder_uploaded_documents_guard",
        ]
        for marker in required:
            self.assertIn(marker, source)

    def test_progress_tracker_has_external_options(self):
        source = read("jobhub/progress_external_options_guard.py")
        required = [
            "importlib.import_module",
            "External — 100%",
            "Upper scaff / lower / touch-ups",
            "Coating steps",
            "external_overall",
            "upper_scaff_work",
            "lower_external",
            "touch ups",
            "FALLBACK_COATING_STAGES",
            "install_progress_external_options_guard",
            "_install_external_schema_guard",
            "_install_external_editor_guard",
        ]
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
