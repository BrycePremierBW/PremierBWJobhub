"""Make SWMS controls visible in employee, manager, and admin JobHub views.

The main JobHub app validates its hard-coded menu list on every rerun, so this
module injects "SWMS / Safety Sign-off" into the real Streamlit radio options
and protects that selection from the app's reset checks before rendering the
SWMS page.
"""

from __future__ import annotations

import sys
from typing import Any

from . import swms_guard

ADMIN_SWMS_LABEL = "SWMS / Safety Sign-off"
ADMIN_SWMS_STATE_KEY = "_pb_show_admin_swms_page"
SESSION_GET_PATCH_KEY = "_pb_swms_session_get_guard"


MAIN_MENU_LABELS = {
    "Dashboard", "Control Centre", "Operations Hub", "Jobs", "Job Folders",
    "Estimating", "Site Operations", "Reports", "Management", "AI Assistant",
    "Field Mode", "Employee Portal",
}

MANAGEMENT_LABELS = {
    "User Accounts", "Builders & Clients", "Employees", "Staff Requests",
    "Products", "Equipment",
}

SITE_LABELS = {
    "Staff Scheduler", "Painting Intelligence", "Material Costs", "Wages",
    "Timesheets", "Job Photos",
}

EMPLOYEE_TAB_LABELS = {
    "My Job Info", "Requests", "Submit Timesheet", "View Equipment",
    "Generate Forms", "Upload Photos", "Change Password",
}

RESET_SAFE_VALUES = {
    "main_menu": "Dashboard",
    "management_menu": "Builders & Clients",
    "site_operations_menu": "Staff Scheduler",
    "estimating_menu": "Import / Create Job Pack",
    "ai_menu": "JobHub AI Assistant",
    "employee_menu": "Generate Forms",
    "employee_portal_menu": "Generate Forms",
}


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


def _session_value(st: Any, key: str, default: Any = None) -> Any:
    try:
        return st.session_state[key]
    except Exception:
        try:
            return st.session_state.get(key, default)
        except Exception:
            return default


def _install_session_state_reset_guard(st: Any) -> bool:
    """Stop JobHub's hard-coded menu validation from erasing SWMS selection.

    JobHub checks st.session_state.get("main_menu") against its original menu
    list before the radio widget is rendered. When SWMS is selected, that check
    would normally reset the key to Dashboard. This patch only changes .get()
    for those reset checks; the underlying session value remains SWMS so the
    radio wrapper can render the SWMS page.
    """
    try:
        state_cls = type(st.session_state)
        original_get = getattr(state_cls, "get", None)
    except Exception:
        return False
    if original_get is None or getattr(original_get, SESSION_GET_PATCH_KEY, False):
        return False

    def guarded_get(self: Any, key: Any, default: Any = None) -> Any:
        value = original_get(self, key, default)
        key_text = str(key or "")
        if key_text in RESET_SAFE_VALUES and str(value) == ADMIN_SWMS_LABEL:
            if key_text == "main_menu" and _employee_mode():
                return "Employee Portal"
            return RESET_SAFE_VALUES[key_text]
        return value

    guarded_get._pb_original_get = original_get
    setattr(guarded_get, SESSION_GET_PATCH_KEY, True)
    setattr(state_cls, "get", guarded_get)
    return True


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


def _labels(options: Any) -> list[str]:
    try:
        return [str(option) for option in list(options)]
    except Exception:
        return []


def _should_inject_swms(label: Any, key: Any, options: Any) -> bool:
    option_labels = set(_labels(options))
    if ADMIN_SWMS_LABEL in option_labels:
        return True

    label_text = str(label or "").strip()
    key_text = str(key or "").strip()

    if label_text == "Menu" or key_text == "main_menu":
        return bool(option_labels.intersection(MAIN_MENU_LABELS))
    if label_text in {"Management Section", "Site Section"} or key_text in {"management_menu", "site_operations_menu"}:
        return bool(option_labels.intersection(MANAGEMENT_LABELS | SITE_LABELS))
    if label_text in {"Employee Portal", "Employee Section"} or key_text in {"employee_menu", "employee_portal_menu"}:
        return bool(option_labels.intersection(EMPLOYEE_TAB_LABELS))

    # Fallback for sidebar radios whose label/key has changed but whose options
    # clearly match one of JobHub's navigation groups.
    if len(option_labels.intersection(MAIN_MENU_LABELS)) >= 2:
        return True
    if len(option_labels.intersection(MANAGEMENT_LABELS | SITE_LABELS)) >= 2:
        return True
    return False


