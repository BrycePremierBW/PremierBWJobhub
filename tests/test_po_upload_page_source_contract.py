from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PoUploadPageSourceContractTests(unittest.TestCase):
    def test_legacy_page_still_routes_schema_calls_through_patchable_helper(self):
        source = (ROOT / "jobhub" / "po_upload_guard.py").read_text(encoding="utf-8")
        self.assertIn("def _ensure_schema()", source)
        self.assertIn("def render_po_upload_page()", source)
        self.assertIn("_ensure_schema()", source)

    def test_split_page_routes_constraint_work_through_patchable_helper(self):
        source = (ROOT / "jobhub" / "po_upload_split_guard.py").read_text(encoding="utf-8")
        self.assertIn("def _relax_po_number_uniqueness", source)
        self.assertIn("def _record_po_line", source)
        self.assertIn("_relax_po_number_uniqueness(po)", source)


if __name__ == "__main__":
    unittest.main()
