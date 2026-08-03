"""Make SWMS controls visible in employee and admin JobHub views."""

from __future__ import annotations

import sys
from typing import Any

from . import swms_guard

ADMIN_SWMS_LABEL = "SWMS / Safety Sign-off"
ADMIN_SWMS_STATE_KEY = "_pb_show_admin_swms_page"
DASHBOARD_PATCH_KEY = "_pb_swms_dashboard_route_guard"


def _safe_rerun(st: Any) -> None:
    rerun = swms_guard._app("pb_rerun") or swms_guard._app("refresh") or getattr(st, "rerun", None)
    if callable(rerun):
        rerun()


def _install_employee_tab_visibility() -> bool:
    proxy_cls = getattr(swms_guard, "_TabProxy", None)
    if proxy_cls is None or getattr(proxy_cls, "_pb_swms_visibility_guard", False):
        return False

    original_enter = getattr(proxy_cls, "__enter__", None)
    if original_enter is None:
        return False

    def visible_enter(self: Any) -> Any:
        entered = original_enter(self)
        try:
            swms_guard.render_swms_panel(employee_mode=True, key_prefix="employee_swms")
        except Exception as exc:
            st = swms_guard._st()
            if st is not None:
                st.warning(f"SWMS panel could not render: {exc}")
        return entered

    def visible_exit(self: Any, exc_type: Any, exc: Any, tb: Any) -> Any:
        return self._tab.__exit__(exc_type, exc, tb)

    proxy_cls.__enter__ = visible_enter
    proxy_cls.__exit__ = visible_exit
    proxy_cls._pb_swms_visibility_guard = True
    return True


def _looks_like_main_menu(options: Any) -> bool:
    try:
        labels = [str(option) for option in list(options)]
    except Exception:
        return False
    if ADMIN_SWMS_LABEL in labels:
        return True
    markers = {
        "Dashboard", "Jobs", "Job Folders", "Estimating", "Site Operations",
        "Operations Hub", "Management", "Employee Portal", "Reports",
        "Settings", "User Access", "Job Photos", "Timesheets",
    }
    return len(markers.intersection(labels)) >= 3


def _with_admin_swms(options: Any) -> list[Any]:
    values = list(options)
    labels = [str(option) for option in values]
    if ADMIN_SWMS_LABEL not in labels:
        insert_at = len(values)
        for idx, label in enumerate(labels):
            if label in {"Management", "Operations Hub", "Site Operations", "Job Folders"}:
                insert_at = idx + 1
        values.insert(insert_at, ADMIN_SWMS_LABEL)
    return values


def _clear_admin_swms_state(st: Any) -> None:
    try:
        st.session_state.pop(ADMIN_SWMS_STATE_KEY, None)
        st.session_state["main_menu"] = "Dashboard"
    except Exception:
        pass


def _show_admin_swms(st: Any) -> None:
    st.header("SWMS / Safety Sign-off")
    st.caption("Admin view: create job SWMS documents, download them, and review employee electronic acknowledgements.")
    if st.button("← Back to Dashboard", key="admin_swms_back_to_dashboard"):
        _clear_admin_swms_state(st)
        _safe_rerun(st)
    swms_guard.render_swms_panel(employee_mode=False, key_prefix="admin_swms")
    st.stop()


def _install_dashboard_route(st: Any) -> bool:
    # The app validates the main menu against its original option list on every
    # rerun. A dynamically added SWMS menu item can therefore reset back to
    # Dashboard before the route block runs. This patch makes that reset safe:
    # when SWMS has been selected, the normal Dashboard route renders the SWMS
    # page instead of the operational dashboard.
    module = sys.modules.get("__main__") or sys.modules.get("pb_jobhub_app")
    if module is None:
        return False
    original = getattr(module, "render_operational_dashboard", None)
    if original is None or getattr(original, DASHBOARD_PATCH_KEY, False):
        return False

    def dashboard_or_swms(*args: Any, **kwargs: Any):
        if bool(st.session_state.get(ADMIN_SWMS_STATE_KEY)):
            _show_admin_swms(st)
        return original(*args, **kwargs)

    setattr(dashboard_or_swms, DASHBOARD_PATCH_KEY, True)
    setattr(dashboard_or_swms, "_pb_original_dashboard", original)
    setattr(module, "render_operational_dashboard", dashboard_or_swms)
    return True


def _patch_widget(owner: Any, attr: str, st: Any) -> bool:
    original = getattr(owner, attr, None)
    if original is None or getattr(original, "_pb_swms_admin_nav_guard", False):
        return False

    def wrapper(*args: Any, **kwargs: Any):
        # Supports both st.radio(label, options, ...) and
        # DeltaGenerator.radio(self, label, options, ...).
        arg_list = list(args)
        menu_widget = False
        if len(arg_list) >= 2 and _looks_like_main_menu(arg_list[1]):
            menu_widget = True
            arg_list[1] = _with_admin_swms(arg_list[1])
        elif len(arg_list) >= 3 and _looks_like_main_menu(arg_list[2]):
            menu_widget = True
            arg_list[2] = _with_admin_swms(arg_list[2])
        elif "options" in kwargs and _looks_like_main_menu(kwargs.get("options")):
            menu_widget = True
            kwargs["options"] = _with_admin_swms(kwargs["options"])

        if menu_widget:
            _install_dashboard_route(st)

        result = original(*tuple(arg_list), **kwargs)
        if menu_widget and str(result) == ADMIN_SWMS_LABEL:
            st.session_state[ADMIN_SWMS_STATE_KEY] = True
            # Rerun so the Dashboard fallback route can render the SWMS page in
            # the main app area instead of inside the sidebar widget call.
            _safe_rerun(st)
        return result

    wrapper._pb_swms_admin_nav_guard = True
    wrapper._pb_original = original
    setattr(owner, attr, wrapper)
    return True


def _install_admin_menu_visibility() -> bool:
    st = sys.modules.get("streamlit")
    if st is None:
        return False
    installed = False
    for attr in ("radio", "selectbox"):
        installed = _patch_widget(st, attr, st) or installed

    delta_module = sys.modules.get("streamlit.delta_generator")
    delta_cls = getattr(delta_module, "DeltaGenerator", None) if delta_module is not None else None
    if delta_cls is not None:
        for attr in ("radio", "selectbox"):
            installed = _patch_widget(delta_cls, attr, st) or installed
    return installed


def install_swms_visibility_guard() -> bool:
    employee_installed = _install_employee_tab_visibility()
    admin_installed = _install_admin_menu_visibility()
    return bool(employee_installed or admin_installed)
