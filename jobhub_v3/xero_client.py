"""Small Xero OAuth 2.0 and Accounting API client.

Token persistence and encryption are intentionally handled by the calling
application. This module never writes credentials to disk or logs tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

try:
    import requests
except ModuleNotFoundError:  # Allows pure unit tests before dependencies install.
    requests = None


AUTHORISE_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
CONNECTIONS_URL = "https://api.xero.com/connections"
ACCOUNTING_API_URL = "https://api.xero.com/api.xro/2.0"
DEFAULT_SCOPES = (
    "openid",
    "profile",
    "email",
    "offline_access",
    "accounting.contacts",
    "accounting.invoices",
    "accounting.payments",
)


@dataclass(frozen=True)
class XeroOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: tuple[str, ...] = DEFAULT_SCOPES

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.client_id:
            errors.append("XERO_CLIENT_ID is required.")
        if not self.client_secret:
            errors.append("XERO_CLIENT_SECRET is required.")
        if not self.redirect_uri.startswith(("https://", "http://localhost")):
            errors.append("XERO_REDIRECT_URI must be HTTPS or localhost.")
        return errors


@dataclass(frozen=True)
class XeroToken:
    access_token: str
    refresh_token: str
    expires_at: datetime
    token_type: str = "Bearer"
    scope: str = ""

    @classmethod
    def from_response(
        cls,
        payload: dict[str, Any],
        now: datetime | None = None,
    ) -> "XeroToken":
        issued_at = now or datetime.now(timezone.utc)
        return cls(
            access_token=str(payload["access_token"]),
            refresh_token=str(payload.get("refresh_token", "")),
            expires_at=issued_at + timedelta(seconds=int(payload.get("expires_in", 1800))),
            token_type=str(payload.get("token_type", "Bearer")),
            scope=str(payload.get("scope", "")),
        )

    def needs_refresh(
        self,
        now: datetime | None = None,
        leeway_seconds: int = 120,
    ) -> bool:
        check_time = now or datetime.now(timezone.utc)
        return check_time + timedelta(seconds=leeway_seconds) >= self.expires_at


class XeroClient:
    def __init__(
        self,
        config: XeroOAuthConfig,
        session: Any | None = None,
        timeout: int = 30,
    ) -> None:
        errors = config.validate()
        if errors:
            raise ValueError(" ".join(errors))
        self.config = config
        if session is None:
            if requests is None:
                raise RuntimeError(
                    "The requests package is required for live Xero API calls."
                )
            session = requests.Session()
        self.session = session
        self.timeout = timeout

    def authorisation_url(self, state: str) -> str:
        if not state:
            raise ValueError("OAuth state is required.")
        return f"{AUTHORISE_URL}?{urlencode({
            'response_type': 'code',
            'client_id': self.config.client_id,
            'redirect_uri': self.config.redirect_uri,
            'scope': ' '.join(self.config.scopes),
            'state': state,
        })}"

    def _basic_authorisation(self) -> str:
        raw = f"{self.config.client_id}:{self.config.client_secret}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def _token_request(self, data: dict[str, str]) -> XeroToken:
        response = self.session.post(
            TOKEN_URL,
            data=data,
            headers={
                "Authorization": self._basic_authorisation(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return XeroToken.from_response(response.json())

    def exchange_code(self, code: str) -> XeroToken:
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.config.redirect_uri,
            }
        )

    def refresh(self, refresh_token: str) -> XeroToken:
        if not refresh_token:
            raise ValueError("A refresh token is required.")
        return self._token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        )

    def connections(self, access_token: str) -> list[dict[str, Any]]:
        response = self.session.get(
            CONNECTIONS_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return list(response.json())

    def accounting_request(
        self,
        method: str,
        endpoint: str,
        token: XeroToken,
        tenant_id: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if token.needs_refresh():
            raise RuntimeError("Xero access token must be refreshed before this request.")
        if not tenant_id:
            raise ValueError("A Xero tenant ID is required.")
        response = self.session.request(
            method.upper(),
            f"{ACCOUNTING_API_URL}/{endpoint.lstrip('/')}",
            json=payload,
            params=params,
            headers={
                "Authorization": f"Bearer {token.access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "xero-tenant-id": tenant_id,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return dict(response.json())
