from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_stage_percent_widgets_have_distinct_scoped_keys():
    source = (ROOT / "pb_jobhub_app.py").read_text(encoding="utf-8")
    assert 'key=f"add_job_stage_percent_{job_id}"' in source
    assert 'key=f"edit_job_stage_percent_{stage_id}"' in source


def test_preset_guard_only_matches_add_stage_percent_widget():
    source = (ROOT / "jobhub" / "stage_preset_guard.py").read_text(encoding="utf-8")
    assert 'widget_key.startswith("add_job_stage_percent_")' in source
    assert '_STAGE_PERCENT_KEY = "pb_stage_preset_job_percent"' in source
