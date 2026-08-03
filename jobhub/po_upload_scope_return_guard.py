"""PO upload usability guard.

Adds a reliable return-to-dashboard button for the injected Upload PO route and
adds clear Whole job/Internal/External stage-area choices with an in-form help
popup for the PO calculation workflow.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any


AREA_LINE_KEY = "pb_po_upload_area_line"
DISPLAY_WHOLE_JOB = "Whole job"
DISPLAY_INTERNAL = "Internal"
DISPLAY_EXTERNAL = "External"
DISPLAY_UPPER_SCAFF = "External - Upper scaff work"
DISPLAY_LOWER_EXTERNAL = "External - Lower external"
DISPLAY_EXTERNAL_TOUCHUPS = "External - Touch ups"
DISPLAY_CUSTOM = "Custom / not listed"
DISPLAY_WHOLE_JOB_BASIS = "Whole job value"
DISPLAY_SELECTED_SCOPE_BASIS = "Selected area / stage value"

QUICK_STAGE_OPTIONS: tuple[str, ...] = (
    DISPLAY_WHOLE_JOB,
    DISPLAY_INTERNAL,
    DISPLAY_EXTERNAL,
    DISPLAY_UPPER_SCAFF,
    DISPLAY_LOWER_EXTERNAL,
    DISPLAY_EXTERNAL_TOUCHUPS,
    DISPLAY_CUSTOM,
)

STAGE_AREA_GUIDE: tuple[tuple[str, str], ...] = (
    (DISPLAY_WHOLE_JOB, "Use when the PO covers the full contract value."),
    (DISPLAY_INTERNAL, "Use for internal painting works only. Enter the internal works value."),
    (DISPLAY_EXTERNAL, "Use for external painting works only. Enter the external works value."),
    (DISPLAY_UPPER_SCAFF, "Use for the upper scaffold external claim. Common split is 45% of external."),
    (DISPLAY_LOWER_EXTERNAL, "Use for lower external works. Common split is 45% of external."),
    (DISPLAY_EXTERNAL_TOUCHUPS, "Use for external touch-ups. Common split is 10% of external."),
    (DISPLAY_CUSTOM, "Use when the PO does not fit one of the standard options."),
)


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
            clicked = streamlit_module.button(
                "← Return to start / Dashboard",
                key="po_upload_return_to_start_dashboard",
                type="secondary",
                width="stretch",
            )
        except TypeError:
            clicked = streamlit_module.button(
                "← Return to start / Dashboard",
                key="po_upload_return_to_start_dashboard",
                type="secondary",
            )
        if clicked:
            _clear_po_route(streamlit_module)
            _safe_rerun(streamlit_module)
            return
        return original(streamlit_module)

    show_page_with_return._pb_po_scope_return_guard = True
    show_page_with_return._pb_original_show_page = original
    po._show_page = show_page_with_return
    return True


def _install_stage_options_guard(po: Any) -> bool:
    original = getattr(po, "_stage_options", None)
    if original is None or getattr(original, "_pb_po_stage_options_guard", False):
        return False

    def stage_options_with_standard_areas(job_id: int) -> dict[str, int | None]:
        try:
            existing = dict(original(job_id) or {})
        except Exception:
            existing = {}
        combined: dict[str, int | None] = {}
        whole_key = "Whole job / not stage-specific"
        if whole_key in existing:
            combined[whole_key] = existing.pop(whole_key)
        for option in QUICK_STAGE_OPTIONS:
            if option not in combined:
                combined[option] = None
        for key, value in existing.items():
            if str(key) not in combined:
                combined[str(key)] = value
        return combined

    stage_options_with_standard_areas._pb_po_stage_options_guard = True
    stage_options_with_standard_areas._pb_original_stage_options = original
    po._stage_options = stage_options_with_standard_areas
    return True


def _widget_signature(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[str, int | None]:
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


def _set_arg_label(arg_list: list[Any], kwargs: dict[str, Any], label_index: int | None, label: str) -> None:
    if label_index is not None and label_index < len(arg_list):
        arg_list[label_index] = label
    else:
        kwargs["label"] = label


def _render_stage_area_popup(st: Any) -> None:
    lines = "\n".join(f"- **{name}:** {description}" for name, description in STAGE_AREA_GUIDE)
    try:
        popover = getattr(st, "popover", None)
        if callable(popover):
            with popover("Stage / area options guide"):
                st.markdown(lines)
            return
    except Exception:
        pass
    try:
        with st.expander("Stage / area options guide", expanded=False):
            st.markdown(lines)
    except Exception:
        try:
            st.caption("Stage / area options: Whole job, Internal, External, Upper scaff, Lower external, Touch ups, or Custom.")
        except Exception:
            pass


def _area_line_from_result(result: Any) -> str:
    text = str(result or "").strip()
    if text == "Whole job / not stage-specific":
        return DISPLAY_WHOLE_JOB
    return text or DISPLAY_CUSTOM


def _patch_selectbox(owner: Any, st: Any, po: Any) -> bool:
    original = getattr(owner, "selectbox", None)
    if original is None or getattr(original, "_pb_po_scope_selectbox_guard", False):
        return False

    def selectbox_with_po_scope_lines(*args: Any, **kwargs: Any):
        arg_list = list(args)
        label, options_index = _widget_signature(tuple(arg_list), kwargs)
        label_index = 0 if arg_list and isinstance(arg_list[0], str) else (1 if len(arg_list) >= 2 else None)

        if label == "Stage / area":
            _render_stage_area_popup(st)
            kwargs.setdefault(
                "help",
                "Choose Whole job, Internal, External, an external split line, or Custom / not listed.",
            )
            result = original(*tuple(arg_list), **kwargs)
            try:
                st.session_state[AREA_LINE_KEY] = _area_line_from_result(result)
            except Exception:
                pass
            return result

        if label == "Calculate % from":
            _set_arg_label(arg_list, kwargs, label_index, "Calculation basis")
            options = [DISPLAY_WHOLE_JOB_BASIS, DISPLAY_SELECTED_SCOPE_BASIS]
            if options_index is not None:
                arg_list[options_index] = options
            else:
                kwargs["options"] = options
            kwargs.setdefault(
                "help",
                "Use Whole job value for full-job POs. Use selected area/stage value for Internal, External, upper scaffold, lower external or touch-ups.",
            )
            try:
                line = str(st.session_state.get(AREA_LINE_KEY, "") or "")
                if line and line != DISPLAY_WHOLE_JOB and "index" not in kwargs:
                    kwargs["index"] = 1
            except Exception:
                pass
            result = original(*tuple(arg_list), **kwargs)
            if str(result) == DISPLAY_WHOLE_JOB_BASIS:
                return getattr(po, "BASIS_TOTAL_JOB", DISPLAY_WHOLE_JOB_BASIS)
            return getattr(po, "BASIS_MANUAL_SCOPE", "Manual area / stage value")

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
                if line and line != DISPLAY_CUSTOM:
                    kwargs["value"] = line
                    kwargs["placeholder"] = f"{line} works"
                elif line == DISPLAY_CUSTOM:
                    kwargs["placeholder"] = "e.g. Block A external, Stage 2 internal, defects, variation"
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
                if line and line != DISPLAY_CUSTOM:
                    new_label = f"{line} value ex GST"
                    if label_index is not None:
                        arg_list[label_index] = new_label
                    else:
                        kwargs["label"] = new_label
                    kwargs["help"] = f"Enter the {line.lower()} value. JobHub will calculate the PO percentage from this line."
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
    installed = _install_stage_options_guard(po) or installed
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
