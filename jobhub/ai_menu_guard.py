"""Hide AI Assistant navigation from Premier Brushworks JobHub.

The main Streamlit app still contains the historical AI routes.  This guard
removes the AI menu choices from Streamlit radio widgets before they render, so
users cannot open those sections from the sidebar.
"""

from __future__ import annotations

import sys
from typing import Any


AI_MENU_VALUES = {"AI Assistant", "JobHub AI Assistant", "App Builder AI"}
AI_SESSION_KEYS = {"ai_menu"}


def _without_ai_options(options: Any) -> Any:
    try:
        filtered = [item for item in list(options) if str(item) not in AI_MENU_VALUES]
    except Exception:
        return options
    return filtered if len(filtered) != len(list(options)) else options


def _clear_ai_state(st: Any, removed_options: Any) -> None:
    try:
        removed_values = {str(value) for value in removed_options}
        if str(st.session_state.get("main_menu") or "") in AI_MENU_VALUES:
            st.session_state["main_menu"] = "Dashboard"
        if str(st.session_state.get("go_to_menu") or "") in AI_MENU_VALUES:
            st.session_state.pop("go_to_menu", None)
        for key in AI_SESSION_KEYS:
            if key in st.session_state:
                st.session_state.pop(key, None)
        for key, value in list(st.session_state.items()):
            if str(value) in removed_values and str(value) in AI_MENU_VALUES:
                st.session_state.pop(key, None)
    except Exception:
        return


def install_ai_menu_guard() -> bool:
    st = sys.modules.get("streamlit")
    if st is None:
        return False

    installed = False

    original_sidebar_radio = getattr(getattr(st, "sidebar", None), "radio", None)
    if original_sidebar_radio is not None and not getattr(original_sidebar_radio, "_pb_ai_menu_guard", False):
        def pb_sidebar_radio_no_ai(label: Any, options: Any, *args: Any, **kwargs: Any):
            clean_options = _without_ai_options(options)
            if clean_options is not options:
                _clear_ai_state(st, options)
                if not clean_options:
                    clean_options = ["Dashboard"]
            return original_sidebar_radio(label, clean_options, *args, **kwargs)

        pb_sidebar_radio_no_ai._pb_ai_menu_guard = True
        pb_sidebar_radio_no_ai._pb_original_radio = original_sidebar_radio
        st.sidebar.radio = pb_sidebar_radio_no_ai
        installed = True

    delta_module = sys.modules.get("streamlit.delta_generator")
    delta_cls = getattr(delta_module, "DeltaGenerator", None) if delta_module is not None else None
    if delta_cls is not None:
        dg_radio = getattr(delta_cls, "radio", None)
        if dg_radio is not None and not getattr(dg_radio, "_pb_ai_menu_guard", False):
            def pb_dg_radio_no_ai(self: Any, label: Any, options: Any, *args: Any, **kwargs: Any):
                clean_options = _without_ai_options(options)
                if clean_options is not options:
                    _clear_ai_state(st, options)
                    if not clean_options:
                        clean_options = ["Dashboard"]
                return dg_radio(self, label, clean_options, *args, **kwargs)

            pb_dg_radio_no_ai._pb_ai_menu_guard = True
            pb_dg_radio_no_ai._pb_original_radio = dg_radio
            delta_cls.radio = pb_dg_radio_no_ai
            installed = True

    return installed
