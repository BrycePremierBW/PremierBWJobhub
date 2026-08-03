"""Smoke tests for stage preset visibility guard."""

from pathlib import Path
import ast


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_stage_preset_visibility_guard_source_parses():
    ast.parse(read("jobhub/stage_preset_visibility_guard.py"), filename="jobhub/stage_preset_visibility_guard.py")


def test_stage_preset_visibility_guard_is_installed():
    init_source = read("jobhub/__init__.py")
    assert "from .stage_preset_visibility_guard import install_stage_preset_visibility_guard" in init_source
    assert "install_stage_preset_visibility_guard()" in init_source


def test_stage_preset_visibility_guard_keeps_all_options_visible():
    source = read("jobhub/stage_preset_visibility_guard.py")
    required = [
        "Keep JobHub stage preset options visible across stage sections",
        "Return all presets so changing section never hides stage options",
        "original_presets_for_section(\"All\")",
        "normalise_without_resetting_visible_choice",
        "_STAGE_CHOICE_KEY",
        "_LAST_STAGE_SECTION_KEY",
        "install_stage_preset_visibility_guard",
    ]
    for marker in required:
        assert marker in source
