"""Relax the Job Stage preset hook so the dropdown appears reliably.

The original hook only matched an Add Stage field with a very specific
placeholder. Some forms do not use that exact placeholder, so this guard makes
the stage preset selector attach to any new blank "Stage Name" field while still
leaving edit forms alone.
"""

from __future__ import annotations

from typing import Any

from . import stage_preset_guard


def install_stage_preset_selector_fix_guard() -> bool:
    original = getattr(stage_preset_guard, "_is_add_stage_name_input", None)
    if getattr(original, "_pb_relaxed_stage_selector", False):
        return False

    def relaxed_stage_name_input(label: Any, kwargs: dict[str, Any]) -> bool:
        return (
            str(label or "").strip().casefold() == "stage name"
            and "value" not in kwargs
        )

    relaxed_stage_name_input._pb_relaxed_stage_selector = True
    relaxed_stage_name_input._pb_original = original
    stage_preset_guard._is_add_stage_name_input = relaxed_stage_name_input
    return True
