"""Responsive native mobile navigation for Premier Brushworks JobHub.

The app's real ``main_menu`` sidebar radio remains the desktop source of truth.
Immediately before that widget is created, this guard renders a second native
Streamlit radio in the main page. CSS turns it into a horizontally scrollable app
header on phones and hides the Streamlit drawer entirely at mobile widths.
"""

from __future__ import annotations

import sys
from typing import Any


_MENU_ICONS = {
    "Dashboard": "🏠",
    "Control Centre": "🎯",
    "Jobs": "🧾",
    "Job Folders": "📁",
    "Estimating": "💰",
    "Site Operations": "🛠️",
    "Reports": "📊",
    "Management": "⚙️",
    "AI Assistant": "🤖",
    "3D Building Mapper": "🏗️",
    "Building Progress Mapper": "🗺️",
    "Employee Portal": "👷",
}


_MOBILE_APP_NAV_CSS = """
<style id="pb-native-mobile-app-navigation-v6">
/* Desktop keeps the established sidebar navigation. */
.st-key-pb_mobile_app_navigation {
    display: none !important;
}

@media (max-width: 768px) {
    /* Native phone header. */
    .st-key-pb_mobile_app_navigation {
        display: block !important;
        position: sticky !important;
        top: calc(0.25rem + env(safe-area-inset-top)) !important;
        z-index: 2147483640 !important;
        width: 100% !important;
        margin: 0 0 0.8rem 0 !important;
        padding: 0.42rem 0.38rem !important;
        border: 1px solid rgba(122,104,86,0.20) !important;
        border-radius: 14px !important;
        background: rgba(255,255,255,0.98) !important;
        box-shadow: 0 5px 18px rgba(0,0,0,0.15) !important;
        backdrop-filter: blur(12px) !important;
        -webkit-backdrop-filter: blur(12px) !important;
        box-sizing: border-box !important;
    }

    .st-key-pb_mobile_app_navigation [data-testid="stRadio"] {
        margin: 0 !important;
        width: 100% !important;
    }

    .st-key-pb_mobile_app_navigation [data-testid="stRadio"] > label {
        display: none !important;
    }

    .st-key-pb_mobile_app_navigation [role="radiogroup"] {
        display: flex !important;
        flex-wrap: nowrap !important;
        gap: 0.34rem !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow-x: auto !important;
        overflow-y: hidden !important;
        padding: 0.08rem 0.04rem 0.28rem 0.04rem !important;
        scrollbar-width: none !important;
        overscroll-behavior-x: contain !important;
        -webkit-overflow-scrolling: touch !important;
        touch-action: pan-x !important;
    }

    .st-key-pb_mobile_app_navigation [role="radiogroup"]::-webkit-scrollbar {
        display: none !important;
    }

    .st-key-pb_mobile_app_navigation [role="radiogroup"] label {
        flex: 0 0 auto !important;
        width: auto !important;
        min-width: 92px !important;
        min-height: 48px !important;
        margin: 0 !important;
        padding: 0.5rem 0.62rem !important;
        border: 1px solid #e7ddd3 !important;
        border-radius: 11px !important;
        background: #f8f4ef !important;
        color: #2a2724 !important;
        box-sizing: border-box !important;
        justify-content: center !important;
        text-align: center !important;
        box-shadow: none !important;
    }

    .st-key-pb_mobile_app_navigation [role="radiogroup"] label:has([aria-checked="true"]),
    .st-key-pb_mobile_app_navigation [role="radiogroup"] label:has(input:checked) {
        background: #2b2520 !important;
        border-color: #2b2520 !important;
        color: #ffffff !important;
        box-shadow: 0 3px 10px rgba(0,0,0,0.18) !important;
    }

    .st-key-pb_mobile_app_navigation [role="radiogroup"] label p,
    .st-key-pb_mobile_app_navigation [role="radiogroup"] label span {
        color: inherit !important;
        font-size: 0.78rem !important;
        font-weight: 700 !important;
        line-height: 1.12 !important;
        white-space: nowrap !important;
    }

    .st-key-pb_mobile_app_navigation [role="radio"] > div:first-child,
    .st-key-pb_mobile_app_navigation input[type="radio"] {
        display: none !important;
    }

    /* Phones use the app header rather than Streamlit's drawer. */
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
        pointer-events: none !important;
    }

    [data-testid="stAppViewContainer"],
    [data-testid="stMain"],
    .main,
    .block-container {
        margin-left: 0 !important;
        left: 0 !important;
        width: 100% !important;
        max-width: 100vw !important;
        min-width: 0 !important;
    }

    .block-container {
        padding-top: calc(0.65rem + env(safe-area-inset-top)) !important;
        padding-left: max(0.7rem, env(safe-area-inset-left)) !important;
        padding-right: max(0.7rem, env(safe-area-inset-right)) !important;
    }
}
</style>
"""


def install_mobile_top_navigation_guard() -> bool:
    """Install the native phone header before JobHub creates its main menu."""
    st = sys.modules.get("streamlit")
    if st is None:
        return False

    sidebar = getattr(st, "sidebar", None)
    original_radio = getattr(sidebar, "radio", None)
    if original_radio is None or getattr(original_radio, "_pb_native_mobile_app_nav_v6", False):
        return False

    def pb_sidebar_radio(label: Any, options: Any, *args: Any, **kwargs: Any):
        option_list = list(options) if options is not None else []
        key = kwargs.get("key")

        if key == "main_menu" and option_list:
            # Streamlit clears rendered elements on every rerun. Re-render the CSS
            # every time the main menu is created so the drawer stays hidden after
            # a mobile navigation selection triggers a rerun.
            st.markdown(_MOBILE_APP_NAV_CSS, unsafe_allow_html=True)

            state = st.session_state
            current = state.get("main_menu")
            if current not in option_list:
                current = option_list[0]
                state["main_menu"] = current

            mobile_key = "pb_mobile_app_main_menu"
            if state.get(mobile_key) not in option_list:
                state[mobile_key] = current
            elif state.get(mobile_key) != current:
                state[mobile_key] = current

            def sync_mobile_navigation() -> None:
                selected = state.get(mobile_key)
                if selected in option_list:
                    state["main_menu"] = selected

            def format_mobile_option(option: Any) -> str:
                text = str(option)
                return f"{_MENU_ICONS.get(text, '•')} {text}"

            try:
                mobile_container = st.container(key="pb_mobile_app_navigation")
            except TypeError:
                mobile_container = st.container()

            with mobile_container:
                st.radio(
                    "JobHub navigation",
                    option_list,
                    key=mobile_key,
                    horizontal=True,
                    on_change=sync_mobile_navigation,
                    format_func=format_mobile_option,
                    label_visibility="collapsed",
                )

        return original_radio(label, option_list, *args, **kwargs)

    pb_sidebar_radio._pb_native_mobile_app_nav_v6 = True
    pb_sidebar_radio._pb_original_radio = original_radio
    sidebar.radio = pb_sidebar_radio
    return True
