"""Native phone navigation for Premier Brushworks JobHub.

This guard adds a real Streamlit selectbox immediately before JobHub renders its
``main_menu`` sidebar radio. CSS shows the selectbox on phones and hides it on
desktop. The sidebar radio remains the source of truth, so desktop routing is not
patched or replaced.
"""

from __future__ import annotations

import sys
from typing import Any


_MOBILE_TOP_NAV_CSS = """
<style id="pb-native-mobile-top-navigation-v3">
/* Hidden by default so desktop keeps the established sidebar-only layout. */
div[data-testid="stElementContainer"]:has(.pb-mobile-top-nav-marker) {
    display: none !important;
}

@media (max-width: 768px) {
    div[data-testid="stElementContainer"]:has(.pb-mobile-top-nav-marker) {
        display: block !important;
        position: sticky !important;
        top: calc(0.3rem + env(safe-area-inset-top)) !important;
        z-index: 2147483640 !important;
        margin: 0 0 0.7rem 0 !important;
        padding: 0.42rem !important;
        border: 1px solid rgba(122,104,86,0.22) !important;
        border-radius: 13px !important;
        background: rgba(255,255,255,0.97) !important;
        box-shadow: 0 4px 16px rgba(0,0,0,0.16) !important;
        backdrop-filter: blur(10px) !important;
        -webkit-backdrop-filter: blur(10px) !important;
    }

    div[data-testid="stElementContainer"]:has(.pb-mobile-top-nav-marker)
    div[data-baseweb="select"] {
        width: 100% !important;
    }

    div[data-testid="stElementContainer"]:has(.pb-mobile-top-nav-marker)
    [data-testid="stSelectbox"] label {
        display: none !important;
    }

    div[data-testid="stElementContainer"]:has(.pb-mobile-top-nav-marker)
    [data-baseweb="select"] > div {
        min-height: 44px !important;
        border-radius: 10px !important;
        font-weight: 700 !important;
    }

    /* The phone uses the top menu. Keep Streamlit's drawer off the canvas. */
    section[data-testid="stSidebar"] {
        display: none !important;
        visibility: hidden !important;
        pointer-events: none !important;
        width: 0 !important;
        min-width: 0 !important;
        max-width: 0 !important;
        transform: translateX(-120vw) !important;
    }

    [data-testid="collapsedControl"] {
        display: none !important;
        visibility: hidden !important;
    }

    [data-testid="stAppViewContainer"],
    [data-testid="stMain"],
    .main,
    .block-container {
        margin-left: 0 !important;
        left: 0 !important;
        width: 100vw !important;
        max-width: 100vw !important;
    }
}
</style>
"""


def _install_main_menu_radio_guard(streamlit_module: Any) -> bool:
    sidebar = getattr(streamlit_module, "sidebar", None)
    original_radio = getattr(sidebar, "radio", None)
    if original_radio is None or getattr(original_radio, "_pb_native_mobile_top_nav", False):
        return False

    def pb_radio(label: Any, options: Any, *args: Any, **kwargs: Any):
        key = kwargs.get("key")
        option_list = list(options) if options is not None else []

        if key == "main_menu" and option_list:
            state = streamlit_module.session_state
            current = state.get("main_menu")
            if current not in option_list:
                current = option_list[0]
                state["main_menu"] = current

            mobile_key = "pb_mobile_top_main_menu"
            if state.get(mobile_key) not in option_list:
                state[mobile_key] = current
            elif state.get(mobile_key) != current:
                # Keep external route changes reflected in the phone control.
                state[mobile_key] = current

            def sync_mobile_choice() -> None:
                selected = state.get(mobile_key)
                if selected in option_list:
                    state["main_menu"] = selected

            streamlit_module.markdown(
                '<span class="pb-mobile-top-nav-marker"></span>',
                unsafe_allow_html=True,
            )
            streamlit_module.selectbox(
                "JobHub menu",
                option_list,
                key=mobile_key,
                on_change=sync_mobile_choice,
                label_visibility="collapsed",
            )

        return original_radio(label, option_list, *args, **kwargs)

    pb_radio._pb_native_mobile_top_nav = True
    pb_radio._pb_original_radio = original_radio
    sidebar.radio = pb_radio
    return True


def _install_css_guard(streamlit_module: Any) -> bool:
    original_markdown = getattr(streamlit_module, "markdown", None)
    if original_markdown is None or getattr(original_markdown, "_pb_mobile_top_css_guard", False):
        return False

    css_done = False

    def pb_markdown(body: Any, *args: Any, **kwargs: Any):
        nonlocal css_done
        result = original_markdown(body, *args, **kwargs)
        if not css_done and isinstance(body, str) and "PB_JOBHUB_SIDEBAR_SCROLL_GUARD" in body:
            css_done = True
            original_markdown(_MOBILE_TOP_NAV_CSS, unsafe_allow_html=True)
        return result

    pb_markdown._pb_mobile_top_css_guard = True
    pb_markdown._pb_original_markdown = original_markdown
    streamlit_module.markdown = pb_markdown
    return True


def install_mobile_top_navigation_guard() -> bool:
    streamlit_module = sys.modules.get("streamlit")
    if streamlit_module is None:
        return False
    radio_installed = _install_main_menu_radio_guard(streamlit_module)
    css_installed = _install_css_guard(streamlit_module)
    return bool(radio_installed or css_installed)
