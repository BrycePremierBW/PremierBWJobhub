import io

import pandas as pd

from jobhub import subscriber_setup_guard as guard


class Upload:
    def __init__(self, name: str, payload: bytes):
        self.name = name
        self._payload = payload

    def getvalue(self):
        return self._payload


def test_csv_upload_reader_round_trips_rows():
    upload = Upload("employees.csv", b"Employee Name,Mobile\nAlex Smith,0400000000\n")
    frame = guard._read_upload(upload)
    assert list(frame.columns) == ["Employee Name", "Mobile"]
    assert frame.iloc[0]["Employee Name"] == "Alex Smith"


def test_xlsx_upload_reader_round_trips_rows():
    source = pd.DataFrame([{"Product": "Low Sheen", "Price": 159.95}])
    buffer = io.BytesIO()
    source.to_excel(buffer, index=False, engine="openpyxl")
    upload = Upload("products.xlsx", buffer.getvalue())
    frame = guard._read_upload(upload)
    assert frame.iloc[0]["Product"] == "Low Sheen"
    assert float(frame.iloc[0]["Price"]) == 159.95


def test_safe_float_handles_blank_and_bad_values():
    assert guard._safe_float(65) == 65.0
    assert guard._safe_float("") == 0.0
    assert guard._safe_float("not-a-number") == 0.0


def test_setting_keys_are_namespaced_for_subscriber_setup():
    assert guard._setting_key("company_name") == "subscriber_company_name"
    assert guard._setting_key("xero_connected") == "subscriber_xero_connected"


def test_save_import_routes_only_supported_entities(monkeypatch):
    monkeypatch.setattr(guard, "_upsert_employees", lambda rows: 2)
    monkeypatch.setattr(guard, "_upsert_builders_clients", lambda rows: 3)
    monkeypatch.setattr(guard, "_upsert_products", lambda rows: 4)
    assert guard._save_import("employees", [{}, {}]) == 2
    assert guard._save_import("builders_clients", [{}, {}, {}]) == 3
    assert guard._save_import("products", [{}, {}, {}, {}]) == 4


def test_guard_wraps_existing_setup_page_once(monkeypatch):
    calls = []

    def original():
        calls.append("original")

    monkeypatch.setattr(guard.setup_defaults_guard, "render_setup_defaults_page", original)
    monkeypatch.setattr(guard, "render_subscriber_setup", lambda: calls.append("subscriber"))
    assert guard.install_subscriber_setup_guard() is True
    guard.setup_defaults_guard.render_setup_defaults_page()
    assert calls == ["original", "subscriber"]
    assert guard.install_subscriber_setup_guard() is False
