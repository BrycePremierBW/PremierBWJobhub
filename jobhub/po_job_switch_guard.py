"""Make Upload PO job changes fast and isolate widget state per job."""

from __future__ import annotations

import sys
from typing import Any


PATCH_MARKER = "_pb_po_job_switch_guard"
JOB_KEY = "po_upload_job"
STAGE_KEY = "po_upload_stage"
FORM_KEY = "po_upload_form"
FILE_KEY = "po_upload_file"
SCHEMA_READY_KEY = "_pb_po_schema_ready"


def _selected_job_token(st: Any) -> str:
    try:
        value = str(st.session_state.get(JOB_KEY, "default") or "default")
    except Exception:
        value = "default"
    # Stable, widget-safe token without depending on the database id lookup.
    return str(abs(hash(value)))


def _patch_selectbox(owner: Any, st: Any) -> bool:
    original = getattr(owner, "selectbox", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    def wrapper(*args: Any, **kwargs: Any):
        if str(kwargs.get("key") or "") == STAGE_KEY:
            kwargs["key"] = f"{STAGE_KEY}_{_selected_job_token(st)}"
        return original(*args, **kwargs)

    setattr(wrapper, PATCH_MARKER, True)
    wrapper._pb_original_selectbox = original
    setattr(owner, "selectbox", wrapper)
    return True


def _patch_file_uploader(owner: Any, st: Any) -> bool:
    original = getattr(owner, "file_uploader", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    def wrapper(*args: Any, **kwargs: Any):
        if str(kwargs.get("key") or "") == FILE_KEY:
            kwargs["key"] = f"{FILE_KEY}_{_selected_job_token(st)}"
        return original(*args, **kwargs)

    setattr(wrapper, PATCH_MARKER, True)
    wrapper._pb_original_file_uploader = original
    setattr(owner, "file_uploader", wrapper)
    return True


def _patch_form(owner: Any, st: Any) -> bool:
    original = getattr(owner, "form", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    def wrapper(key: str, *args: Any, **kwargs: Any):
        if str(key) == FORM_KEY:
            key = f"{FORM_KEY}_{_selected_job_token(st)}"
        return original(key, *args, **kwargs)

    setattr(wrapper, PATCH_MARKER, True)
    wrapper._pb_original_form = original
    setattr(owner, "form", wrapper)
    return True


def _patch_schema_once(st: Any) -> bool:
    module = sys.modules.get("jobhub.po_upload_guard")
    if module is None:
        return False
    original = getattr(module, "_ensure_schema", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    def ensure_schema_once() -> None:
        try:
            if st.session_state.get(SCHEMA_READY_KEY, False):
                return
        except Exception:
            pass
        original()
        try:
            st.session_state[SCHEMA_READY_KEY] = True
        except Exception:
            pass

    setattr(ensure_schema_once, PATCH_MARKER, True)
    ensure_schema_once._pb_original_ensure_schema = original
    module._ensure_schema = ensure_schema_once
    return True


def install_po_job_switch_guard() -> bool:
    st = sys.modules.get("streamlit")
    if st is None:
        return False
    installed = _patch_schema_once(st)
    installed = _patch_selectbox(st, st) or installed
    installed = _patch_file_uploader(st, st) or installed
    installed = _patch_form(st, st) or installed
    try:
        delta_module = sys.modules.get("streamlit.delta_generator")
        delta_cls = getattr(delta_module, "DeltaGenerator", None) if delta_module is not None else None
    except Exception:
        delta_cls = None
    if delta_cls is not None:
        installed = _patch_selectbox(delta_cls, st) or installed
        installed = _patch_file_uploader(delta_cls, st) or installed
        installed = _patch_form(delta_cls, st) or installed
    return installed
