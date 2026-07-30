from pathlib import Path
import re
import unittest


APP_SOURCE = Path(__file__).resolve().parents[1] / "pb_jobhub_app.py"


class NavigationVisibilityRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = APP_SOURCE.read_text(encoding="utf-8")

    def test_sidebar_has_independent_vertical_scroll(self):
        self.assertIn("PB_JOBHUB_SIDEBAR_SCROLL_GUARD_V2", self.source)
        self.assertRegex(
            self.source,
            r'\[data-testid="stSidebarContent"\][^{]*\{[^}]*overflow-y:\s*auto\s*!important',
        )
        self.assertIn("padding-bottom: 2rem !important", self.source)

    def test_submenus_use_visible_radio_lists_not_clipped_dropdowns(self):
        for label in (
            "Management Section",
            "Estimating Section",
            "Site Section",
            "AI Section",
        ):
            self.assertRegex(
                self.source,
                rf"st\.sidebar\.radio\(\s*\"{re.escape(label)}\"",
            )
            self.assertNotRegex(
                self.source,
                rf"st\.sidebar\.selectbox\(\s*\"{re.escape(label)}\"",
            )

    def test_radio_labels_can_wrap_without_clipping(self):
        self.assertRegex(
            self.source,
            r'\[role="radiogroup"\]\s+label\s+p\s*\{[^}]*overflow-wrap:\s*anywhere',
        )

    def test_mobile_sidebar_width_is_viewport_bounded(self):
        self.assertIn("width: min(92vw, 360px) !important", self.source)
        self.assertIn("max-height: 56vh !important", self.source)


if __name__ == "__main__":
    unittest.main()
