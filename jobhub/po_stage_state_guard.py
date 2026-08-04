"""Prevent Upload PO from freezing when switching between jobs.

The Upload PO page reuses the same stage widget key for every job. Streamlit keeps
widget state by key, so switching jobs can leave the stage selector bound to a
value from the previous job. Give the stage selector a stable job-specific key
before Streamlit creates it, and discard any invalid value for that job.
"""

from __future__ import annotations

import hashlib
import sys
from typing import Any


PO_STAGE_KEY = "po_upload_stage"
PO_JOB_KEY = "po_upload_job"


def _option_values(options: Any) -> list[Any]:
    try:
        return list(options) if options is not None else []
    except Exception:
        return []


def _job_specific_key(st: Any) -> str:
    try:
        selected_job = str(st.session_state.get(PO_JOB_KEY) or "default")
    except Exception:
        selected_job = "default"
    token = hashlib.sha1(selected_job.encode("utf-8")).hexdigest()[:12]
    return f"{PO_STAGE_KEY}_{token}"


def _patch_selectbox(owner: Any, st: Any) -> bool:
    original = getattr(owner, "selectbox", None)
    if original is None or getattr(original, "_pb_po_stage_state_guard_v2", False):
        return False

    def wrapper(*args: Any, **kwargs: Any):
        key = str(kwargs.get("key") or "")
        if key == PO_STAGE_KEY:
            arg_list = list(args)
            options = kwargs.get("options")
            if options is None:
                if len(arg_list) >= 2 and isinstance(arg_list[0], str):
                    options = arg_list[1]
                elif len(arg_list) >= 3:
                    options = arg_list[2]
            values = _option_values(options)
            dynamic_key = _job_specific_key(st)
            kwargs["key"] = dynamic_key
            try:
                current = st.session_state.get(dynamic_key)
                if current is not None and current not in values:
                    del st.session_state[dynamic_key]
            except Exception:
                pass
        return original(*args, **kwargs)

    wrapper._pb_po_stage_state_guard_v2 = True
    wrapper._pb_original_selectbox = original
    setattr(owner, "selectbox", wrapper)
    return True


def install_po_stage_state_guard() -> bool:
    st = sys.modules.get("streamlit")
    if st is None:
        return False
    installed = _patch_selectbox(st, st)
    try:
        delta_module = sys.modules.get("streamlit.delta_generator")
        delta_cls = getattr(delta_module, "DeltaGenerator", None) if delta_module is not None else None
    except Exception:
        delta_cls = None
    if delta_cls is not None:
        installed = _patch_selectbox(delta_cls, st) or installed
    return installed
