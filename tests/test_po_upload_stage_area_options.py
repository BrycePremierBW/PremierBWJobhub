"""Smoke tests for PO upload stage-area usability."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class PoUploadStageAreaOptionsTests(unittest.TestCase):
    def test_scope_return_guard_source_parses(self):
        ast.parse(read("jobhub/po_upload_scope_return_guard.py"))

    def test_stage_area_contains_standard_scope_options_and_popup(self):
        source = read("jobhub/po_upload_scope_return_guard.py")
        required = [
            "QUICK_STAGE_OPTIONS",
            "Stage / area options guide",
            "Whole job",
            "DISPLAY_INTERNAL",
            "DISPLAY_EXTERNAL",
            "External - Upper scaff work",
            "External - Lower external",
            "External - Touch ups",
            "Custom / not listed",
            "_install_stage_options_guard",
            "_render_stage_area_popup",
        ]
        for marker in required:
            self.assertIn(marker, source)

    def test_calculation_basis_stays_separate_from_area_choice(self):
        source = read("jobhub/po_upload_scope_return_guard.py")
        required = [
            "Calculation basis",
            "Selected area / stage value",
            "Use Whole job value for full-job POs",
            "return getattr(po, \"BASIS_TOTAL_JOB\"",
            "return getattr(po, \"BASIS_MANUAL_SCOPE\"",
        ]
        for marker in required:
            self.assertIn(marker, source)


if __name__ == "__main__":
    unittest.main()
