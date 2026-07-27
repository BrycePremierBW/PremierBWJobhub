"""Administrator-facing Xero connection workflow for JobHub."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import secrets
from typing import Any

import streamlit as st

from .oauth_state import OAuthNonceStore, OAuthStateSigner
from .schema import ensure_xero_schema
from .token_store import FernetTokenCipher, XeroTokenStore
from .xero_client import XeroClient, XeroOAuthConfig, XeroToken


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _enabled() -> bool:
    return _env("XERO_ENABLED").lower() in {"1", "true", "yes", "on"}


def _client() -> XeroClient:
    return XeroClient(
        XeroOAuthConfig(
            client_id=_env("XERO_CLIENT_ID"),
            client_secret=_env("XERO_CLIENT_SECRET"),
            redirect_uri=_env("XERO_REDIRECT_URI"),
        )
    )


def _current_user_id(ctx: dict[str, Any]) -> str:
    user = ctx["get_current_user"]() or {}
    return str(user.get("id") or user.get("username") or "unknown")


def _token_store(ctx: dict[str, Any]) -> XeroTokenStore:
    return XeroTokenStore(
        ctx["connect"],
        FernetTokenCipher(_env("XERO_TOKEN_ENCRYPTION_KEY")),
        use_postgres=bool(ctx.get("USE_POSTGRES")),
    )


def _nonce_store(ctx: dict[str, Any]) -> OAuthNonceStore:
    return OAuthNonceStore(
        ctx["connect"],
        use_postgres=bool(ctx.get("USE_POSTGRES")),
    )


def _state_signer() -> OAuthStateSigner:
    return OAuthStateSigner(_env("XERO_OAUTH_STATE_SECRET"))


def _query_value(name: str) -> str:
    value = st.query_params.get(name, "")
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "")


def _clear_oauth_query() -> None:
    for name in ("code", "state", "scope", "session_state", "error", "error_description"):
        if name in st.query_params:
            del st.query_params[name]


def _store_connection(
    ctx: dict[str, Any],
    token: XeroToken,
    connection: dict[str, Any],
) -> None:
    tenant_id = str(connection.get("tenantId", ""))
    tenant_name = str(connection.get("tenantName", ""))
    _token_store(ctx).save(
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        token=token,
        connected_by=_current_user_id(ctx),
        now=datetime.now(timezone.utc),
    )
    ctx["record_audit_event"](
        "xero_connected",
        "xero_connection",
        tenant_id,
        {"tenant_name": tenant_name},
    )


def _handle_callback(ctx: dict[str, Any]) -> None:
    error = _query_value("error")
    if error:
        description = _query_value("error_description") or error
        st.error(f"Xero connection was not completed: {description}")
        _clear_oauth_query()
        return

    code = _query_value("code")
    state = _query_value("state")
    if not code and not state:
        return
    if not code or not state:
        st.error("The Xero callback was incomplete. Start the connection again.")
        _clear_oauth_query()
        return

    try:
        payload = _state_signer().verify(state)
        if not _nonce_store(ctx).consume(
            payload["user_id"],
            payload["nonce"],
            consumed_at=datetime.now(timezone.utc).isoformat(),
        ):
            raise ValueError("The Xero connection request was already used or expired.")
        if str(payload["user_id"]) != _current_user_id(ctx):
            raise ValueError("The Xero callback belongs to a different JobHub user.")
        client = _client()
        token = client.exchange_code(code)
        connections = client.connections(token.access_token)
        if not connections:
            raise ValueError("Xero did not return an authorised organisation.")
        if len(connections) == 1:
            _store_connection(ctx, token, connections[0])
            st.success(f"Connected to Xero: {connections[0].get('tenantName', 'organisation')}")
        else:
            st.session_state["_xero_pending_token"] = token
            st.session_state["_xero_pending_connections"] = connections
            st.info("Choose the Xero organisation to connect below.")
    except Exception as exc:
        ctx["pb_error"](f"Xero connection failed: {exc}")
    finally:
        _clear_oauth_query()


def _render_pending_organisation(ctx: dict[str, Any]) -> None:
    token = st.session_state.get("_xero_pending_token")
    connections = st.session_state.get("_xero_pending_connections") or []
    if not token or not connections:
        return
    options = {
        f"{item.get('tenantName', 'Xero organisation')} ({item.get('tenantType', '')})": item
        for item in connections
    }
    selected = st.selectbox("Xero organisation", list(options), key="xero_tenant_choice")
    if st.button("Confirm Xero organisation", type="primary"):
        _store_connection(ctx, token, options[selected])
        st.session_state.pop("_xero_pending_token", None)
        st.session_state.pop("_xero_pending_connections", None)
        ctx["pb_success"](f"Connected to Xero: {selected}")
        ctx["pb_rerun"]()


def _render_connect_button(ctx: dict[str, Any]) -> None:
    if not _enabled():
        st.warning("Xero is disabled. Set XERO_ENABLED=true in the staging environment.")
        return
    missing = [
        name
        for name in (
            "XERO_CLIENT_ID",
            "XERO_CLIENT_SECRET",
            "XERO_REDIRECT_URI",
            "XERO_TOKEN_ENCRYPTION_KEY",
            "XERO_OAUTH_STATE_SECRET",
        )
        if not _env(name)
    ]
    if missing:
        st.error("Missing secure environment settings: " + ", ".join(missing))
        return
    now = datetime.now(timezone.utc)
    nonce = secrets.token_urlsafe(32)
    user_id = _current_user_id(ctx)
    _nonce_store(ctx).register(
        user_id,
        nonce,
        created_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=10)).isoformat(),
    )
    state = _state_signer().issue(user_id, nonce)
    st.link_button("Connect Xero organisation", _client().authorisation_url(state))


def render_xero_settings(ctx: dict[str, Any]) -> None:
    st.header("Xero Integration")
    st.caption("Securely connect approved JobHub commercial records to Xero.")
    ensure_xero_schema(ctx["connect"])
    _handle_callback(ctx)
    _render_pending_organisation(ctx)

    connections = ctx["df_query"](
        """
        SELECT tenant_id, tenant_name, connected_by, connected_at, updated_at
        FROM xero_connections
        ORDER BY tenant_name
        """
    )
    if connections.empty:
        st.info("No Xero organisation is connected.")
    else:
        st.success(f"{len(connections)} Xero organisation(s) connected.")
        st.dataframe(
            connections.rename(
                columns={
                    "tenant_name": "Organisation",
                    "connected_by": "Connected by",
                    "connected_at": "Connected",
                    "updated_at": "Token updated",
                }
            ),
            hide_index=True,
            width="stretch",
        )
    _render_connect_button(ctx)
