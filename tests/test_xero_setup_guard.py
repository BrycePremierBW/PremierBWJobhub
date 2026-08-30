import pandas as pd

from jobhub import xero_setup_guard as guard


def test_server_setting_prefers_environment(monkeypatch):
    class Secrets(dict):
        pass

    class FakeSt:
        secrets = Secrets({"XERO_CLIENT_ID": "secret-value"})

    monkeypatch.setenv("XERO_CLIENT_ID", "env-value")
    assert guard._server_setting(FakeSt(), "XERO_CLIENT_ID") == "env-value"


def test_xero_config_reads_only_server_side_settings(monkeypatch):
    values = {
        "XERO_CLIENT_ID": "id",
        "XERO_CLIENT_SECRET": "secret",
        "XERO_REDIRECT_URI": "https://example/callback",
        "JOBHUB_INTEGRATION_ENCRYPTION_KEY": "key",
    }
    monkeypatch.setattr(guard, "_server_setting", lambda st, key: values.get(key, ""))
    config = guard._xero_config(object())
    assert config == {
        "client_id": "id",
        "client_secret": "secret",
        "redirect_uri": "https://example/callback",
        "encryption_key": "key",
    }


def test_save_connection_persists_encrypted_payload_not_plain_tokens(monkeypatch):
    calls = []
    settings = []
    monkeypatch.setattr(guard, "_execute", lambda sql, params=(): calls.append((sql, params)))
    monkeypatch.setattr(guard.subscriber_setup_guard, "_set_setting", lambda key, value: settings.append((key, value)))

    guard._save_connection(
        7,
        {"tenantId": "tenant-1", "tenantName": "Example Pty Ltd"},
        "gAAAA-encrypted-payload",
        "openid accounting.contacts",
    )

    assert len(calls) == 1
    sql, params = calls[0]
    assert "organization_integrations" in sql
    assert "encrypted_token_payload" in sql
    assert "tenant-1" in params
    assert "Example Pty Ltd" in params
    assert "gAAAA-encrypted-payload" in params
    assert settings == [("xero_connected", "yes")]


def test_disconnect_removes_token_and_marks_status(monkeypatch):
    calls = []
    settings = []
    monkeypatch.setattr(guard, "_execute", lambda sql, params=(): calls.append((sql, params)))
    monkeypatch.setattr(guard.subscriber_setup_guard, "_set_setting", lambda key, value: settings.append((key, value)))
    guard._disconnect(4)
    assert "encrypted_token_payload=NULL" in calls[0][0]
    assert "status='Disconnected'" in calls[0][0]
    assert settings == [("xero_connected", "no")]


def test_integration_row_reads_selected_tenant(monkeypatch):
    monkeypatch.setattr(
        guard,
        "_df_query",
        lambda sql, params=(): pd.DataFrame([
            {
                "status": "Connected",
                "external_tenant_id": "tenant-1",
                "external_tenant_name": "Example Pty Ltd",
                "scopes": "openid",
                "connected_at": "now",
                "refreshed_at": "now",
                "disconnected_at": None,
            }
        ]),
    )
    row = guard._integration_row(3)
    assert row["status"] == "Connected"
    assert row["external_tenant_name"] == "Example Pty Ltd"


def test_callback_rejects_state_mismatch_without_token_exchange(monkeypatch):
    errors = []

    class Session(dict):
        pass

    class QueryParams(dict):
        pass

    class FakeSt:
        session_state = Session({guard.STATE_KEY: "expected-state"})
        query_params = QueryParams({"code": "code-1", "state": "wrong-state"})

        @staticmethod
        def error(message):
            errors.append(message)

    called = []
    monkeypatch.setattr(guard, "exchange_authorization_code", lambda *args, **kwargs: called.append(True))
    guard._handle_callback(
        FakeSt(),
        {
            "client_id": "id",
            "client_secret": "secret",
            "redirect_uri": "https://callback",
            "encryption_key": "key",
        },
        1,
    )
    assert not called
    assert errors
    assert "code" not in FakeSt.query_params
    assert guard.STATE_KEY not in FakeSt.session_state


def test_guard_wraps_subscriber_setup_once(monkeypatch):
    calls = []

    def original():
        calls.append("subscriber")

    monkeypatch.setattr(guard.subscriber_setup_guard, "render_subscriber_setup", original)
    monkeypatch.setattr(guard, "render_xero_setup_panel", lambda: calls.append("xero"))
    assert guard.install_xero_setup_guard() is True
    guard.subscriber_setup_guard.render_subscriber_setup()
    assert calls == ["subscriber", "xero"]
    assert guard.install_xero_setup_guard() is False
