"""Native phone-only top navigation for Premier Brushworks JobHub.

This guard mirrors the main sidebar radio with a normal Streamlit selectbox. The
selectbox is hidden on desktop with CSS, so desktop routing remains unchanged.
"""

from __future__ import annotations

import sys
from typing import Any, Sequence


_MOBILE_TOP_NAV_CSS = """
<style id="pb-native-mobile-top-navigation-v2">
/* Hidden on desktop. Streamlit gives keyed containers a stable st-key-* class. */
.st-key-pb_mobile_top_navigation {
    display: none !important;
}

@media (max-width: 768px) {
    .st-key-pb_mobile_top_navigation {
        display: block !important;
        position: fixed !important;
        top: calc(0.42rem + env(safe-area-inset-top)) !important;
        left: calc(3.25rem + env(safe-area-inset-left)) !important;
        right: calc(0.55rem + env(safe-area-inset-right)) !important;
        z-index: 2147483646 !important;
        margin: 0 !important;
        padding: 0 !important;
    }

    .st-key-pb_mobile_top_navigation [data-testid="stSelectbox"] {
        margin: 0 !important;
    }

    .st-key-pb_mobile_top_navigation [data-baseweb="select"] > div {
        min-height: 42px !important;
        border: 1px solid rgba(122,104,86,0.24) !important;
        border-radius: 12px !important;
        background: rgba(255,255,255,0.98) !important;
        box-shadow: 0 3px 14px rgba(0,0,0,0.16) !important;
        font-weight: 700 !important;
    }

    .st-key-pb_mobile_top_navigation [data-baseweb="select"] * {
        color: #1f1f1f !important;
        white-space: normal !important;
        overflow-wrap: anywhere !important;
    }

    .block-container {
        padding-top: calc(3.75rem + env(safe-area-inset-top)) !important;
    }
}
</style>
"""


def _normalise_options(options: Sequence[Any]) -> list[Any]:
    return list(options or [])


def install_mobile_top_navigation_guard() -> bool:
    """Render a native top selector whenever JobHub creates its main Menu radio."""
    st = sys.modules.get("streamlit")
    if st is None:
        return False

    sidebar = getattr(st, "sidebar", None)
    original_radio = getattr(sidebar, "radio", None)
    if original_radio is None or getattr(original_radio, "_pb_mobile_top_navigation_guard", False):
        return False

    css_done = False

    def pb_sidebar_radio(label: str, options: Sequence[Any], *args: Any, **kwargs: Any):
        nonlocal css_done

        option_list = _normalise_options(options)
        key = kwargs.get("key")
        is_main_menu = str(label) == "Menu" and key == "main_menu" and bool(option_list)

        if is_main_menu:
            if not css_done:
                css_done = True
                try:
                    st.markdown(_MOBILE_TOP_NAV_CSS, unsafe_allow_html=True)
                except Exception:
                    pass

            state = st.session_state
            main_key = "main_menu"
            mobile_key = "pb_mobile_top_menu"
            sync_key = "pb_mobile_top_menu_last_synced"

            fallback_index = int(kwargs.get("index", 0) or 0)
            fallback_index = min(max(fallback_index, 0), len(option_list) - 1)
            fallback = option_list[fallback_index]

            main_value = state.get(main_key, fallback)
            if main_value not in option_list:
                main_value = fallback
                state[main_key] = main_value

            mobile_value = state.get(mobile_key, main_value)
            if mobile_value not in option_list:
                mobile_value = main_value
                state[mobile_key] = mobile_value

            last_synced = state.get(sync_key, main_value)

            # Detect which control changed since the previous rerun and mirror it
            # before either widget is instantiated on this run.
            if main_value != last_synced and mobile_value == last_synced:
                state[mobile_key] = main_value
                mobile_value = main_value
            elif mobile_value != last_synced and main_value == last_synced:
                state[main_key] = mobile_value
                main_value = mobile_value
            elif main_value != mobile_value:
                # Ambiguous state: preserve the real sidebar value.
                state[mobile_key] = main_value
                mobile_value = main_value

            try:
                with st.container(key="pb_mobile_top_navigation"):
                    mobile_choice = st.selectbox(
                        "JobHub navigation",
                        option_list,
                        key=mobile_key,
                        label_visibility="collapsed",
                    )
            except TypeError:
                # Compatibility fallback for Streamlit versions without keyed containers.
                st.markdown('<div class="pb-mobile-top-navigation-fallback">', unsafe_allow_html=True)
                mobile_choice = st.selectbox(
                    "JobHub navigation",
                    option_list,
                    key=mobile_key,
                    label_visibility="collapsed",
                )
                st.markdown("</div>", unsafe_allow_html=True)

            if mobile_choice in option_list and mobile_choice != state.get(main_key):
                state[main_key] = mobile_choice
            state[sync_key] = state.get(main_key, mobile_choice)

        return original_radio(label, option_list, *args, **kwargs)

    pb_sidebar_radio._pb_mobile_top_navigation_guard = True
    pb_sidebar_radio._pb_original_radio = original_radio
    sidebar.radio = pb_sidebar_radio
    return True
