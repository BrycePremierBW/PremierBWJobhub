from __future__ import annotations

import pandas as pd

from jobhub.timesheet_bulk_reassign import _is_timesheet_selector, _selected_ids


def _timesheet_frame():
    return pd.DataFrame(
        [
            {"Select": True, "id": 11, "Employee": "A", "Date": "2026-08-20", "Hours": 8.0, "Status": "Submitted"},
            {"Select": False, "id": 12, "Employee": "B", "Date": "2026-08-20", "Hours": 8.0, "Status": "Approved"},
            {"Select": True, "id": 13, "Employee": "C", "Date": "2026-08-21", "Hours": 7.5, "Status": "Paid"},
        ]
    )


def test_timesheet_selector_recognises_existing_bulk_table():
    frame = _timesheet_frame()
    assert _is_timesheet_selector("admin_timesheets_checkbox_table_abc123", frame)


def test_non_timesheet_editor_is_not_patched():
    frame = pd.DataFrame([{"Select": True, "id": 1, "Status": "Active", "Name": "Other"}])
    assert not _is_timesheet_selector("other_checkbox_table_abc123", frame)


def test_selected_ids_preserve_multiple_people_and_dates():
    assert _selected_ids(_timesheet_frame()) == [11, 13]


def test_no_selection_returns_empty_list():
    frame = _timesheet_frame()
    frame["Select"] = False
    assert _selected_ids(frame) == []
