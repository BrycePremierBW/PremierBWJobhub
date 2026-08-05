from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_stage_setup_uses_plain_language_labels():
    source = (ROOT / "jobhub" / "stage_setup_simplifier_guard.py").read_text(
        encoding="utf-8"
    )
    expected = [
        "Add stages for multiple dwellings",
        "First dwelling",
        "Last dwelling",
        "Number of dwellings",
        "Interior share of whole job (%)",
        "Share of whole job (%)",
    ]
    for label in expected:
        assert label in source


def test_multi_dwelling_builder_is_injected_before_empty_stage_return():
    source = (ROOT / "jobhub" / "stage_setup_simplifier_guard.py").read_text(
        encoding="utf-8"
    )
    assert "INTRO_CAPTION" in source
    assert "_inject_builder(st)" in source
    assert "_RENDERED_JOBS.discard(job_id)" in source
    assert "dwelling_builder._render_bulk_dwelling_stage_builder(st, job_id)" in source


def test_simplifier_installs_after_existing_stage_guards():
    source = (ROOT / "jobhub" / "__init__.py").read_text(encoding="utf-8")
    assert "from .stage_setup_simplifier_guard import install_stage_setup_simplifier_guard" in source
    assert source.index("install_stage_dwelling_builder_guard()") < source.index(
        "install_stage_setup_simplifier_guard()"
    )
    assert source.index("install_stage_preset_visibility_guard()") < source.index(
        "install_stage_setup_simplifier_guard()"
    )
