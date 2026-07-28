"""Administrator-facing Xero connection workflow for JobHub."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import os
import secrets
from typing import Any

import streamlit as st

from .mappings import build_sales_invoice_payload
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


def _active_token(
    ctx: dict[str, Any],
    *,
    tenant_id: str,
    tenant_name: str,
) -> XeroToken:
    store = _token_store(ctx)
    token = store.load(tenant_id)
    if token is None:
        raise ValueError("The saved Xero token could not be loaded. Reconnect Xero.")
    if token.needs_refresh():
        token = _client().refresh(token.refresh_token)
        store.save(
            tenant_id=tenant_id,
            tenant_name=tenant_name,
            token=token,
            connected_by=_current_user_id(ctx),
            now=datetime.now(timezone.utc),
        )
    return token


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


def _render_connection_tools(ctx: dict[str, Any], connections) -> None:
    if connections.empty:
        return

    connection_rows = {
        f"{row['tenant_name']} ({row['tenant_id']})": {
            "tenant_id": str(row["tenant_id"]),
            "tenant_name": str(row["tenant_name"]),
        }
        for _, row in connections.iterrows()
    }
    selected_label = st.selectbox(
        "Connected Xero organisation",
        list(connection_rows),
        key="xero_connected_organisation",
    )
    selected = connection_rows[selected_label]
    tenant_id = selected["tenant_id"]
    tenant_name = selected["tenant_name"]

    verify_col, contacts_col = st.columns(2)
    if verify_col.button(
        "Verify Xero connection",
        key="xero_verify_connection",
        use_container_width=True,
    ):
        try:
            token = _active_token(
                ctx,
                tenant_id=tenant_id,
                tenant_name=tenant_name,
            )
            authorised = _client().connections(token.access_token)
            if any(str(item.get("tenantId")) == tenant_id for item in authorised):
                ctx["pb_success"](f"Xero connection verified: {tenant_name}")
                ctx["record_audit_event"](
                    "xero_connection_verified",
                    "xero_connection",
                    tenant_id,
                    {"tenant_name": tenant_name},
                )
            else:
                raise ValueError("The organisation was not returned by Xero.")
        except Exception as exc:
            ctx["pb_error"](f"Xero connection verification failed: {exc}")

    if contacts_col.button(
        "Load Xero contacts",
        key="xero_load_contacts",
        use_container_width=True,
    ):
        try:
            token = _active_token(
                ctx,
                tenant_id=tenant_id,
                tenant_name=tenant_name,
            )
            contacts = _client().contacts(token, tenant_id)
            st.session_state[f"_xero_contacts_{tenant_id}"] = [
                {
                    "id": str(item.get("ContactID", "")),
                    "name": str(item.get("Name", "")).strip(),
                    "email": str(item.get("EmailAddress", "")).strip(),
                }
                for item in contacts
                if item.get("ContactID") and item.get("Name")
            ]
            ctx["pb_success"](
                f"Loaded {len(st.session_state[f'_xero_contacts_{tenant_id}'])} "
                "Xero contact(s)."
            )
        except Exception as exc:
            ctx["pb_error"](f"Xero contacts could not be loaded: {exc}")

    st.divider()
    st.subheader("Controlled draft invoice test")
    st.caption(
        "Staging only. This creates a DRAFT invoice in Xero. "
        "It cannot approve, send, email or record a payment."
    )
    if _env("JOBHUB_ENV").casefold() != "staging":
        st.warning("Draft invoice test controls are available only in staging.")
        return

    contacts = st.session_state.get(f"_xero_contacts_{tenant_id}") or []
    if not contacts:
        st.info("Load Xero contacts before preparing a draft invoice.")
        return

    contact_options = {
        (
            f"{item['name']} — {item['email']}"
            if item["email"]
            else item["name"]
        ): item["id"]
        for item in contacts
    }
    with st.form("xero_controlled_draft_invoice_form"):
        contact_label = st.selectbox(
            "Xero contact",
            list(contact_options),
            key="xero_draft_contact",
        )
        invoice_date = st.date_input(
            "Invoice date",
            value=date.today(),
            key="xero_draft_invoice_date",
        )
        due_date = st.date_input(
            "Due date",
            value=date.today() + timedelta(days=7),
            key="xero_draft_due_date",
        )
        reference = st.text_input(
            "Reference",
            value=f"JOBHUB-STAGING-{date.today().isoformat()}",
            key="xero_draft_reference",
        )
        description = st.text_input(
            "Line description",
            value="JobHub staging integration test — DRAFT ONLY",
            key="xero_draft_description",
        )
        amount = st.number_input(
            "Amount excluding GST",
            min_value=0.01,
            value=1.00,
            step=1.00,
            key="xero_draft_amount",
        )
        account_code = st.text_input(
            "Xero sales account code",
            help="Use a valid revenue account code from Xero.",
            key="xero_draft_account_code",
        )
        tax_type = st.selectbox(
            "Tax type",
            ["OUTPUT", "EXEMPTOUTPUT", "BASEXCLUDED"],
            key="xero_draft_tax_type",
        )
        confirmation = st.text_input(
            "Type CREATE XERO DRAFT to confirm",
            key="xero_draft_confirmation",
        )
        submitted = st.form_submit_button(
            "Create draft invoice in Xero",
            type="primary",
        )

    if not submitted:
        return
    if confirmation.strip() != "CREATE XERO DRAFT":
        ctx["pb_error"]("Type CREATE XERO DRAFT exactly before creating the draft.")
        return
    if due_date < invoice_date:
        ctx["pb_error"]("The due date cannot be before the invoice date.")
        return
    if not account_code.strip():
        ctx["pb_error"]("Enter a valid Xero sales account code.")
        return

    try:
        invoice = build_sales_invoice_payload(
            contact_id=contact_options[contact_label],
            reference=reference,
            date=invoice_date.isoformat(),
            due_date=due_date.isoformat(),
            lines=[
                {
                    "description": description,
                    "quantity": 1,
                    "unit_amount": amount,
                }
            ],
            account_code=account_code,
            tax_type=tax_type,
        )
        token = _active_token(
            ctx,
            tenant_id=tenant_id,
            tenant_name=tenant_name,
        )
        created = _client().create_draft_invoice(token, tenant_id, invoice)
        invoice_id = str(created.get("InvoiceID", ""))
        invoice_number = str(created.get("InvoiceNumber", "")).strip()
        ctx["record_audit_event"](
            "xero_draft_invoice_created",
            "xero_invoice",
            invoice_id or invoice_number,
            {
                "tenant_name": tenant_name,
                "invoice_number": invoice_number,
                "status": "DRAFT",
                "reference": reference,
            },
        )
        ctx["pb_success"](
            "Created Xero draft invoice"
            + (f" {invoice_number}" if invoice_number else "")
            + ". It has not been approved, sent or paid."
        )
    except Exception as exc:
        ctx["pb_error"](f"Xero draft invoice was not created: {exc}")


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
        _render_connection_tools(ctx, connections)
    _render_connect_button(ctx)
