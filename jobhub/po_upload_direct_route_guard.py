"""Provide a deterministic native Upload PO route for desktop and mobile.

The main application validates its hard-coded menu list before Streamlit renders
the navigation widget.  ``Upload PO`` is injected by this guard, so a normal
Streamlit rerun can otherwise mistake the selected route for an invalid value
and replace it with Dashboard before the PO page is reached.

This guard is installed before the application runs.  It preserves the real
Upload PO session value during that validation pass, then owns the final desktop
and mobile radio calls and renders the native page directly.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from .po_upload_native_guard import render_native_po_upload_page


PO_UPLOAD_LABEL = "Upload PO"
PATCH_MARKER = "_pb_po_upload_direct_route_v2"
SESSION_GET_PATCH_MARKER = "_pb_po_upload_direct_route_session_get_v2"
MAIN_NAV_KEYS = {"main_menu", "pb_mobile_app_main_menu"}
MAIN_NAV_MARKERS = {"Dashboard", "Jobs", "Job Folders", "Site Operations"}

# During the application's hard-coded menu validation, report a valid menu value
# without changing the real stored Upload PO selection.  The subsequent radio
# widget still reads the real value and routes to the native PO page.
RESET_SAFE_VALUES = {
    "main_menu": "Dashboard",
    "pb_mobile_app_main_menu": "Dashboard",
}


def _labels(options: Any) -> list[Any]:
    try:
        return list(options)
    except Exception:
        return []


def _is_main_navigation(label: Any, key: Any, options: Any) -> bool:
    if str(key or "") in MAIN_NAV_KEYS:
        return True
    labels = {str(item) for item in _labels(options)}
    return str(label or "") in {"Menu", "JobHub navigation"} and len(
        labels.intersection(MAIN_NAV_MARKERS)
    ) >= 2


def _with_upload_option(options: Any) -> list[Any]:
    values = _labels(options)
    if PO_UPLOAD_LABEL not in [str(item) for item in values]:
        values.append(PO_UPLOAD_LABEL)
    return values


def _install_session_state_reset_guard(st: Any) -> bool:
    """Prevent the app's invalid-menu fallback from wiping Upload PO."""
    try:
        state_cls = type(st.session_state)
        original_get = getattr(state_cls, "get", None)
    except Exception:
        return False

    if original_get is None or getattr(original_get, SESSION_GET_PATCH_MARKER, False):
        return False

    def guarded_get(self: Any, key: Any, default: Any = None) -> Any:
        value = original_get(self, key, default)
        key_text = str(key or "")
        if key_text in RESET_SAFE_VALUES and str(value) == PO_UPLOAD_LABEL:
            return RESET_SAFE_VALUES[key_text]
        return value

    guarded_get._pb_original_get = original_get
    setattr(guarded_get, SESSION_GET_PATCH_MARKER, True)
    setattr(state_cls, "get", guarded_get)
    return True


def _render_live_badge(st: Any) -> None:
    try:
        commit = str(os.getenv("RENDER_GIT_COMMIT", "") or "").strip()
        build = commit[:8] if commit else "local"
        st.sidebar.caption(f"Live build · {build} · direct PO route v2")
    except Exception:
        pass


def _render_and_stop(st: Any) -> None:
    render_native_po_upload_page()
    stop = getattr(st, "stop", None)
    if callable(stop):
        stop()


def _patch_radio(owner: Any, st: Any, *, show_badge: bool = False) -> bool:
    original = getattr(owner, "radio", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    def wrapper(label: Any, options: Any, *args: Any, **kwargs: Any):
        key = kwargs.get("key")
        is_main = _is_main_navigation(label, key, options)
        routed_options = _with_upload_option(options) if is_main else options
        if is_main and show_badge:
            _render_live_badge(st)
        result = original(label, routed_options, *args, **kwargs)
        if is_main and str(result) == PO_UPLOAD_LABEL:
            _render_and_stop(st)
        return result

    setattr(wrapper, PATCH_MARKER, True)
    wrapper._pb_original_radio = original
    owner.radio = wrapper
    return True


def install_po_upload_direct_route_guard() -> bool:
    """Install the state protection and final desktop/mobile PO route."""
    st = sys.modules.get("streamlit")
    if st is None:
        return False

    installed = _install_session_state_reset_guard(st)
    installed = _patch_radio(st, st) or installed
    sidebar = getattr(st, "sidebar", None)
    if sidebar is not None:
        installed = _patch_radio(sidebar, st, show_badge=True) or installed
    return installed
