import pandas as pd

from jobhub import subscriber_import_safety_guard as guard


def test_template_csv_has_expected_canonical_headers():
    text = guard.template_csv_bytes("employees").decode("utf-8")
    assert text.startswith("name,role,phone,email,base_hourly_rate,status,notes")


def test_partition_rows_separates_new_existing_and_within_file_duplicates():
    rows = [
        {"name": "Alex Smith", "role": "Painter"},
        {"name": "Jordan Lee", "role": "Painter"},
        {"name": "Jordan Lee", "role": "Leading Hand"},
        {"name": "Taylor Brown", "role": "Apprentice"},
    ]
    new_rows, matched_rows, duplicates = guard.partition_rows(
        "employees",
        rows,
        {"alex smith"},
    )
    assert [row["name"] for row in new_rows] == ["Jordan Lee", "Taylor Brown"]
    assert [row["name"] for row in matched_rows] == ["Alex Smith"]
    assert duplicates == [3]


def test_product_identity_uses_code_or_name_fallback():
    coded = {"product_code": "PB-001", "product_name": "Low Sheen"}
    uncoded = {"product_code": "", "product_name": "Ceiling White"}
    assert guard._row_identity("products", coded) == "pb-001"
    assert guard._row_identity("products", uncoded) == "ceiling white"


def test_existing_keys_are_loaded_from_jobhub_tables(monkeypatch):
    def fake_query(sql, params=()):
        if "FROM employees" in sql:
            return pd.DataFrame([{"name": "Alex Smith"}])
        if "FROM builders_clients" in sql:
            return pd.DataFrame([{"name": "Acme Builders"}])
        return pd.DataFrame([
            {"product_code": "P-1", "product_name": "Product One"},
            {"product_code": "", "product_name": "Product Two"},
        ])

    monkeypatch.setattr(guard.subscriber_setup_guard, "_df_query", fake_query)
    assert guard._existing_keys("employees") == {"alex smith"}
    assert guard._existing_keys("builders_clients") == {"acme builders"}
    assert guard._existing_keys("products") == {"p-1", "product two"}


def test_guard_replaces_import_renderer_only_once(monkeypatch):
    calls = []

    def original(st, entity, label):
        calls.append("original")

    monkeypatch.setattr(guard.subscriber_setup_guard, "_render_import_panel", original)
    monkeypatch.setattr(guard, "_render_import_panel", lambda st, entity, label: calls.append((entity, label)))
    assert guard.install_subscriber_import_safety_guard() is True
    guard.subscriber_setup_guard._render_import_panel(object(), "employees", "Employees")
    assert calls == [("employees", "Employees")]
    assert guard.install_subscriber_import_safety_guard() is False
