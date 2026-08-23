from datetime import time

from jobhub.employee_portal_home_guard import calculate_hours
from jobhub.navigation_simplifier_guard import filtered_main_menu
from jobhub.po_admin_only_guard import filter_po_options, is_po_sensitive_text


def test_management_navigation_is_one_dashboard():
    options = [
        "Dashboard",
        "Control Centre",
        "Operations Hub",
        "Jobs",
        "Job Folders",
        "Upload PO",
        "Management",
    ]
    admin = filtered_main_menu(options, "admin")
    assert "Dashboard" in admin
    assert "Control Centre" not in admin
    assert "Operations Hub" not in admin
    assert "Upload PO" in admin

    manager = filtered_main_menu(options, "manager")
    assert "Control Centre" not in manager
    assert "Operations Hub" not in manager
    assert "Upload PO" not in manager


def test_purchase_order_labels_are_admin_only():
    options = [
        "Job Control",
        "Purchasing",
        "Purchase Orders",
        "Upload PO",
        "Timesheets",
        "Shared PO",
        "Photos",
    ]
    assert filter_po_options(options, True) == options
    assert filter_po_options(options, False) == ["Job Control", "Timesheets", "Photos"]
    for value in (
        "Purchasing",
        "Procurement",
        "Purchase Orders",
        "Upload PO",
        "PO Number",
        "Shared PO",
        "purchase_order_id",
    ):
        assert is_po_sensitive_text(value)
    assert not is_po_sensitive_text("Employee Portal")
    assert not is_po_sensitive_text("Photos")


def test_simple_timesheet_hours_use_zero_break_by_default():
    assert calculate_hours(time(7, 0), time(15, 0)) == 8.0
    assert calculate_hours(time(7, 0), time(15, 0), 30) == 7.5
    assert calculate_hours(time(22, 0), time(6, 0), 0) == 8.0
