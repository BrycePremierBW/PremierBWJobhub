from pathlib import Path
import re
import unittest


APP_SOURCE = Path(__file__).resolve().parents[1] / "pb_jobhub_app.py"


class NavigationVisibilityRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = APP_SOURCE.read_text(encoding="utf-8")

    def test_sidebar_has_independent_vertical_scroll(self):
        self.assertIn("PB_JOBHUB_SIDEBAR_SCROLL_GUARD_V3", self.source)
        self.assertRegex(
            self.source,
            r'\[data-testid="stSidebarContent"\][^{]*\{[^}]*overflow-y:\s*auto\s*!important',
        )
        self.assertIn("height: 100dvh !important", self.source)
        self.assertIn("-webkit-overflow-scrolling: touch !important", self.source)
        self.assertIn("touch-action: pan-y !important", self.source)
        self.assertIn("env(safe-area-inset-bottom)", self.source)

    def test_mobile_navigation_closes_sidebar_from_top_level_page(self):
        start = self.source.index("def pb_scroll_sidebar_to_top")
        end = self.source.index("def pb_page_header", start)
        helper = self.source[start:end]
        self.assertIn("st.html(", helper)
        self.assertIn("unsafe_allow_javascript=True", helper)
        self.assertNotIn("st.iframe(", helper)
        self.assertIn("closeMobileSidebar", helper)
        self.assertIn('stSidebarCollapseButton', helper)
        self.assertIn("button[aria-label=\"Close sidebar\"]", helper)
        self.assertIn("window.matchMedia('(max-width: 768px)')", helper)

    def test_submenus_use_visible_radio_lists_not_clipped_dropdowns(self):
        # Each sub-menu is rendered through the shared persistent-radio helper,
        # which always uses st.sidebar.radio (a fully visible list) rather than
        # a clipped sidebar selectbox.
        for label in (
            "Management Section",
            "Estimating Section",
            "Site Section",
            "AI Section",
        ):
            self.assertRegex(
                self.source,
                rf"_render_persistent_submenu_radio\(\s*\"[a-z_]+\",\s*[a-z_]+,\s*\"{re.escape(label)}\"",
            )
            self.assertNotRegex(
                self.source,
                rf"st\.sidebar\.selectbox\(\s*\"{re.escape(label)}\"",
            )
        start = self.source.index("def _render_persistent_submenu_radio")
        end = self.source.index("reports_menu_map", start)
        helper = self.source[start:end]
        self.assertIn("st.sidebar.radio(", helper)
        self.assertNotIn("st.sidebar.selectbox(", helper)

    def test_submenu_selections_survive_top_level_menu_switches(self):
        # Streamlit deletes the session value of any radio not rendered in a run,
        # so switching top-level menus used to snap each sub-menu back to its first
        # option. The shared helper must persist each selection and restore it.
        self.assertIn("PB_JOBHUB_SUBMENU_STATE_FIX", self.source)
        start = self.source.index("def _render_persistent_submenu_radio")
        end = self.source.index("reports_menu_map", start)
        helper = self.source[start:end]
        self.assertIn("_PERSISTED_SUBMENU_KEYS[widget_key]", helper)
        self.assertIn("state[widget_key] = current", helper)
        self.assertIn("state[_PERSISTED_SUBMENU_KEYS[widget_key]] = selected", helper)

    def test_reset_menu_clears_persisted_submenu_selections(self):
        start = self.source.index("sidebar_reset_target")
        end = self.source.index("hidden_route_options", start)
        reset_block = self.source[start:end]
        for persisted_key in (
            "_pb_persisted_management_menu",
            "_pb_persisted_estimating_menu",
            "_pb_persisted_site_operations_menu",
            "_pb_persisted_ai_menu",
        ):
            self.assertIn(f'"{persisted_key}"', reset_block)

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
