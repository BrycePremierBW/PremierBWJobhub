"""Timesheet area selector helpers for Premier Brushworks JobHub.

Adds an Area Worked selector to the existing timesheet form without changing the
underlying timesheet table schema. The selected area is stored in the existing
work_type text as "Internal — Painting", "External — Prep", etc., so current
reports and exports keep working.
"""

from __future__ import annotations

import sys
from typing import Any


AREA_OPTIONS = ["All", "Internal", "External"]
AREA_KEY_SUFFIX = "_area_worked"


def _looks_like_timesheet_work_type(label: Any, option_args: tuple[Any, ...], kwargs: dict[str, Any]) -> bool:
    if str(label or "") != "Work Type":
        return False
    key = str(kwargs.get("key") or "")
    if not key.endswith("_work_type"):
        return False
    options = list(option_args[0]) if option_args else list(kwargs.get("options") or [])
    expected = {"Painting", "Prep", "Spraying", "Touch-ups", "Travel", "Site Setup", "Other"}
    return bool(expected.intersection({str(option) for option in options}))


def _area_key(work_type_key: str) -> str:
    base = work_type_key[:-len("_work_type")] if work_type_key.endswith("_work_type") else work_type_key
    return f"{base}{AREA_KEY_SUFFIX}"


def _combine_area_and_work_type(area: str, work_type: Any) -> str:
    work_type_text = str(work_type or "").strip()
    area_text = str(area or "All").strip() or "All"
    for prefix in AREA_OPTIONS:
        marker = f"{prefix} — "
        if work_type_text.startswith(marker):
            work_type_text = work_type_text[len(marker):]
            break
    return f"{area_text} — {work_type_text}" if work_type_text else area_text


def _split_selectbox_call(args: tuple[Any, ...]) -> tuple[bool, Any, Any, tuple[Any, ...]]:
    # Top-level st.selectbox: (label, options, ...)
    # DeltaGenerator.selectbox: (self, label, options, ...)
    if len(args) >= 2 and not isinstance(args[0], str):
        return True, args[0], args[1], tuple(args[2:])
    label = args[0] if args else ""
    return False, None, label, tuple(args[1:])


def _call_selectbox(original: Any, has_self: bool, owner_self: Any, label: Any, *args: Any, **kwargs: Any) -> Any:
    if has_self:
        return original(owner_self, label, *args, **kwargs)
    return original(label, *args, **kwargs)


def _patch_selectbox(owner: Any) -> bool:
    original_selectbox = getattr(owner, "selectbox", None)
    if original_selectbox is None or getattr(original_selectbox, "_pb_timesheet_area_guard", False):
        return False

    def pb_timesheet_area_selectbox(*args: Any, **kwargs: Any):
        has_self, owner_self, label, option_args = _split_selectbox_call(args)
        if _looks_like_timesheet_work_type(label, option_args, kwargs):
            work_type_key = str(kwargs.get("key") or "timesheet_work_type")
            area = _call_selectbox(
                original_selectbox,
                has_self,
                owner_self,
                "Area Worked",
                AREA_OPTIONS,
                key=_area_key(work_type_key),
                help="Select whether these hours were worked on all areas, internal work, or external work.",
            )
            work_type = original_selectbox(*args, **kwargs)
            return _combine_area_and_work_type(str(area), work_type)
        return original_selectbox(*args, **kwargs)

    pb_timesheet_area_selectbox._pb_timesheet_area_guard = True
    pb_timesheet_area_selectbox._pb_original_selectbox = original_selectbox
    setattr(owner, "selectbox", pb_timesheet_area_selectbox)
    return True


def install_timesheet_area_guard() -> bool:
    st = sys.modules.get("streamlit")
    if st is None:
        return False

    installed = _patch_selectbox(st)

    delta_module = sys.modules.get("streamlit.delta_generator")
    delta_cls = getattr(delta_module, "DeltaGenerator", None) if delta_module is not None else None
    if delta_cls is not None:
        installed = _patch_selectbox(delta_cls) or installed
    return installed
