"""Provide a deterministic native Upload PO route for desktop and mobile.

The legacy PO route depended on several radio-widget wrappers installed in a
particular order. This final wrapper is installed after mobile navigation and
owns only the two real main-navigation widgets. Selecting Upload PO therefore
always renders the native page directly, without relying on the old route guard.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from .po_upload_native_guard import render_native_po_upload_page


PO_UPLOAD_LABEL = "Upload PO"
PATCH_MARKER = "_pb_po_upload_direct_route_v1"
MAIN_NAV_KEYS = {"main_menu", "pb_mobile_app_main_menu"}
MAIN_NAV_MARKERS = {"Dashboard", "Jobs", "Job Folders", "Site Operations"}


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


def _render_live_badge(st: Any) -> None:
    try:
        commit = str(os.getenv("RENDER_GIT_COMMIT", "") or "").strip()
        build = commit[:8] if commit else "local"
        st.sidebar.caption(f"Live build · {build} · direct PO route")
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
    """Install after mobile navigation so this is the final route owner."""
    st = sys.modules.get("streamlit")
    if st is None:
        return False
    installed = _patch_radio(st, st)
    sidebar = getattr(st, "sidebar", None)
    if sidebar is not None:
        installed = _patch_radio(sidebar, st, show_badge=True) or installed
    return installed