def _with_swms_option(options: Any) -> Any:
    try:
        values = list(options)
    except Exception:
        return options
    if ADMIN_SWMS_LABEL not in [str(value) for value in values]:
        values.append(ADMIN_SWMS_LABEL)
    return values


def _clear_swms_state(st: Any) -> None:
    try:
        st.session_state.pop(ADMIN_SWMS_STATE_KEY, None)
        st.session_state["main_menu"] = "Employee Portal" if _employee_mode() else "Dashboard"
        for key in ("management_menu", "site_operations_menu", "estimating_menu", "ai_menu", "employee_menu", "employee_portal_menu"):
            if str(_session_value(st, key, "")) == ADMIN_SWMS_LABEL:
                st.session_state.pop(key, None)
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
        _clear_swms_state(st)
        _safe_rerun(st)
    swms_guard.render_swms_panel(
        employee_mode=employee_mode,
        key_prefix="employee_swms_page" if employee_mode else "admin_swms",
    )
    st.stop()


def _patch_radio(owner: Any, st: Any) -> bool:
    original = getattr(owner, "radio", None)
    if original is None or getattr(original, "_pb_swms_visible_menu_guard", False):
        return False

    def wrapper(*args: Any, **kwargs: Any):
        arg_list = list(args)
        label = None
        options_index = None

        # Supports both st.radio(label, options, ...) and
        # DeltaGenerator.radio(self, label, options, ...).
        if len(arg_list) >= 2 and isinstance(arg_list[0], str):
            label = arg_list[0]
            options_index = 1
        elif len(arg_list) >= 3:
            label = arg_list[1]
            options_index = 2
        elif "options" in kwargs:
            label = arg_list[0] if arg_list else kwargs.get("label")

        options = arg_list[options_index] if options_index is not None else kwargs.get("options")
        key_text = str(kwargs.get("key") or "")
        should_inject = _should_inject_swms(label, key_text, options)
        if should_inject:
            if options_index is not None:
                arg_list[options_index] = _with_swms_option(options)
            else:
                kwargs["options"] = _with_swms_option(options)

            # If the front end has already placed SWMS into session_state, render
            # now before the original radio falls back to the app's default.
            if key_text and str(_session_value(st, key_text)) == ADMIN_SWMS_LABEL:
                st.session_state[ADMIN_SWMS_STATE_KEY] = True
                _show_swms_page(st)
            if bool(_session_value(st, ADMIN_SWMS_STATE_KEY, False)):
                _show_swms_page(st)

        result = original(*tuple(arg_list), **kwargs)
        if should_inject and str(result) == ADMIN_SWMS_LABEL:
            st.session_state[ADMIN_SWMS_STATE_KEY] = True
            _show_swms_page(st)
        return result

    wrapper._pb_swms_visible_menu_guard = True
    wrapper._pb_original = original
    setattr(owner, "radio", wrapper)
    return True


def _install_menu_option() -> bool:
    st = sys.modules.get("streamlit")
    if st is None:
        return False

    installed = _install_session_state_reset_guard(st)
    installed = _patch_radio(st, st) or installed

    delta_module = sys.modules.get("streamlit.delta_generator")
    delta_cls = getattr(delta_module, "DeltaGenerator", None) if delta_module is not None else None
    if delta_cls is not None:
        installed = _patch_radio(delta_cls, st) or installed
    return installed


def install_swms_visibility_guard() -> bool:
    employee_installed = _install_employee_tab_visibility()
    menu_installed = _install_menu_option()
    return bool(employee_installed or menu_installed)
