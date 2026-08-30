from jobhub.timesheet_area_guard import (
    AREA_OPTIONS,
    LEAVE_OPTIONS,
    _combine_area_and_work_type,
    _looks_like_employee_home_work_type,
    _looks_like_timesheet_work_type,
    _patch_selectbox,
    _with_leave_options,
)


STANDARD_WORK = ["Painting", "Prep", "Spraying", "Touch-ups", "Site Setup", "Other"]


class FakeSelectboxOwner:
    def __init__(self):
        self.calls = []

    def selectbox(self, label, options, *args, **kwargs):
        values = list(options)
        self.calls.append((label, values, dict(kwargs)))
        if label == "Area Worked":
            return "All"
        if "Annual Leave" in values:
            return "Annual Leave"
        return values[0] if values else None


def test_leave_options_append_once_and_preserve_existing_order():
    augmented = _with_leave_options(STANDARD_WORK)
    assert augmented[: len(STANDARD_WORK)] == STANDARD_WORK
    assert augmented[-2:] == ["Sick Day", "Annual Leave"]

    already_present = _with_leave_options(augmented)
    assert already_present == augmented
    assert already_present.count("Sick Day") == 1
    assert already_present.count("Annual Leave") == 1


def test_full_timesheet_work_type_is_recognised_and_gets_leave_choices():
    kwargs = {"key": "employee_12_work_type"}
    assert _looks_like_timesheet_work_type("Work Type", (STANDARD_WORK,), kwargs)

    owner = FakeSelectboxOwner()
    assert _patch_selectbox(owner)
    selected = owner.selectbox("Work Type", STANDARD_WORK, key="employee_12_work_type")

    assert selected == "All — Annual Leave"
    work_call = next(call for call in owner.calls if call[0] == "Work Type")
    assert "Sick Day" in work_call[1]
    assert "Annual Leave" in work_call[1]
    assert any(call[0] == "Area Worked" and call[1] == AREA_OPTIONS for call in owner.calls)


def test_simplified_employee_home_work_selector_gets_leave_choices_without_extra_area_selector():
    kwargs = {"key": "pb_home_timesheet_work"}
    assert _looks_like_employee_home_work_type("WORK", (STANDARD_WORK,), kwargs)

    owner = FakeSelectboxOwner()
    assert _patch_selectbox(owner)
    selected = owner.selectbox("WORK", STANDARD_WORK, key="pb_home_timesheet_work")

    assert selected == "Annual Leave"
    work_call = next(call for call in owner.calls if call[0] == "WORK")
    assert work_call[1][-2:] == LEAVE_OPTIONS
    assert not any(call[0] == "Area Worked" for call in owner.calls)


def test_unrelated_selectboxes_are_not_modified():
    owner = FakeSelectboxOwner()
    assert _patch_selectbox(owner)
    jobs = ["PB26001", "PB26002"]
    selected = owner.selectbox("JOB", jobs, key="pb_home_timesheet_job")

    assert selected == "PB26001"
    assert owner.calls[-1][1] == jobs
    assert not any(leave in owner.calls[-1][1] for leave in LEAVE_OPTIONS)


def test_leave_work_type_remains_compatible_with_existing_area_text_storage():
    assert _combine_area_and_work_type("All", "Sick Day") == "All — Sick Day"
    assert _combine_area_and_work_type("All", "Annual Leave") == "All — Annual Leave"
    assert _combine_area_and_work_type("External", "Painting") == "External — Painting"
