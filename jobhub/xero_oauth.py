"""Secure Xero OAuth 2.0 helpers for JobHub.

This module contains no Streamlit UI and never persists tokens by itself. Callers
must supply credentials from server-side environment/secrets and store returned
tokens only after encrypting them with a server-managed Fernet key.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable
from urllib.parse import urlencode

import requests
from cryptography.fernet import Fernet, InvalidToken


AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
CONNECTIONS_URL = "https://api.xero.com/connections"
DEFAULT_SCOPES = (
    "openid",
    "profile",
    "email",
    "accounting.transactions",
    "accounting.contacts",
    "accounting.settings",
    "offline_access",
)


class XeroOAuthError(RuntimeError):
    pass


def build_authorization_url(
    client_id: str,
    redirect_uri: str,
    state: str,
    scopes: Iterable[str] = DEFAULT_SCOPES,
) -> str:
    client_id = str(client_id or "").strip()
    redirect_uri = str(redirect_uri or "").strip()
    state = str(state or "").strip()
    if not client_id:
        raise ValueError("Xero client_id is required.")
    if not redirect_uri:
        raise ValueError("Xero redirect_uri is required.")
    if not state:
        raise ValueError("OAuth state is required.")
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(str(scope).strip() for scope in scopes if str(scope).strip()),
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _json_or_error(response: Any, action: str) -> dict[str, Any] | list[Any]:
    try:
        response.raise_for_status()
    except Exception as exc:
        body = ""
        try:
            body = str(response.text or "")[:500]
        except Exception:
            pass
        raise XeroOAuthError(f"Xero {action} failed: {body or exc}") from exc
    try:
        return response.json()
    except Exception as exc:
        raise XeroOAuthError(f"Xero {action} returned invalid JSON.") from exc


def exchange_authorization_code(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    post: Callable[..., Any] = requests.post,
    timeout: int = 20,
) -> dict[str, Any]:
    code = str(code or "").strip()
    if not code:
        raise ValueError("Authorization code is required.")
    response = post(
        TOKEN_URL,
        auth=(str(client_id), str(client_secret)),
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": str(redirect_uri),
        },
        timeout=timeout,
    )
    payload = _json_or_error(response, "token exchange")
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise XeroOAuthError("Xero token exchange did not return an access token.")
    return payload


def refresh_access_token(
    refresh_token: str,
    client_id: str,
    client_secret: str,
    post: Callable[..., Any] = requests.post,
    timeout: int = 20,
) -> dict[str, Any]:
    refresh_token = str(refresh_token or "").strip()
    if not refresh_token:
        raise ValueError("Refresh token is required.")
    response = post(
        TOKEN_URL,
        auth=(str(client_id), str(client_secret)),
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=timeout,
    )
    payload = _json_or_error(response, "token refresh")
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise XeroOAuthError("Xero token refresh did not return an access token.")
    return payload


def list_connections(
    access_token: str,
    get: Callable[..., Any] = requests.get,
    timeout: int = 20,
) -> list[dict[str, Any]]:
    access_token = str(access_token or "").strip()
    if not access_token:
        raise ValueError("Access token is required.")
    response = get(
        CONNECTIONS_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        timeout=timeout,
    )
    payload = _json_or_error(response, "connections request")
    if not isinstance(payload, list):
        raise XeroOAuthError("Xero connections response was not a list.")
    return [dict(item) for item in payload if isinstance(item, dict)]


def validate_encryption_key(key: str | bytes) -> bytes:
    raw = key.encode("utf-8") if isinstance(key, str) else bytes(key or b"")
    if not raw:
        raise ValueError("JOBHUB_INTEGRATION_ENCRYPTION_KEY is required.")
    try:
        Fernet(raw)
    except Exception as exc:
        raise ValueError("JOBHUB_INTEGRATION_ENCRYPTION_KEY must be a valid Fernet key.") from exc
    return raw


def encrypt_token_payload(payload: dict[str, Any], key: str | bytes) -> str:
    raw_key = validate_encryption_key(key)
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return Fernet(raw_key).encrypt(encoded).decode("utf-8")


def decrypt_token_payload(ciphertext: str, key: str | bytes) -> dict[str, Any]:
    raw_key = validate_encryption_key(key)
    try:
        decoded = Fernet(raw_key).decrypt(str(ciphertext or "").encode("utf-8"))
    except InvalidToken as exc:
        raise XeroOAuthError("Stored Xero token could not be decrypted with the configured key.") from exc
    try:
        payload = json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        raise XeroOAuthError("Stored Xero token payload is invalid.") from exc
    if not isinstance(payload, dict):
        raise XeroOAuthError("Stored Xero token payload is not an object.")
    return payload
