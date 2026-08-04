"""Prevent Upload PO from freezing when switching between jobs.

Streamlit retains selectbox values by widget key. The Upload PO stage selector uses
one key across every job, so changing to a job with different stages can leave a
saved value that is not present in the new option list. Reset only that stale
value before Streamlit validates the widget.
"""

from __future__ import annotations

import sys
from typing import Any


PO_STAGE_KEY = "po_upload_stage"


def _option_values(options: Any) -> list[Any]:
    try:
        return list(options) if options is not None else []
    except Exception:
        return []


def _patch_selectbox(owner: Any, st: Any) -> bool:
    original = getattr(owner, "selectbox", None)
    if original is None or getattr(original, "_pb_po_stage_state_guard", False):
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
            try:
                current = st.session_state.get(PO_STAGE_KEY)
                if current is not None and current not in values:
                    del st.session_state[PO_STAGE_KEY]
            except Exception:
                pass
        return original(*args, **kwargs)

    wrapper._pb_po_stage_state_guard = True
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
