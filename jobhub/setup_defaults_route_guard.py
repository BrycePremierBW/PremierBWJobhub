"""Keep the JobHub Setup / Edit Defaults route from being reset to Dashboard.

The setup page is injected into existing Streamlit radios. Some hard-coded menu
validation in the main app treats unknown menu values as invalid and silently
replaces them with Dashboard before the injected page can render. This guard
uses the same safe-session pattern as the Upload PO and SWMS route guards.
"""

from __future__ import annotations

import sys
from typing import Any

from . import setup_defaults_guard


PATCH_MARKER = "_pb_setup_defaults_route_guard"

RESET_SAFE_VALUES = {
    "main_menu": "Dashboard",
    "management_menu": "Builders & Clients",
    "site_operations_menu": "Staff Scheduler",
    "estimating_menu": "Import / Create Job Pack",
    "ai_menu": "JobHub AI Assistant",
}


def _st() -> Any:
    return sys.modules.get("streamlit")


def _install_session_state_reset_guard(st: Any) -> bool:
    try:
        state_cls = type(st.session_state)
        original_get = getattr(state_cls, "get", None)
    except Exception:
        return False
    if original_get is None or getattr(original_get, PATCH_MARKER, False):
        return False

    def guarded_get(self: Any, key: Any, default: Any = None) -> Any:
        value = original_get(self, key, default)
        key_text = str(key or "")
        if key_text in RESET_SAFE_VALUES and str(value) == setup_defaults_guard.SETUP_LABEL:
            return RESET_SAFE_VALUES[key_text]
        return value

    guarded_get._pb_original_get = original_get
    guarded_get._pb_setup_defaults_route_guard = True
    setattr(state_cls, "get", guarded_get)
    return True


def _patch_show_page() -> bool:
    original_show_page = getattr(setup_defaults_guard, "_show_page", None)
    if original_show_page is None or getattr(original_show_page, PATCH_MARKER, False):
        return False

    def show_page_without_dashboard_reset(st: Any) -> None:
        st.session_state[setup_defaults_guard.SETUP_STATE_KEY] = True
        setup_defaults_guard.render_setup_defaults_page()
        st.stop()

    show_page_without_dashboard_reset._pb_setup_defaults_route_guard = True
    show_page_without_dashboard_reset._pb_original = original_show_page
    setup_defaults_guard._show_page = show_page_without_dashboard_reset
    return True


def install_setup_defaults_route_guard() -> bool:
    st = _st()
    if st is None:
        return False
    installed = _install_session_state_reset_guard(st)
    installed = _patch_show_page() or installed
    return installed
