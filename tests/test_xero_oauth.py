from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet

from jobhub.xero_oauth import (
    AUTHORIZE_URL,
    CONNECTIONS_URL,
    TOKEN_URL,
    XeroOAuthError,
    build_authorization_url,
    decrypt_token_payload,
    encrypt_token_payload,
    exchange_authorization_code,
    list_connections,
    refresh_access_token,
    validate_encryption_key,
)


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_authorization_url_contains_required_oauth_parameters():
    url = build_authorization_url(
        "client-123",
        "https://jobhub.example/xero/callback",
        "state-abc",
    )
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == AUTHORIZE_URL
    assert params["response_type"] == ["code"]
    assert params["client_id"] == ["client-123"]
    assert params["redirect_uri"] == ["https://jobhub.example/xero/callback"]
    assert params["state"] == ["state-abc"]
    scopes = set(params["scope"][0].split())
    assert "accounting.contacts" in scopes
    assert "accounting.transactions" in scopes
    assert "offline_access" in scopes


def test_authorization_url_rejects_missing_required_fields():
    with pytest.raises(ValueError):
        build_authorization_url("", "https://callback", "state")
    with pytest.raises(ValueError):
        build_authorization_url("client", "", "state")
    with pytest.raises(ValueError):
        build_authorization_url("client", "https://callback", "")


def test_exchange_authorization_code_posts_expected_request():
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse({"access_token": "access", "refresh_token": "refresh", "expires_in": 1800})

    payload = exchange_authorization_code(
        "code-1",
        "client-id",
        "client-secret",
        "https://jobhub.example/callback",
        post=fake_post,
    )
    assert payload["access_token"] == "access"
    assert calls[0][0] == TOKEN_URL
    assert calls[0][1]["auth"] == ("client-id", "client-secret")
    assert calls[0][1]["data"] == {
        "grant_type": "authorization_code",
        "code": "code-1",
        "redirect_uri": "https://jobhub.example/callback",
    }


def test_refresh_access_token_uses_latest_refresh_token():
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse({"access_token": "new-access", "refresh_token": "new-refresh"})

    payload = refresh_access_token("old-refresh", "client", "secret", post=fake_post)
    assert payload["refresh_token"] == "new-refresh"
    assert calls[0][0] == TOKEN_URL
    assert calls[0][1]["data"] == {
        "grant_type": "refresh_token",
        "refresh_token": "old-refresh",
    }


def test_connections_request_uses_bearer_token():
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse([
            {"id": "connection-1", "tenantId": "tenant-1", "tenantName": "Example Pty Ltd"}
        ])

    connections = list_connections("access-token", get=fake_get)
    assert connections[0]["tenantName"] == "Example Pty Ltd"
    assert calls[0][0] == CONNECTIONS_URL
    assert calls[0][1]["headers"]["Authorization"] == "Bearer access-token"


def test_http_and_payload_errors_are_wrapped():
    def bad_post(url, **kwargs):
        return FakeResponse({"error": "invalid_grant"}, status_code=400, text="invalid_grant")

    with pytest.raises(XeroOAuthError, match="token exchange failed"):
        exchange_authorization_code("code", "client", "secret", "https://callback", post=bad_post)

    def missing_access(url, **kwargs):
        return FakeResponse({"refresh_token": "refresh"})

    with pytest.raises(XeroOAuthError, match="did not return an access token"):
        refresh_access_token("refresh", "client", "secret", post=missing_access)


def test_encrypted_token_payload_round_trip_and_wrong_key_failure():
    key = Fernet.generate_key()
    payload = {
        "access_token": "access-secret",
        "refresh_token": "refresh-secret",
        "tenant_id": "tenant-1",
    }
    encrypted = encrypt_token_payload(payload, key)
    assert "access-secret" not in encrypted
    assert decrypt_token_payload(encrypted, key) == payload

    wrong_key = Fernet.generate_key()
    with pytest.raises(XeroOAuthError, match="could not be decrypted"):
        decrypt_token_payload(encrypted, wrong_key)


def test_invalid_encryption_key_is_rejected():
    with pytest.raises(ValueError, match="valid Fernet key"):
        validate_encryption_key("not-a-fernet-key")
