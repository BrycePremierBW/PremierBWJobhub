"""Make SWMS controls visible in employee, manager, and admin JobHub views.

The main JobHub router uses a hard-coded menu list and validates session state
on every rerun. This guard therefore avoids injecting fake menu choices. It
adds a real Safety button beside the existing sidebar menu when the real main
menu widget is rendered, then opens the SWMS page directly and stops the normal
page route.
"""

from __future__ import annotations

import sys
from typing import Any

from . import swms_guard

ADMIN_SWMS_LABEL = "SWMS / Safety Sign-off"
ADMIN_SWMS_STATE_KEY = "_pb_show_admin_swms_page"


def _safe_rerun(st: Any) -> None:
    rerun = swms_guard._app("pb_rerun") or swms_guard._app("refresh") or getattr(st, "rerun", None)
    if callable(rerun):
        rerun()


def _current_role() -> str:
    role_fn = swms_guard._app("current_role")
    if callable(role_fn):
        try:
            return str(role_fn() or "").strip().casefold()
        except Exception:
            return ""
    return ""


def _employee_mode() -> bool:
    return _current_role() == "employee"


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


def _is_main_menu_options(options: Any) -> bool:
    try:
        labels = {str(option) for option in list(options)}
    except Exception:
        return False

    admin_or_manager_menu = (
        "Dashboard" in labels
        and ("Jobs" in labels or "Job Folders" in labels or "Control Centre" in labels)
        and ("Management" in labels or "Operations Hub" in labels or "Site Operations" in labels)
    )
    employee_menu = {"Field Mode", "Employee Portal"}.issubset(labels)
    return bool(admin_or_manager_menu or employee_menu)


def _clear_admin_swms_state(st: Any) -> None:
    try:
        st.session_state.pop(ADMIN_SWMS_STATE_KEY, None)
        st.session_state["main_menu"] = "Employee Portal" if _employee_mode() else "Dashboard"
    except Exception:
        pass


def _show_swms_page(st: Any) -> None:
    employee_mode = _employee_mode()
    st.header("SWMS / Safety Sign-off")
    if employee_mode:
        st.caption("Employee view: download job SWMS documents and record your electronic acknowledgement.")
    else:
        st.caption(
            "Admin view: create job SWMS documents, download them, and review "
            "employee electronic acknowledgements."
        )
    if st.button("← Back to start", key="admin_swms_back_to_dashboard"):
        _clear_admin_swms_state(st)
        _safe_rerun(st)
    swms_guard.render_swms_panel(
        employee_mode=employee_mode,
        key_prefix="employee_swms_page" if employee_mode else "admin_swms",
    )
    st.stop()


def _render_swms_launcher(st: Any) -> None:
    # This runs during the real sidebar menu render, not during package import,
    # so it does not violate Streamlit's set_page_config ordering.
    if bool(st.session_state.get(ADMIN_SWMS_STATE_KEY)):
        _show_swms_page(st)

    st.sidebar.markdown("### Safety")
    try:
        clicked = st.sidebar.button(
            ADMIN_SWMS_LABEL,
            key="admin_swms_sidebar_launcher",
            width="stretch",
        )
    except TypeError:
        clicked = st.sidebar.button(
            ADMIN_SWMS_LABEL,
            key="admin_swms_sidebar_launcher",
        )

    if clicked:
        st.session_state[ADMIN_SWMS_STATE_KEY] = True
        _show_swms_page(st)


def _patch_radio(owner: Any, st: Any) -> bool:
    original = getattr(owner, "radio", None)
    if original is None or getattr(original, "_pb_swms_admin_launcher_guard", False):
        return False

    def wrapper(*args: Any, **kwargs: Any):
        arg_list = list(args)
        options = None
        if len(arg_list) >= 2 and _is_main_menu_options(arg_list[1]):
            options = arg_list[1]
        elif len(arg_list) >= 3 and _is_main_menu_options(arg_list[2]):
            options = arg_list[2]
        elif "options" in kwargs and _is_main_menu_options(kwargs.get("options")):
            options = kwargs.get("options")

        if options is not None:
            _render_swms_launcher(st)

        result = original(*tuple(arg_list), **kwargs)
        if str(result) == ADMIN_SWMS_LABEL:
            st.session_state[ADMIN_SWMS_STATE_KEY] = True
            _show_swms_page(st)
        return result

    wrapper._pb_swms_admin_launcher_guard = True
    wrapper._pb_original = original
    setattr(owner, "radio", wrapper)
    return True


def _install_sidebar_launcher() -> bool:
    st = sys.modules.get("streamlit")
    if st is None:
        return False

    installed = _patch_radio(st, st)

    delta_module = sys.modules.get("streamlit.delta_generator")
    delta_cls = getattr(delta_module, "DeltaGenerator", None) if delta_module is not None else None
    if delta_cls is not None:
        installed = _patch_radio(delta_cls, st) or installed
    return installed


def install_swms_visibility_guard() -> bool:
    employee_installed = _install_employee_tab_visibility()
    launcher_installed = _install_sidebar_launcher()
    return bool(employee_installed or launcher_installed)
