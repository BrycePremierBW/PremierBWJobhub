from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_work_area_control_is_outside_the_form():
    source = (ROOT / "jobhub" / "stage_scope_refresh_guard.py").read_text(
        encoding="utf-8"
    )
    assert source.index('st.selectbox(\n                "Work area"') < source.index(
        'with st.form(f"bulk_dwelling_stage_builder_{job_id}"'
    )


def test_painting_stage_state_is_scoped_by_work_area():
    source = (ROOT / "jobhub" / "stage_scope_refresh_guard.py").read_text(
        encoding="utf-8"
    )
    assert "step_key = f\"bulk_stage_steps_{job_id}_{_scope_widget_suffix(scope)}\"" in source
    assert 'steps = builder._stage_steps(scope)' in source
    assert '"Painting stages"' in source


def test_scope_refresh_installs_before_stage_simplifier():
    source = (ROOT / "jobhub" / "__init__.py").read_text(encoding="utf-8")
    assert "from .stage_scope_refresh_guard import install_stage_scope_refresh_guard" in source
    assert source.index("install_stage_dwelling_builder_guard()") < source.index(
        "install_stage_scope_refresh_guard()"
    )
    assert source.index("install_stage_scope_refresh_guard()") < source.index(
        "install_stage_setup_simplifier_guard()"
    )


def test_only_the_selected_area_percentage_is_shown():
    source = (ROOT / "jobhub" / "stage_scope_refresh_guard.py").read_text(
        encoding="utf-8"
    )
    assert 'if scope == "Interior":' in source
    assert 'elif scope == "Exterior":' in source
    assert '"Interior share of whole job (%)"' in source
    assert '"Exterior share of whole job (%)"' in source
