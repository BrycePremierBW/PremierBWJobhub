from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = ROOT / "jobhub_lean" / "app.py"
MOBILE_SOURCE = ROOT / "jobhub_lean" / "mobile.py"


class NavigationVisibilityRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_source = APP_SOURCE.read_text(encoding="utf-8")
        cls.mobile_source = MOBILE_SOURCE.read_text(encoding="utf-8")

    def test_sidebar_has_independent_vertical_scroll(self):
        self.assertRegex(
            self.mobile_source,
            r'\[data-testid="stSidebarContent"\]\s*\{[^}]*overflow-y:\s*auto\s*!important',
        )
        self.assertIn("height: 100dvh !important", self.mobile_source)
        self.assertIn("-webkit-overflow-scrolling: touch !important", self.mobile_source)
        self.assertIn("touch-action: pan-y !important", self.mobile_source)
        self.assertIn("env(safe-area-inset-bottom)", self.mobile_source)

    def test_mobile_navigation_closes_sidebar_from_top_level_page(self):
        self.assertIn("install_mobile_shell()", self.app_source)
        self.assertIn("st.html(", self.mobile_source)
        self.assertIn("unsafe_allow_javascript=True", self.mobile_source)
        self.assertNotIn("st.iframe(", self.mobile_source)
        self.assertIn("stSidebarCollapseButton", self.mobile_source)
        self.assertIn('button[aria-label="Close sidebar"]', self.mobile_source)
        self.assertIn("window.matchMedia('(max-width: 768px)')", self.mobile_source)

    def test_navigation_is_one_visible_radio_list(self):
        self.assertIn('st.sidebar.radio("Navigation"', self.app_source)
        self.assertNotIn("st.sidebar.selectbox", self.app_source)
        self.assertIn("_menu_options()", self.app_source)

    def test_radio_labels_can_wrap_without_clipping(self):
        self.assertRegex(
            self.mobile_source,
            r'\[role="radiogroup"\]\s+label\s+p\s*\{[^}]*overflow-wrap:\s*anywhere',
        )

    def test_mobile_sidebar_width_is_viewport_bounded(self):
        self.assertIn("width: min(92vw, 360px) !important", self.mobile_source)
        self.assertIn("max-height: 56vh !important", self.mobile_source)

    def test_pwa_viewport_metadata_is_installed(self):
        self.assertIn("viewport-fit=cover", self.mobile_source)
        self.assertIn("manifest.webmanifest", self.mobile_source)
        self.assertIn("apple-mobile-web-app-capable", self.mobile_source)


if __name__ == "__main__":
    unittest.main()
