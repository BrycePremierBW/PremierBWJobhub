from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "jobhub_po_upload_native_guard_test",
    ROOT / "jobhub" / "po_upload_native_guard.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class NativePoUploadTests(unittest.TestCase):
    def test_split_line_numbers_are_unique_without_schema_changes(self):
        self.assertEqual(MODULE._line_number("PO-123", "INT"), "PO-123-INT")
        self.assertEqual(MODULE._line_number("PO-123", "EXT"), "PO-123-EXT")
        self.assertEqual(MODULE._line_number("PO-123-INT", "INT"), "PO-123-INT")

    def test_po_values_preserve_original_builder_number(self):
        values = MODULE._po_values(
            job_id=7,
            stage_id=3,
            po_number="PO-123-INT",
            original_po_number="PO-123",
            amount=2500,
            scope_label="Internal",
            scope_base=10000,
            job_value=20000,
            file_name="po.pdf",
            file_path="/tmp/po.pdf",
            uploaded_by="admin",
            notes="Progress claim",
            mode="Native split - Internal",
            now="2026-08-05 06:00:00",
        )
        self.assertEqual(values["po_number"], "PO-123-INT")
        self.assertEqual(values["amount_ex_gst"], 2500.0)
        self.assertEqual(values["po_scope_percent"], 25.0)
        self.assertEqual(values["po_percent_of_job"], 12.5)
        self.assertIn("Original builder PO: PO-123", values["notes"])

    def test_large_file_detection_uses_reported_size(self):
        class Upload:
            size = MODULE.MAX_UPLOAD_BYTES + 1

        self.assertGreater(MODULE._uploaded_size(Upload()), MODULE.MAX_UPLOAD_BYTES)

    def test_startup_uses_final_direct_route_after_mobile_navigation(self):
        source = (ROOT / "jobhub" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("install_po_upload_direct_route_guard()", source)
        self.assertIn("def _retired_po_upload_route_guard() -> bool:", source)
        self.assertIn(
            "install_po_upload_guard = _retired_po_upload_route_guard",
            source,
        )
        self.assertIn("install_po_upload_native_guard()", source)
        self.assertNotIn("from .po_upload_guard import install_po_upload_guard", source)
        self.assertNotIn("install_po_upload_scope_return_guard()", source)
        self.assertNotIn("install_po_upload_split_guard()", source)
        self.assertLess(
            source.index("install_mobile_top_navigation_guard()"),
            source.index("install_po_upload_direct_route_guard()"),
        )

    def test_direct_route_owns_desktop_and_mobile_main_navigation(self):
        source = (ROOT / "jobhub" / "po_upload_direct_route_guard.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('MAIN_NAV_KEYS = {"main_menu", "pb_mobile_app_main_menu"}', source)
        self.assertIn("render_native_po_upload_page()", source)
        self.assertIn("direct PO route", source)

    def test_native_page_does_not_call_schema_migration(self):
        source = (ROOT / "jobhub" / "po_upload_native_guard.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("_ensure_schema()", source)
        self.assertNotIn("ALTER TABLE", source)
        self.assertNotIn("DROP CONSTRAINT", source)


if __name__ == "__main__":
    unittest.main()
