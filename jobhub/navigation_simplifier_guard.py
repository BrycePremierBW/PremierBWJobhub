"""Simplify JobHub's top-level management navigation.

Dashboard, Control Centre and Operations Hub grew into three overlapping entry
points.  This guard keeps one top-level Dashboard and exposes the two specialist
views as modes inside that dashboard.  Existing renderers are reused unchanged,
so no operational capability is removed.

The guard also keeps the native mobile navigation in sync: it is installed after
the mobile navigation wrapper so phones receive the same filtered option list as
desktop.
"""
from __future__ import annotations

import sys
from typing import Any


HIDDEN_TOP_LEVEL = {"Control Centre", "Operations Hub"}
DASHBOARD_MODE_KEY = "pb_dashboard_view_mode"
PATCH_MARKER = "_pb_navigation_simplifier_guard"


def _st() -> Any:
    return sys.modules.get("streamlit")


def _app_attr(name: str, default: Any = None) -> Any:
    for module_name in ("__main__", "pb_jobhub_app"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, name):
            return getattr(module, name)
    return default


def _role(st: Any) -> str:
    current_role = _app_attr("current_role")
    if callable(current_role):
        try:
            return str(current_role() or "").strip().lower()
        except Exception:
            pass
    try:
        return str((st.session_state.get("user") or {}).get("role") or "").strip().lower()
    except Exception:
        return ""


def filtered_main_menu(options: Any, role: str = "admin") -> list[Any]:
    """Return the intentionally short top-level menu.

    Purchase-order visibility is also defensive here so a manager cannot retain
    an old Upload PO top-level route while the dedicated PO permission guard is
    loading.
    """
    values = list(options or [])
    result: list[Any] = []
    for value in values:
        text = str(value)
        if text in HIDDEN_TOP_LEVEL:
            continue
        if text == "Upload PO" and str(role).strip().lower() != "admin":
            continue
        result.append(value)
    return result


def _patch_sidebar_radio(st: Any) -> bool:
    sidebar = getattr(st, "sidebar", None)
    original = getattr(sidebar, "radio", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    def simplified_radio(label: Any, options: Any, *args: Any, **kwargs: Any):
        option_list = list(options or [])
        if str(kwargs.get("key") or "") == "main_menu":
            role = _role(st)
            option_list = filtered_main_menu(option_list, role)
            state = st.session_state
            prior = str(state.get("main_menu") or "")
            if prior in HIDDEN_TOP_LEVEL:
                state[DASHBOARD_MODE_KEY] = prior
                state["main_menu"] = "Dashboard"
            if state.get("main_menu") not in option_list and option_list:
                state["main_menu"] = "Dashboard" if "Dashboard" in option_list else option_list[0]
            mobile_key = "pb_mobile_app_main_menu"
            if state.get(mobile_key) not in option_list and option_list:
                state[mobile_key] = state.get("main_menu") or option_list[0]
        return original(label, option_list, *args, **kwargs)

    simplified_radio._pb_original_radio = original
    setattr(simplified_radio, PATCH_MARKER, True)
    sidebar.radio = simplified_radio
    return True


def _render_dashboard_switcher(st: Any) -> None:
    role = _role(st)
    if role not in {"admin", "manager"}:
        return

    state = st.session_state
    current = str(state.get(DASHBOARD_MODE_KEY) or "Overview")
    if current not in {"Overview", "Operations Hub", "Control Centre"}:
        current = "Overview"
        state[DASHBOARD_MODE_KEY] = current

    st.caption("One home for management — choose the level of detail you need.")
    mode = st.radio(
        "Dashboard view",
        ["Overview", "Operations Hub", "Control Centre"],
        index=["Overview", "Operations Hub", "Control Centre"].index(current),
        horizontal=True,
        key=DASHBOARD_MODE_KEY,
        label_visibility="collapsed",
    )

    if mode == "Operations Hub":
        renderer = _app_attr("render_operations_hub")
        context_builder = _app_attr("jobhub_enterprise_context")
        if callable(renderer) and callable(context_builder):
            renderer(context_builder())
            st.stop()
        st.warning("Operations tools are temporarily unavailable. Use Overview and try again.")
        return

    if mode == "Control Centre":
        renderer = _app_attr("control_centre_page")
        if callable(renderer):
            renderer()
            st.stop()
        st.warning("Control Centre tools are temporarily unavailable. Use Overview and try again.")


def _patch_subheader(st: Any) -> bool:
    original = getattr(st, "subheader", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    rendering_switcher = False

    def simplified_subheader(body: Any, *args: Any, **kwargs: Any):
        nonlocal rendering_switcher
        result = original(body, *args, **kwargs)
        if str(body or "").strip() == "Operations Dashboard" and not rendering_switcher:
            try:
                rendering_switcher = True
                _render_dashboard_switcher(st)
            finally:
                rendering_switcher = False
        return result

    simplified_subheader._pb_original_subheader = original
    setattr(simplified_subheader, PATCH_MARKER, True)
    st.subheader = simplified_subheader
    return True


def install_navigation_simplifier_guard() -> bool:
    st = _st()
    if st is None:
        return False
    return bool(_patch_sidebar_radio(st) or _patch_subheader(st))
