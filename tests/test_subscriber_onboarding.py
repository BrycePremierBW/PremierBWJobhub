import pandas as pd

from jobhub.subscriber_onboarding import (
    canonical_column_map,
    import_template,
    normalise_header,
    preview_import,
    setup_completion_percent,
    setup_health,
)


def test_normalise_header_and_alias_mapping():
    assert normalise_header("Employee Name") == "employee_name"
    mapping = canonical_column_map("employees", ["Employee Name", "Mobile", "Hourly Rate", "Notes"])
    assert mapping == {
        "Employee Name": "name",
        "Mobile": "phone",
        "Hourly Rate": "base_hourly_rate",
        "Notes": "notes",
    }


def test_employee_preview_validates_required_and_duplicates():
    df = pd.DataFrame(
        [
            {"Employee Name": "Alex Smith", "Mobile": "0400000000"},
            {"Employee Name": "Jordan Lee", "Mobile": "0411111111"},
            {"Employee Name": "Jordan Lee", "Mobile": "0422222222"},
            {"Employee Name": "", "Mobile": "0433333333"},
        ]
    )
    preview = preview_import("employees", df, existing_keys=["Alex Smith"])

    assert preview.mapped_columns["Employee Name"] == "name"
    assert 2 in preview.duplicate_rows
    assert 4 in preview.duplicate_rows
    assert any(issue.row_number == 5 and issue.field == "name" for issue in preview.issues)
    assert preview.accepted_rows == [3]


def test_product_preview_uses_name_when_code_missing():
    df = pd.DataFrame(
        [
            {"Product": "Interior Low Sheen", "Supplier": "Paint Co", "Price": 159.95},
            {"Product": "Interior Low Sheen", "Supplier": "Paint Co", "Price": 160.00},
        ]
    )
    preview = preview_import("products", df)

    assert preview.rows[0]["product_name"] == "Interior Low Sheen"
    assert preview.rows[0]["price_ex_gst"] == 159.95
    assert preview.duplicate_rows == [3]
    assert preview.accepted_rows == [2]


def test_missing_required_column_is_reported_before_import():
    preview = preview_import("builders_clients", pd.DataFrame([{"Phone": "0400"}]))
    assert preview.rows == []
    assert any(issue.row_number == 0 and issue.field == "name" for issue in preview.issues)
    assert not preview.ready_to_import


def test_templates_expose_canonical_columns():
    employees = import_template("employees")
    products = import_template("products")
    assert list(employees.columns)[0] == "name"
    assert "base_hourly_rate" in employees.columns
    assert list(products.columns)[0] == "product_name"
    assert "price_ex_gst" in products.columns


def test_setup_health_and_completion_percent():
    health = setup_health(
        company_profile={"company_name": "Coastal Coatings", "logo_present": True},
        employee_count=12,
        builder_client_count=20,
        product_count=400,
        xero_connected=True,
        rates_configured=True,
        stages_configured=False,
        notifications_configured=False,
    )
    assert health["company_profile"] is True
    assert health["logo"] is True
    assert health["employees"] is True
    assert health["job_stages"] is False
    assert setup_completion_percent(health) == 78
