"""Stage preset helpers for Premier Brushworks JobHub.

This guard keeps the stage workflow safe by adding the requested Premier
Brushworks stage presets to the existing Add Stage form. The stage form uses
Streamlit column widgets (``a1.text_input`` / ``a3.number_input``), so this
module patches both the top-level ``st`` helpers and DeltaGenerator column
methods.
"""

from __future__ import annotations

import sys
from typing import Any, Callable


STAGE_PRESET_SECTIONS: dict[str, list[tuple[str, float]]] = {
    "Internal": [
        ("Internal prep and spray Sealer", 30.0),
        ("prep and spray finish coats", 30.0),
        ("cut and roll walls and paint doors", 30.0),
        ("touch ups", 10.0),
    ],
    "External": [
        ("upper scaff work", 45.0),
        ("lower", 45.0),
        ("touch ups", 10.0),
    ],
}

ADD_CUSTOM_STAGE = "Add item not listed"
_STAGE_SECTION_KEY = "pb_stage_preset_section"
_STAGE_CHOICE_KEY = "pb_stage_preset_stage_name_choice"
_STAGE_CUSTOM_KEY = "pb_stage_preset_custom_stage_name"
_STAGE_PERCENT_KEY = "pb_stage_preset_job_percent"
_LAST_STAGE_CHOICE_KEY = "pb_stage_preset_last_choice"
_LAST_STAGE_SECTION_KEY = "pb_stage_preset_last_section"


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


def _stage_sections() -> list[str]:
    return list(STAGE_PRESET_SECTIONS.keys())


def _preset_labels(section: str) -> list[str]:
    presets = STAGE_PRESET_SECTIONS.get(section) or STAGE_PRESET_SECTIONS["Internal"]
    return [name for name, _ in presets] + [ADD_CUSTOM_STAGE]


def _preset_percent(stage_name: str, section: str | None = None) -> float | None:
    sections = [section] if section in STAGE_PRESET_SECTIONS else _stage_sections()
    for section_name in sections:
        for preset_name, percent in STAGE_PRESET_SECTIONS[section_name]:
            if stage_name == preset_name:
                return percent
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
    percent = _preset_percent(choice, section)
    state_key = f"{section}:{choice}"
    if st.session_state.get(_LAST_STAGE_CHOICE_KEY) != state_key:
        if percent is not None:
            st.session_state[_STAGE_PERCENT_KEY] = float(percent)
            st.session_state["pb_stage_preset_selected_percent"] = float(percent)
        elif choice == ADD_CUSTOM_STAGE:
            st.session_state[_STAGE_PERCENT_KEY] = float(
                st.session_state.get(_STAGE_PERCENT_KEY, 0.0) or 0.0
            )
            st.session_state["pb_stage_preset_selected_percent"] = 0.0
        st.session_state[_LAST_STAGE_CHOICE_KEY] = state_key


def _render_stage_name_selector(
    st: Any,
    selectbox_fn: Callable[..., Any],
    text_input_fn: Callable[..., Any],
    caption_fn: Callable[..., Any] | None,
) -> str:
    if callable(caption_fn):
        caption_fn(
            "Stage section: choose Internal or External, then select a standard "
            "Premier Brushworks stage or choose Add item not listed."
        )

    section_options = _stage_sections()
    current_section = str(st.session_state.get(_STAGE_SECTION_KEY) or "Internal")
    if current_section not in section_options:
        current_section = "Internal"
        st.session_state[_STAGE_SECTION_KEY] = current_section
    section = selectbox_fn(
        "Stage section",
        section_options,
        index=section_options.index(current_section),
        key=_STAGE_SECTION_KEY,
        help="Internal shows sealer, finish coats, walls/doors and touch-ups. External shows upper scaffold, lower and touch-ups.",
    )

    _normalise_stage_state(st, str(section))
    labels = _preset_labels(str(section))
    choice = selectbox_fn(
        "Stage selection",
        labels,
        index=labels.index(st.session_state.get(_STAGE_CHOICE_KEY, labels[0])),
        key=_STAGE_CHOICE_KEY,
        help="The selected stage automatically fills the Job % allowance.",
    )
    st.session_state["pb_stage_preset_selected_section"] = str(section)
    st.session_state["pb_stage_preset_selected_name"] = str(choice)
    _sync_percent_state(st, str(section), str(choice))

    if _preset_percent(str(choice), str(section)) is not None:
        return str(choice)
    return str(
        text_input_fn(
            "Custom Stage Name",
            value=str(st.session_state.get(_STAGE_CUSTOM_KEY, "") or ""),
            key=_STAGE_CUSTOM_KEY,
            placeholder="Type the custom stage name",
        )
        or ""
    )


def _stage_percent_kwargs(st: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    section = str(st.session_state.get("pb_stage_preset_selected_section") or "Internal")
    selected = str(st.session_state.get("pb_stage_preset_selected_name") or "")
    percent = _preset_percent(selected, section)
    new_kwargs = dict(kwargs)
    new_kwargs.setdefault("key", _STAGE_PERCENT_KEY)
    if percent is not None:
        _sync_percent_state(st, section, selected)
        new_kwargs["value"] = float(percent)
        new_kwargs.setdefault(
            "help",
            "Auto-filled from the selected standard stage. You can still change it before saving.",
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
                    original_caption,
                )
            return original_text_input(label, *args, **kwargs)

        def pb_stage_preset_number_input(label: Any, *args: Any, **kwargs: Any):
            if _is_add_stage_percent_input(label, kwargs):
                kwargs = _stage_percent_kwargs(st, kwargs)
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
                    return _render_stage_name_selector(
                        st,
                        lambda *a, **k: dg_selectbox(self, *a, **k),
                        lambda *a, **k: dg_text_input(self, *a, **k),
                        caption_fn,
                    )
                return dg_text_input(self, label, *args, **kwargs)

            def pb_dg_stage_preset_number_input(self: Any, label: Any, *args: Any, **kwargs: Any):
                if _is_add_stage_percent_input(label, kwargs):
                    kwargs = _stage_percent_kwargs(st, kwargs)
                return dg_number_input(self, label, *args, **kwargs)

            pb_dg_stage_preset_text_input._pb_stage_preset_guard = True
            pb_dg_stage_preset_text_input._pb_original_text_input = dg_text_input
            pb_dg_stage_preset_number_input._pb_stage_preset_guard = True
            pb_dg_stage_preset_number_input._pb_original_number_input = dg_number_input
            delta_cls.text_input = pb_dg_stage_preset_text_input
            delta_cls.number_input = pb_dg_stage_preset_number_input
            installed = True

    return installed
