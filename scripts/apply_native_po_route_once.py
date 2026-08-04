from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "pb_jobhub_app.py"
INIT = ROOT / "jobhub" / "__init__.py"
TEST = ROOT / "tests" / "test_po_upload_native_guard.py"


app = APP.read_text(encoding="utf-8")
menu_anchor = '        "Job Folders",\n        "Estimating",'
menu_replacement = '        "Job Folders",\n        "Upload PO",\n        "Estimating",'
menu_matches = app.count(menu_anchor)
if menu_matches != 2:
    raise RuntimeError(
        f"Expected two manager/admin menu anchors, found {menu_matches}."
    )
app = app.replace(menu_anchor, menu_replacement, 2)

route_anchor = '''elif menu == "Job Folders":
    job_folders_page()


elif menu == "Dashboard":
    render_operational_dashboard()
'''
route_replacement = '''elif menu == "Job Folders":
    job_folders_page()


elif menu == "Upload PO":
    from jobhub.po_upload_native_guard import render_native_po_upload_page

    render_native_po_upload_page()


elif menu == "Dashboard":
    render_operational_dashboard()
'''
route_matches = app.count(route_anchor)
if route_matches != 1:
    raise RuntimeError(f"Expected one main router anchor, found {route_matches}.")
app = app.replace(route_anchor, route_replacement, 1)
APP.write_text(app, encoding="utf-8")


init_source = INIT.read_text(encoding="utf-8")
init_source = init_source.replace(
    "from .po_upload_direct_route_guard import install_po_upload_direct_route_guard\n",
    "",
)
init_source = init_source.replace(
    "# The deterministic PO route must be installed after mobile navigation so it is\n"
    "# the final owner of both desktop and mobile main-menu selections.\n"
    "install_mobile_top_navigation_guard()\n"
    "install_po_upload_direct_route_guard()\n",
    "# Upload PO is now a first-class route in pb_jobhub_app.py. Mobile navigation\n"
    "# reads that same native menu instead of relying on a radio/session wrapper.\n"
    "install_mobile_top_navigation_guard()\n",
)
if "install_po_upload_direct_route_guard" in init_source:
    raise RuntimeError("Direct PO route wrapper is still installed in jobhub/__init__.py")
INIT.write_text(init_source, encoding="utf-8")


TEST.write_text(
    '''from __future__ import annotations

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

    def test_upload_po_is_a_first_class_main_app_route(self):
        source = (ROOT / "pb_jobhub_app.py").read_text(encoding="utf-8")
        menu_anchor = '        "Job Folders",\\n        "Upload PO",\\n        "Estimating",'
        self.assertEqual(source.count(menu_anchor), 2)
        self.assertIn('elif menu == "Upload PO":', source)
        self.assertIn(
            "from jobhub.po_upload_native_guard import render_native_po_upload_page",
            source,
        )
        self.assertIn("render_native_po_upload_page()", source)

    def test_startup_does_not_install_po_radio_or_session_wrapper(self):
        source = (ROOT / "jobhub" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("install_po_upload_native_guard()", source)
        self.assertIn("install_mobile_top_navigation_guard()", source)
        self.assertNotIn("install_po_upload_direct_route_guard", source)
        self.assertNotIn("install_po_upload_scope_return_guard()", source)
        self.assertNotIn("install_po_upload_split_guard()", source)

    def test_native_page_does_not_call_schema_migration(self):
        source = (ROOT / "jobhub" / "po_upload_native_guard.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("_ensure_schema()", source)
        self.assertNotIn("ALTER TABLE", source)
        self.assertNotIn("DROP CONSTRAINT", source)


if __name__ == "__main__":
    unittest.main()
''',
    encoding="utf-8",
)

print("Applied native Upload PO route directly to pb_jobhub_app.py")
