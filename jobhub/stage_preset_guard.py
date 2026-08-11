"""Stage preset helpers for Premier Brushworks JobHub.

This guard keeps the stage workflow safe by adding Premier Brushworks stage
presets to the existing Add Stage form. The stage form uses Streamlit column
widgets (``a1.text_input`` / ``a3.number_input``), so this module patches both
the top-level ``st`` helpers and DeltaGenerator column methods.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from jobhub_time import jobhub_now
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class StagePreset:
    label: str
    stage_name: str
    percent: float
    section: str = "All"
    custom: bool = False


SIMPLE_STAGE_PRESETS: list[StagePreset] = [
    StagePreset("All / whole job — 100%", "Whole job", 100.0, "All"),
    StagePreset("Internal — 100%", "Internal", 100.0, "Internal"),
    StagePreset("External — 100%", "External", 100.0, "External"),
]

INTERNAL_STAGE_PRESETS: list[StagePreset] = [
    StagePreset("Internal — 100%", "Internal", 100.0, "Internal"),
    StagePreset("Internal prep and spray Sealer — 30%", "Internal prep and spray Sealer", 30.0, "Internal"),
    StagePreset("prep and spray finish coats — 30%", "prep and spray finish coats", 30.0, "Internal"),
    StagePreset("cut and roll walls and paint doors — 30%", "cut and roll walls and paint doors", 30.0, "Internal"),
    StagePreset("touch ups — 10%", "touch ups", 10.0, "Internal"),
]

EXTERNAL_STAGE_PRESETS: list[StagePreset] = [
    StagePreset("External — 100%", "External", 100.0, "External"),
    StagePreset("upper scaff work — 45%", "upper scaff work", 45.0, "External"),
    StagePreset("lower — 45%", "lower", 45.0, "External"),
    StagePreset("touch ups — 10%", "touch ups", 10.0, "External"),
]

ALL_STAGE_PRESETS: list[StagePreset] = [
    *SIMPLE_STAGE_PRESETS,
    StagePreset("Internal prep and spray Sealer — 30%", "Internal prep and spray Sealer", 30.0, "Internal"),
    StagePreset("prep and spray finish coats — 30%", "prep and spray finish coats", 30.0, "Internal"),
    StagePreset("cut and roll walls and paint doors — 30%", "cut and roll walls and paint doors", 30.0, "Internal"),
    StagePreset("Internal touch ups — 10%", "touch ups", 10.0, "Internal"),
    StagePreset("upper scaff work — 45%", "upper scaff work", 45.0, "External"),
    StagePreset("lower — 45%", "lower", 45.0, "External"),
    StagePreset("External touch ups — 10%", "touch ups", 10.0, "External"),
]

BASE_STAGE_PRESET_SECTIONS: dict[str, list[StagePreset]] = {
    "All": ALL_STAGE_PRESETS,
    "Internal": INTERNAL_STAGE_PRESETS,
    "External": EXTERNAL_STAGE_PRESETS,
}

ADD_CUSTOM_STAGE = "Add item not listed"
_STAGE_SECTION_KEY = "pb_stage_preset_section"
_STAGE_CHOICE_KEY = "pb_stage_preset_stage_name_choice"
_STAGE_CUSTOM_KEY = "pb_stage_preset_custom_stage_name"
_STAGE_PERCENT_KEY = "pb_stage_preset_job_percent"
_SAVE_CUSTOM_KEY = "pb_stage_preset_save_custom"
_SAVE_STATUS_KEY = "pb_stage_preset_save_status"
_LAST_STAGE_CHOICE_KEY = "pb_stage_preset_last_choice"
_LAST_STAGE_SECTION_KEY = "pb_stage_preset_last_section"


def _custom_preset_file() -> Path:
    return Path(os.getenv("DATA_DIR", "/var/data")) / "jobhub_custom_stage_presets.json"


def _clean_stage_name(value: Any) -> str:
    return " ".join(str(value or "").strip().split())[:120]


def _percent_label(percent: float) -> str:
    return f"{float(percent):g}%"


def _load_custom_presets() -> list[StagePreset]:
    path = _custom_preset_file()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = payload.get("custom_stages") if isinstance(payload, dict) else []
    if not isinstance(items, list):
        return []

    presets: list[StagePreset] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        stage_name = _clean_stage_name(item.get("stage_name"))
        if not stage_name:
            continue
        section = str(item.get("section") or "All").strip()
        if section not in BASE_STAGE_PRESET_SECTIONS:
            section = "All"
        try:
            percent = max(0.0, min(100.0, float(item.get("percent", 0.0) or 0.0)))
        except Exception:
            percent = 0.0
        key = (section.casefold(), stage_name.casefold())
        if key in seen:
            continue
        seen.add(key)
        label = f"Saved: {stage_name} — {_percent_label(percent)}"
        presets.append(StagePreset(label, stage_name, percent, section, custom=True))
    return presets


def _save_custom_preset(section: str, stage_name: str, percent: float) -> bool:
    clean_name = _clean_stage_name(stage_name)
    if not clean_name:
        return False
    if section not in BASE_STAGE_PRESET_SECTIONS:
        section = "All"
    try:
        clean_percent = max(0.0, min(100.0, float(percent or 0.0)))
    except Exception:
        clean_percent = 0.0

    path = _custom_preset_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
        else:
            payload = {}
        items = payload.get("custom_stages")
        if not isinstance(items, list):
            items = []

        key = (section.casefold(), clean_name.casefold())
        updated = False
        for item in items:
            if not isinstance(item, dict):
                continue
            item_key = (
                str(item.get("section") or "All").casefold(),
                _clean_stage_name(item.get("stage_name")).casefold(),
            )
            if item_key == key:
                item["percent"] = clean_percent
                item["updated_at"] = jobhub_now().strftime("%Y-%m-%d %H:%M:%S")
                updated = True
                break
        if not updated:
            items.append(
                {
                    "section": section,
                    "stage_name": clean_name,
                    "percent": clean_percent,
                    "created_at": jobhub_now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        payload["custom_stages"] = items
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return True
    except Exception:
        return False


def _is_add_stage_name_input(label: Any, kwargs: dict[str, Any]) -> bool:
    return (
        str(label or "") == "Stage Name"
        and "External Upper" in str(kwargs.get("placeholder") or "")
        and "value" not in kwargs
    )


def _is_add_stage_percent_input(label: Any, kwargs: dict[str, Any]) -> bool:
    widget_key = str(kwargs.get("key") or "")
    return (
        str(label or "") == "Job %"
        and widget_key.startswith("add_job_stage_percent_")
        and float(kwargs.get("value", 0.0) or 0.0) == 0.0
        and float(kwargs.get("max_value", 0.0) or 0.0) >= 100.0
    )


def _stage_sections() -> list[str]:
    return list(BASE_STAGE_PRESET_SECTIONS.keys())


def _custom_presets_for_section(section: str) -> list[StagePreset]:
    custom = _load_custom_presets()
    if section == "All":
        return custom
    return [preset for preset in custom if preset.section == section]


def _presets_for_section(section: str) -> list[StagePreset]:
    base = list(BASE_STAGE_PRESET_SECTIONS.get(section) or BASE_STAGE_PRESET_SECTIONS["All"])
    custom = _custom_presets_for_section(section)
    seen = {(preset.stage_name.casefold(), round(float(preset.percent), 4), preset.section) for preset in base}
    for preset in custom:
        key = (preset.stage_name.casefold(), round(float(preset.percent), 4), preset.section)
        if key not in seen:
            base.append(preset)
            seen.add(key)
    return base


def _preset_labels(section: str) -> list[str]:
    return [preset.label for preset in _presets_for_section(section)] + [ADD_CUSTOM_STAGE]


def _preset_for_choice(section: str, choice: str) -> StagePreset | None:
    for preset in _presets_for_section(section):
        if choice == preset.label or choice == preset.stage_name:
            return preset
    # If state holds a label from another section after a rerun, still resolve it
    # before the selector normalises on the next render.
    for section_name in _stage_sections():
        for preset in _presets_for_section(section_name):
            if choice == preset.label or choice == preset.stage_name:
                return preset
    return None


def _normalise_stage_state(st: Any, section: str) -> None:
    labels = _preset_labels(section)
    if st.session_state.get(_LAST_STAGE_SECTION_KEY) != section:
        st.session_state[_STAGE_CHOICE_KEY] = labels[0]
        st.session_state[_LAST_STAGE_SECTION_KEY] = section
        st.session_state[_LAST_STAGE_CHOICE_KEY] = None
    elif st.session_state.get(_STAGE_CHOICE_KEY) not in labels:
        st.session_state[_STAGE_CHOICE_KEY] = labels[0]
        st.session_state[_LAST_STAGE_CHOICE_KEY] = None


def _sync_percent_state(st: Any, section: str, choice: str) -> None:
    preset = _preset_for_choice(section, choice)
    state_key = f"{section}:{choice}"
    if st.session_state.get(_LAST_STAGE_CHOICE_KEY) != state_key:
        if preset is not None:
            st.session_state[_STAGE_PERCENT_KEY] = float(preset.percent)
            st.session_state["pb_stage_preset_selected_percent"] = float(preset.percent)
        elif choice == ADD_CUSTOM_STAGE:
            st.session_state[_STAGE_PERCENT_KEY] = float(
                st.session_state.get(_STAGE_PERCENT_KEY, 0.0) or 0.0
            )
            st.session_state["pb_stage_preset_selected_percent"] = 0.0
        st.session_state[_LAST_STAGE_CHOICE_KEY] = state_key


def _maybe_save_custom_preset(st: Any, percent: Any) -> None:
    if str(st.session_state.get("pb_stage_preset_selected_name") or "") != ADD_CUSTOM_STAGE:
        return
    if not bool(st.session_state.get(_SAVE_CUSTOM_KEY, False)):
        return
    section = str(st.session_state.get("pb_stage_preset_selected_section") or "All")
    stage_name = _clean_stage_name(st.session_state.get(_STAGE_CUSTOM_KEY))
    if not stage_name:
        return
    saved = _save_custom_preset(section, stage_name, float(percent or 0.0))
    st.session_state[_SAVE_STATUS_KEY] = (
        f"Saved custom stage for future: {stage_name} ({section}, {_percent_label(float(percent or 0.0))})."
        if saved
        else "Could not save the custom stage preset. Check persistent storage and try again."
    )


def _render_stage_name_selector(
    st: Any,
    selectbox_fn: Callable[..., Any],
    text_input_fn: Callable[..., Any],
    checkbox_fn: Callable[..., Any] | None,
    caption_fn: Callable[..., Any] | None,
) -> str:
    if callable(caption_fn):
        caption_fn(
            "Stage section: choose All, Internal or External. Use the 100% Internal/External/Whole job "
            "options when a job does not need detailed stage breakdowns."
        )

    section_options = _stage_sections()
    current_section = str(st.session_state.get(_STAGE_SECTION_KEY) or "All")
    if current_section not in section_options:
        current_section = "All"
        st.session_state[_STAGE_SECTION_KEY] = current_section
    section = selectbox_fn(
        "Stage section",
        section_options,
        index=section_options.index(current_section),
        key=_STAGE_SECTION_KEY,
        help="All shows simple 100% options and all detailed stages. Internal and External narrow the list.",
    )

    section = str(section)
    _normalise_stage_state(st, section)
    labels = _preset_labels(section)
    choice = selectbox_fn(
        "Stage selection",
        labels,
        index=labels.index(st.session_state.get(_STAGE_CHOICE_KEY, labels[0])),
        key=_STAGE_CHOICE_KEY,
        help="Pick a simple 100% stage or a detailed preset. Add item not listed lets you type a custom stage.",
    )
    choice = str(choice)
    preset = _preset_for_choice(section, choice)
    st.session_state["pb_stage_preset_selected_section"] = section
    st.session_state["pb_stage_preset_selected_name"] = choice
    _sync_percent_state(st, section, choice)

    if callable(caption_fn) and st.session_state.get(_SAVE_STATUS_KEY):
        caption_fn(str(st.session_state.get(_SAVE_STATUS_KEY)))

    if preset is not None:
        return preset.stage_name

    custom_name = str(
        text_input_fn(
            "Custom Stage Name",
            value=str(st.session_state.get(_STAGE_CUSTOM_KEY, "") or ""),
            key=_STAGE_CUSTOM_KEY,
            placeholder="Type the custom stage name",
        )
        or ""
    )
    if callable(checkbox_fn):
        checkbox_fn(
            "Save this custom stage for future",
            value=bool(st.session_state.get(_SAVE_CUSTOM_KEY, False)),
            key=_SAVE_CUSTOM_KEY,
            help="Adds this custom stage to the Stage selection dropdown for future jobs.",
        )
    elif callable(caption_fn):
        caption_fn("Custom stages can be saved for future once this form is loaded outside a column.")
    return custom_name


def _stage_percent_kwargs(st: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    section = str(st.session_state.get("pb_stage_preset_selected_section") or "All")
    selected = str(st.session_state.get("pb_stage_preset_selected_name") or "")
    preset = _preset_for_choice(section, selected)
    new_kwargs = dict(kwargs)
    new_kwargs.setdefault("key", _STAGE_PERCENT_KEY)
    if preset is not None:
        _sync_percent_state(st, section, selected)
        new_kwargs["value"] = float(preset.percent)
        new_kwargs.setdefault(
            "help",
            "Auto-filled from the selected stage preset. You can still change it before saving.",
        )
    elif selected == ADD_CUSTOM_STAGE:
        new_kwargs.setdefault("help", "Enter the job percentage for the custom stage.")
    return new_kwargs


def install_stage_preset_guard() -> bool:
    st = sys.modules.get("streamlit")
    if st is None:
        return False

    installed = False

    # Patch top-level st.* calls for any non-column form layouts.
    original_text_input = getattr(st, "text_input", None)
    original_number_input = getattr(st, "number_input", None)
    original_selectbox = getattr(st, "selectbox", None)
    original_checkbox = getattr(st, "checkbox", None)
    original_caption = getattr(st, "caption", None)

    if (
        original_text_input is not None
        and original_number_input is not None
        and original_selectbox is not None
        and not getattr(original_text_input, "_pb_stage_preset_guard", False)
    ):
        def pb_stage_preset_text_input(label: Any, *args: Any, **kwargs: Any):
            if _is_add_stage_name_input(label, kwargs):
                return _render_stage_name_selector(
                    st,
                    original_selectbox,
                    original_text_input,
                    original_checkbox,
                    original_caption,
                )
            return original_text_input(label, *args, **kwargs)

        def pb_stage_preset_number_input(label: Any, *args: Any, **kwargs: Any):
            if _is_add_stage_percent_input(label, kwargs):
                kwargs = _stage_percent_kwargs(st, kwargs)
                value = original_number_input(label, *args, **kwargs)
                _maybe_save_custom_preset(st, value)
                return value
            return original_number_input(label, *args, **kwargs)

        pb_stage_preset_text_input._pb_stage_preset_guard = True
        pb_stage_preset_text_input._pb_original_text_input = original_text_input
        pb_stage_preset_number_input._pb_stage_preset_guard = True
        pb_stage_preset_number_input._pb_original_number_input = original_number_input
        st.text_input = pb_stage_preset_text_input
        st.number_input = pb_stage_preset_number_input
        installed = True

    # Patch DeltaGenerator methods so column widgets such as a1.text_input and
    # a3.number_input get the same preset behaviour.
    delta_module = sys.modules.get("streamlit.delta_generator")
    delta_cls = getattr(delta_module, "DeltaGenerator", None) if delta_module is not None else None
    if delta_cls is not None:
        dg_text_input = getattr(delta_cls, "text_input", None)
        dg_number_input = getattr(delta_cls, "number_input", None)
        dg_selectbox = getattr(delta_cls, "selectbox", None)
        dg_checkbox = getattr(delta_cls, "checkbox", None)
        dg_caption = getattr(delta_cls, "caption", None)

        if (
            dg_text_input is not None
            and dg_number_input is not None
            and dg_selectbox is not None
            and not getattr(dg_text_input, "_pb_stage_preset_guard", False)
        ):
            def pb_dg_stage_preset_text_input(self: Any, label: Any, *args: Any, **kwargs: Any):
                if _is_add_stage_name_input(label, kwargs):
                    caption_fn = (lambda message: dg_caption(self, message)) if callable(dg_caption) else None
                    checkbox_fn = (lambda *a, **k: dg_checkbox(self, *a, **k)) if callable(dg_checkbox) else None
                    return _render_stage_name_selector(
                        st,
                        lambda *a, **k: dg_selectbox(self, *a, **k),
                        lambda *a, **k: dg_text_input(self, *a, **k),
                        checkbox_fn,
                        caption_fn,
                    )
                return dg_text_input(self, label, *args, **kwargs)

            def pb_dg_stage_preset_number_input(self: Any, label: Any, *args: Any, **kwargs: Any):
                if _is_add_stage_percent_input(label, kwargs):
                    kwargs = _stage_percent_kwargs(st, kwargs)
                    value = dg_number_input(self, label, *args, **kwargs)
                    _maybe_save_custom_preset(st, value)
                    return value
                return dg_number_input(self, label, *args, **kwargs)

            pb_dg_stage_preset_text_input._pb_stage_preset_guard = True
            pb_dg_stage_preset_text_input._pb_original_text_input = dg_text_input
            pb_dg_stage_preset_number_input._pb_stage_preset_guard = True
            pb_dg_stage_preset_number_input._pb_original_number_input = dg_number_input
            delta_cls.text_input = pb_dg_stage_preset_text_input
            delta_cls.number_input = pb_dg_stage_preset_number_input
            installed = True

    return installed
