"""Keep JobHub stage preset options visible across stage sections.

The stage preset selector has a Stage section control with All/Internal/External.
That originally narrowed the stage dropdown. In practice it made most options
look like they disappeared. This guard keeps the full preset list available for
all sections while still preserving the section value for custom presets.
"""

from __future__ import annotations

from typing import Any

from . import stage_preset_guard


PATCH_MARKER = "_pb_stage_preset_visibility_guard"


def _dedupe_presets(presets: list[Any]) -> list[Any]:
    seen: set[tuple[str, str, float]] = set()
    result: list[Any] = []
    for preset in presets:
        key = (
            str(getattr(preset, "section", "") or "").casefold(),
            str(getattr(preset, "stage_name", "") or "").casefold(),
            round(float(getattr(preset, "percent", 0.0) or 0.0), 4),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(preset)
    return result


def install_stage_preset_visibility_guard() -> bool:
    original_presets_for_section = getattr(stage_preset_guard, "_presets_for_section", None)
    original_normalise = getattr(stage_preset_guard, "_normalise_stage_state", None)
    if original_presets_for_section is None or getattr(original_presets_for_section, PATCH_MARKER, False):
        return False

    def presets_for_section_without_hiding(section: str) -> list[Any]:
        """Return all presets so changing section never hides stage options."""
        all_presets = list(original_presets_for_section("All"))
        if str(section or "All") != "All":
            all_presets.extend(list(original_presets_for_section(str(section))))
        return _dedupe_presets(all_presets)

    def normalise_without_resetting_visible_choice(st: Any, section: str) -> None:
        labels = stage_preset_guard._preset_labels(section)
        if st.session_state.get(stage_preset_guard._STAGE_CHOICE_KEY) not in labels:
            st.session_state[stage_preset_guard._STAGE_CHOICE_KEY] = labels[0]
            st.session_state[stage_preset_guard._LAST_STAGE_CHOICE_KEY] = None
        st.session_state[stage_preset_guard._LAST_STAGE_SECTION_KEY] = section

    presets_for_section_without_hiding._pb_stage_preset_visibility_guard = True
    presets_for_section_without_hiding._pb_original = original_presets_for_section
    stage_preset_guard._presets_for_section = presets_for_section_without_hiding

    if original_normalise is not None:
        normalise_without_resetting_visible_choice._pb_stage_preset_visibility_guard = True
        normalise_without_resetting_visible_choice._pb_original = original_normalise
        stage_preset_guard._normalise_stage_state = normalise_without_resetting_visible_choice

    return True
