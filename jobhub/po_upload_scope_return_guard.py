"""PO upload usability guard.

Adds a reliable return-to-dashboard button for the injected Upload PO route and
adds Internal/External quick scope lines to the PO calculation controls without
rewriting the main app router.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any


AREA_LINE_KEY = "pb_po_upload_area_line"
DISPLAY_INTERNAL = "Internal"
DISPLAY_EXTERNAL = "External"
DISPLAY_WHOLE_JOB = "Whole job value"
DISPLAY_MANUAL_SCOPE = "Manual area / stage value"


def _st() -> Any:
    return sys.modules.get("streamlit")


def _po_module() -> Any:
    return sys.modules.get("jobhub.po_upload_guard") or importlib.import_module("jobhub.po_upload_guard")


def _safe_rerun(st: Any) -> None:
    rerun = getattr(st, "rerun", None)
    if callable(rerun):
        rerun()


def _clear_po_route(st: Any) -> None:
    try:
        po = _po_module()
        st.session_state[getattr(po, "PO_UPLOAD_STATE_KEY", "_pb_show_po_upload_page")] = False
    except Exception:
        pass
    for key, value in {
        "main_menu": "Dashboard",
        "management_menu": "Builders & Clients",
        "site_operations_menu": "Staff Scheduler",
        "estimating_menu": "Import / Create Job Pack",
        "ai_menu": "JobHub AI Assistant",
    }.items():
        try:
            if str(st.session_state.get(key, "")) in {"Upload PO", "Return to start"}:
                st.session_state[key] = value
        except Exception:
            pass
    try:
        st.session_state["main_menu"] = "Dashboard"
    except Exception:
        pass


def _install_return_button(po: Any, st: Any) -> bool:
    original = getattr(po, "_show_page", None)
    if original is None or getattr(original, "_pb_po_scope_return_guard", False):
        return False

    def show_page_with_return(streamlit_module: Any) -> None:
        try:
            if streamlit_module.button(
                "← Return to start / Dashboard",
                key="po_upload_return_to_start_dashboard",
                type="secondary",
                width="stretch",
            ):
                _clear_po_route(streamlit_module)
                _safe_rerun(streamlit_module)
                return
        except TypeError:
            if streamlit_module.button(
                "← Return to start / Dashboard",
                key="po_upload_return_to_start_dashboard",
                type="secondary",
            ):
                _clear_po_route(streamlit_module)
                _safe_rerun(streamlit_module)
                return
        return original(streamlit_module)

    show_page_with_return._pb_po_scope_return_guard = True
    show_page_with_return._pb_original_show_page = original
    po._show_page = show_page_with_return
    return True


def _radio_signature(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[str, int | None]:
    label = str(kwargs.get("label", "") or "")
    options_index: int | None = None
    if len(args) >= 2 and isinstance(args[0], str):
        label = str(args[0])
        options_index = 1
    elif len(args) >= 3:
        label = str(args[1])
        options_index = 2
    elif args:
        label = str(args[0])
    return label, options_index


def _patch_selectbox(owner: Any, st: Any, po: Any) -> bool:
    original = getattr(owner, "selectbox", None)
    if original is None or getattr(original, "_pb_po_scope_selectbox_guard", False):
        return False

    def selectbox_with_po_scope_lines(*args: Any, **kwargs: Any):
        arg_list = list(args)
        label, options_index = _radio_signature(tuple(arg_list), kwargs)
        if label == "Calculate % from":
            options = [DISPLAY_WHOLE_JOB, DISPLAY_INTERNAL, DISPLAY_EXTERNAL, DISPLAY_MANUAL_SCOPE]
            if options_index is not None:
                arg_list[options_index] = options
            else:
                kwargs["options"] = options
            result = original(*tuple(arg_list), **kwargs)
            try:
                if str(result) in {DISPLAY_INTERNAL, DISPLAY_EXTERNAL}:
                    st.session_state[AREA_LINE_KEY] = str(result)
                    return getattr(po, "BASIS_MANUAL_SCOPE", DISPLAY_MANUAL_SCOPE)
                if str(result) == DISPLAY_WHOLE_JOB:
                    st.session_state[AREA_LINE_KEY] = "Whole job"
                    return getattr(po, "BASIS_TOTAL_JOB", DISPLAY_WHOLE_JOB)
                st.session_state[AREA_LINE_KEY] = "Custom scope"
            except Exception:
                pass
            return getattr(po, "BASIS_MANUAL_SCOPE", DISPLAY_MANUAL_SCOPE)
        return original(*args, **kwargs)

    selectbox_with_po_scope_lines._pb_po_scope_selectbox_guard = True
    selectbox_with_po_scope_lines._pb_original_selectbox = original
    setattr(owner, "selectbox", selectbox_with_po_scope_lines)
    return True


def _patch_text_input(owner: Any, st: Any) -> bool:
    original = getattr(owner, "text_input", None)
    if original is None or getattr(original, "_pb_po_scope_text_input_guard", False):
        return False

    def text_input_with_po_scope_default(*args: Any, **kwargs: Any):
        arg_list = list(args)
        label = str(kwargs.get("label", "") or "")
        if arg_list and isinstance(arg_list[0], str):
            label = str(arg_list[0])
        elif len(arg_list) >= 2:
            label = str(arg_list[1])
        if label == "Area / scope name":
            try:
                line = str(st.session_state.get(AREA_LINE_KEY, "") or "")
                if line in {DISPLAY_INTERNAL, DISPLAY_EXTERNAL}:
                    kwargs["value"] = line
                    kwargs["placeholder"] = f"{line} works"
            except Exception:
                pass
        return original(*tuple(arg_list), **kwargs)

    text_input_with_po_scope_default._pb_po_scope_text_input_guard = True
    text_input_with_po_scope_default._pb_original_text_input = original
    setattr(owner, "text_input", text_input_with_po_scope_default)
    return True


def _patch_number_input(owner: Any, st: Any) -> bool:
    original = getattr(owner, "number_input", None)
    if original is None or getattr(original, "_pb_po_scope_number_input_guard", False):
        return False

    def number_input_with_po_scope_label(*args: Any, **kwargs: Any):
        arg_list = list(args)
        label = str(kwargs.get("label", "") or "")
        label_index: int | None = None
        if arg_list and isinstance(arg_list[0], str):
            label = str(arg_list[0])
            label_index = 0
        elif len(arg_list) >= 2:
            label = str(arg_list[1])
            label_index = 1
        if label == "Area / stage value ex GST":
            try:
                line = str(st.session_state.get(AREA_LINE_KEY, "") or "")
                if line in {DISPLAY_INTERNAL, DISPLAY_EXTERNAL}:
                    new_label = f"{line} value ex GST"
                    if label_index is not None:
                        arg_list[label_index] = new_label
                    else:
                        kwargs["label"] = new_label
                    kwargs["help"] = f"Enter the {line.lower()} works value. JobHub will calculate the PO percentage from this line."
            except Exception:
                pass
        return original(*tuple(arg_list), **kwargs)

    number_input_with_po_scope_label._pb_po_scope_number_input_guard = True
    number_input_with_po_scope_label._pb_original_number_input = original
    setattr(owner, "number_input", number_input_with_po_scope_label)
    return True


def install_po_upload_scope_return_guard() -> bool:
    st = _st()
    if st is None:
        return False
    po = _po_module()
    installed = _install_return_button(po, st)
    for owner in (st,):
        installed = _patch_selectbox(owner, st, po) or installed
        installed = _patch_text_input(owner, st) or installed
        installed = _patch_number_input(owner, st) or installed
    try:
        delta_module = sys.modules.get("streamlit.delta_generator") or importlib.import_module("streamlit.delta_generator")
        delta_cls = getattr(delta_module, "DeltaGenerator", None)
    except Exception:
        delta_cls = None
    if delta_cls is not None:
        installed = _patch_selectbox(delta_cls, st, po) or installed
        installed = _patch_text_input(delta_cls, st) or installed
        installed = _patch_number_input(delta_cls, st) or installed
    return installed
