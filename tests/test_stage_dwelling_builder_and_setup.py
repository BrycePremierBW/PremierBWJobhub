"""Smoke tests for JobHub setup defaults and dwelling stage builder."""

from pathlib import Path
import ast


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_setup_defaults_guard_source_parses():
    ast.parse(read("jobhub/setup_defaults_guard.py"), filename="jobhub/setup_defaults_guard.py")


def test_stage_dwelling_builder_guard_source_parses():
    ast.parse(read("jobhub/stage_dwelling_builder_guard.py"), filename="jobhub/stage_dwelling_builder_guard.py")


def test_setup_defaults_page_is_installed():
    init_source = read("jobhub/__init__.py")
    assert "from .setup_defaults_guard import install_setup_defaults_guard" in init_source
    assert "install_setup_defaults_guard()" in init_source
    source = read("jobhub/setup_defaults_guard.py")
    required = [
        "JobHub Setup / Edit Defaults",
        "Default estimating staff cost / hour",
        "Default charge-out / all-in hourly rate",
        "Default painter-day hours",
        "Default production target per painter-day",
        "Default internal % of job",
        "Default external % of job",
        "Default dwelling count",
        "jobhub_crews",
        "jobhub_crew_members",
        "Crew members",
    ]
    for marker in required:
        assert marker in source


def test_dwelling_stage_builder_is_installed():
    init_source = read("jobhub/__init__.py")
    assert "from .stage_dwelling_builder_guard import install_stage_dwelling_builder_guard" in init_source
    assert "install_stage_dwelling_builder_guard()" in init_source
    source = read("jobhub/stage_dwelling_builder_guard.py")
    required = [
        "Build by dwelling / estimate line",
        "Interior",
        "Exterior",
        "Dwelling / unit",
        "Prep and seal",
        "Finish coats",
        "Upper scaff work",
        "Lower external",
        "Quick add dwelling / estimate stages",
        "Optional estimate line to link",
        "Interior - Dwelling 6 - Prep and seal",
        "estimate_line_items SET job_stage_id",
        "selectable_job_stages_",
    ]
    for marker in required:
        assert marker in source


def test_dwelling_stage_builder_reads_setup_defaults():
    source = read("jobhub/stage_dwelling_builder_guard.py")
    required = [
        "default_internal_weight_percent",
        "default_external_weight_percent",
        "default_dwelling_count",
        "stage_interior_prep_seal_percent",
        "stage_exterior_upper_scaff_percent",
        "STAGE_BUILDER_PERCENT_KEY",
        "Auto-filled by the dwelling stage builder",
    ]
    for marker in required:
        assert marker in source
