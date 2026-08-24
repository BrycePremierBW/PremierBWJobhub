from jobhub.persistent_login import _is_login_form_key


def test_desktop_login_form_detected():
    assert _is_login_form_key("login_form")


def test_mobile_login_form_detected():
    assert _is_login_form_key("mobile_login_form")


def test_employee_login_form_detected():
    assert _is_login_form_key("employee-login-form")


def test_unrelated_form_not_detected():
    assert not _is_login_form_key("timesheet_form")
    assert not _is_login_form_key("job_edit_form")
