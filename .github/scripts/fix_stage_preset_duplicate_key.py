from pathlib import Path


app_path = Path("pb_jobhub_app.py")
app = app_path.read_text(encoding="utf-8")

add_old = '''            stage_percent = a3.number_input(
                "Job %",
                min_value=0.0,
                max_value=100.0,
                step=1.0,
                value=0.0,
            )'''
add_new = '''            stage_percent = a3.number_input(
                "Job %",
                min_value=0.0,
                max_value=100.0,
                step=1.0,
                value=0.0,
                key=f"add_job_stage_percent_{job_id}",
            )'''
if app.count(add_old) != 1:
    raise SystemExit(f"Expected one Add Stage Job % widget, found {app.count(add_old)}")
app = app.replace(add_old, add_new, 1)

edit_old = '''        edit_percent = e3.number_input(
            "Job %",
            min_value=0.0,
            max_value=100.0,
            step=1.0,
            value=float(selected["Job %"] or 0),
        )'''
edit_new = '''        edit_percent = e3.number_input(
            "Job %",
            min_value=0.0,
            max_value=100.0,
            step=1.0,
            value=float(selected["Job %"] or 0),
            key=f"edit_job_stage_percent_{stage_id}",
        )'''
if app.count(edit_old) != 1:
    raise SystemExit(f"Expected one Edit Stage Job % widget, found {app.count(edit_old)}")
app = app.replace(edit_old, edit_new, 1)
app_path.write_text(app, encoding="utf-8")

guard_path = Path("jobhub/stage_preset_guard.py")
guard = guard_path.read_text(encoding="utf-8")
guard_old = '''def _is_add_stage_percent_input(label: Any, kwargs: dict[str, Any]) -> bool:
    return (
        str(label or "") == "Job %"
        and float(kwargs.get("value", 0.0) or 0.0) == 0.0
        and float(kwargs.get("max_value", 0.0) or 0.0) >= 100.0
    )'''
guard_new = '''def _is_add_stage_percent_input(label: Any, kwargs: dict[str, Any]) -> bool:
    widget_key = str(kwargs.get("key") or "")
    return (
        str(label or "") == "Job %"
        and widget_key.startswith("add_job_stage_percent_")
        and float(kwargs.get("value", 0.0) or 0.0) == 0.0
        and float(kwargs.get("max_value", 0.0) or 0.0) >= 100.0
    )'''
if guard.count(guard_old) != 1:
    raise SystemExit(f"Expected one stage percent matcher, found {guard.count(guard_old)}")
guard = guard.replace(guard_old, guard_new, 1)
guard_path.write_text(guard, encoding="utf-8")

test_path = Path("tests/test_stage_preset_duplicate_key.py")
test_path.write_text(
    '''from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_stage_percent_widgets_have_distinct_scoped_keys():
    source = (ROOT / "pb_jobhub_app.py").read_text(encoding="utf-8")
    assert 'key=f"add_job_stage_percent_{job_id}"' in source
    assert 'key=f"edit_job_stage_percent_{stage_id}"' in source


def test_preset_guard_only_matches_add_stage_percent_widget():
    source = (ROOT / "jobhub" / "stage_preset_guard.py").read_text(encoding="utf-8")
    assert 'widget_key.startswith("add_job_stage_percent_")' in source
    assert '_STAGE_PERCENT_KEY = "pb_stage_preset_job_percent"' in source
''',
    encoding="utf-8",
)
