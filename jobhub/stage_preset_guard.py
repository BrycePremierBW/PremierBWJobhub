"""Stage preset helpers for Premier Brushworks JobHub.

This guard keeps the stage workflow safe by adding the requested Premier
Brushworks stage presets to the existing Add Stage form without rewriting the
large Streamlit app file.
"""

from __future__ import annotations

import sys
from typing import Any


STAGE_PRESETS: list[tuple[str, float]] = [
    ("Internal prep and spray Sealer", 30.0),
    ("prep and spray finish coats", 30.0),
    ("cut and roll walls and paint doors", 30.0),
    ("touch ups", 10.0),
]
ADD_CUSTOM_STAGE = "Add item not listed"
_STAGE_CHOICE_KEY = "pb_stage_preset_stage_name_choice"
_STAGE_CUSTOM_KEY = "pb_stage_preset_custom_stage_name"
_STAGE_PERCENT_KEY = "pb_stage_preset_job_percent"


def _is_add_stage_name_input(label: Any, kwargs: dict[str, Any]) -> bool:
    return (
        str(label or "") == "Stage Name"
        and "External Upper" in str(kwargs.get("placeholder") or "")
        and "value" not in kwargs
    )


def _is_add_stage_percent_input(label: Any, kwargs: dict[str, Any]) -> bool:
    return (
        str(label or "") == "Job %"
        and float(kwargs.get("value", 0.0) or 0.0) == 0.0
        and float(kwargs.get("max_value", 0.0) or 0.0) >= 100.0
    )


def _preset_percent(stage_name: str) -> float | None:
    for preset_name, percent in STAGE_PRESETS:
        if stage_name == preset_name:
            return percent
    return None


def install_stage_preset_guard() -> bool:
    st = sys.modules.get("streamlit")
    if st is None:
        return False

    original_text_input = getattr(st, "text_input", None)
    original_number_input = getattr(st, "number_input", None)
    original_selectbox = getattr(st, "selectbox", None)
    original_caption = getattr(st, "caption", None)

    if (
        original_text_input is None
        or original_number_input is None
        or original_selectbox is None
        or getattr(original_text_input, "_pb_stage_preset_guard", False)
    ):
        return False

    preset_labels = [name for name, _ in STAGE_PRESETS] + [ADD_CUSTOM_STAGE]

    def pb_stage_preset_text_input(label: Any, *args: Any, **kwargs: Any):
        if _is_add_stage_name_input(label, kwargs):
            if callable(original_caption):
                original_caption(
                    "Stage selection: choose a standard Premier Brushworks stage, "
                    "or choose Add item not listed to type a custom stage."
                )
            choice = original_selectbox(
                "Stage selection",
                preset_labels,
                key=_STAGE_CHOICE_KEY,
                help="Standard internal painting stage split: 30%, 30%, 30%, 10%.",
            )
            st.session_state["pb_stage_preset_selected_name"] = choice
            percent = _preset_percent(choice)
            if percent is not None:
                st.session_state["pb_stage_preset_selected_percent"] = float(percent)
                return choice
            st.session_state["pb_stage_preset_selected_percent"] = 0.0
            custom = original_text_input(
                "Custom Stage Name",
                value=str(st.session_state.get(_STAGE_CUSTOM_KEY, "") or ""),
                key=_STAGE_CUSTOM_KEY,
                placeholder="Type the custom stage name",
            )
            return custom
        return original_text_input(label, *args, **kwargs)

    def pb_stage_preset_number_input(label: Any, *args: Any, **kwargs: Any):
        if _is_add_stage_percent_input(label, kwargs):
            selected = str(st.session_state.get("pb_stage_preset_selected_name") or "")
            percent = _preset_percent(selected)
            kwargs = dict(kwargs)
            if percent is not None:
                kwargs["value"] = float(percent)
                kwargs.setdefault("key", _STAGE_PERCENT_KEY)
                kwargs.setdefault(
                    "help",
                    "Auto-filled from the selected standard stage. You can still change it before saving.",
                )
            elif selected == ADD_CUSTOM_STAGE:
                kwargs.setdefault("key", _STAGE_PERCENT_KEY)
                kwargs.setdefault("help", "Enter the job percentage for the custom stage.")
        return original_number_input(label, *args, **kwargs)

    pb_stage_preset_text_input._pb_stage_preset_guard = True
    pb_stage_preset_text_input._pb_original_text_input = original_text_input
    pb_stage_preset_number_input._pb_stage_preset_guard = True
    pb_stage_preset_number_input._pb_original_number_input = original_number_input
    st.text_input = pb_stage_preset_text_input
    st.number_input = pb_stage_preset_number_input
    return True
