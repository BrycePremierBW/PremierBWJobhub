"""Native phone navigation for Premier Brushworks JobHub.

A real Streamlit selectbox is rendered immediately before the app's main sidebar
radio. It lives in a keyed container so mobile CSS can target it reliably. The
normal sidebar radio remains unchanged on desktop.
"""

from __future__ import annotations

import sys
from typing import Any


_MOBILE_TOP_NAV_CSS = """
<style id="pb-native-mobile-top-navigation-v4">
.st-key-pb_mobile_top_navigation {
    display: none !important;
}

@media (max-width: 768px) {
    .st-key-pb_mobile_top_navigation {
        display: block !important;
        position: sticky !important;
        top: calc(0.35rem + env(safe-area-inset-top)) !important;
        z-index: 2147483640 !important;
        margin: 0 0 0.75rem 0 !important;
        padding: 0.45rem !important;
        border: 1px solid rgba(122,104,86,0.22) !important;
        border-radius: 13px !important;
        background: rgba(255,255,255,0.98) !important;
        box-shadow: 0 4px 16px rgba(0,0,0,0.16) !important;
    }

    .st-key-pb_mobile_top_navigation [data-testid="stSelectbox"] {
        margin: 0 !important;
    }

    .st-key-pb_mobile_top_navigation [data-testid="stSelectbox"] label {
        display: none !important;
    }

    .st-key-pb_mobile_top_navigation [data-baseweb="select"],
    .st-key-pb_mobile_top_navigation [data-baseweb="select"] > div {
        width: 100% !important;
        min-height: 44px !important;
        border-radius: 10px !important;
        font-weight: 700 !important;
    }

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


def install_mobile_top_navigation_guard() -> bool:
    st = sys.modules.get("streamlit")
    if st is None:
        return False

    sidebar = getattr(st, "sidebar", None)
    original_radio = getattr(sidebar, "radio", None)
    if original_radio is None or getattr(original_radio, "_pb_native_mobile_top_nav_v4", False):
        return False

    css_rendered = False

    def pb_radio(label: Any, options: Any, *args: Any, **kwargs: Any):
        nonlocal css_rendered
        option_list = list(options) if options is not None else []
        key = kwargs.get("key")

        if key == "main_menu" and option_list:
            if not css_rendered:
                css_rendered = True
                st.markdown(_MOBILE_TOP_NAV_CSS, unsafe_allow_html=True)

            state = st.session_state
            current = state.get("main_menu")
            if current not in option_list:
                current = option_list[0]
                state["main_menu"] = current

            mobile_key = "pb_mobile_top_main_menu"
            if state.get(mobile_key) not in option_list:
                state[mobile_key] = current
            elif state.get(mobile_key) != current:
                state[mobile_key] = current

            def sync_mobile_choice() -> None:
                selected = state.get(mobile_key)
                if selected in option_list:
                    state["main_menu"] = selected

            try:
                mobile_container = st.container(key="pb_mobile_top_navigation")
            except TypeError:
                mobile_container = st.container()

            with mobile_container:
                st.selectbox(
                    "JobHub menu",
                    option_list,
                    key=mobile_key,
                    on_change=sync_mobile_choice,
                    label_visibility="collapsed",
                )

        return original_radio(label, option_list, *args, **kwargs)

    pb_radio._pb_native_mobile_top_nav_v4 = True
    pb_radio._pb_original_radio = original_radio
    sidebar.radio = pb_radio
    return True
