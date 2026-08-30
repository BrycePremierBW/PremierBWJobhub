"""Wire secure Xero OAuth into JobHub's commercial subscriber setup.

Credentials and encryption keys come only from server environment/secrets.
OAuth tokens are encrypted before database persistence and never written to
browser storage. The guard appends a real connection panel to the existing
subscriber onboarding screen without replacing current production workflows.
"""

from __future__ import annotations

import os
import secrets
import sys
from datetime import datetime, timezone
from typing import Any

from . import subscriber_setup_guard
from .organization_schema_guard import (
    DEFAULT_ORGANIZATION_SLUG,
    ensure_organization_schema,
    get_organization_id,
)
from .xero_oauth import (
    build_authorization_url,
    encrypt_token_payload,
    exchange_authorization_code,
    list_connections,
)


PATCH_MARKER = "_pb_xero_setup_guard"
STATE_KEY = "_jobhub_xero_oauth_state"
PENDING_TOKEN_KEY = "_jobhub_xero_pending_encrypted_token"
PENDING_CONNECTIONS_KEY = "_jobhub_xero_pending_connections"


def _st() -> Any:
    return sys.modules.get("streamlit")


def _app_attr(name: str, default: Any = None) -> Any:
    for module_name in ("__main__", "pb_jobhub_app"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, name):
            return getattr(module, name)
    return default


def _execute(sql: str, params: tuple[Any, ...] = ()) -> Any:
    fn = _app_attr("execute")
    if callable(fn):
        return fn(sql, params)
    raise RuntimeError("JobHub database execute function is not available yet.")


def _df_query(sql: str, params: tuple[Any, ...] = ()) -> Any:
    fn = _app_attr("df_query") or _app_attr("safe_df_query")
    if callable(fn):
        return fn(sql, params)
    raise RuntimeError("JobHub database query function is not available yet.")


def _server_setting(st: Any, key: str) -> str:
    value = str(os.environ.get(key, "") or "").strip()
    if value:
        return value
    try:
        return str(st.secrets.get(key, "") or "").strip()
    except Exception:
        return ""


def _xero_config(st: Any) -> dict[str, str]:
    return {
        "client_id": _server_setting(st, "XERO_CLIENT_ID"),
        "client_secret": _server_setting(st, "XERO_CLIENT_SECRET"),
        "redirect_uri": _server_setting(st, "XERO_REDIRECT_URI"),
        "encryption_key": _server_setting(st, "JOBHUB_INTEGRATION_ENCRYPTION_KEY"),
    }


def _query_param(st: Any, key: str) -> str:
    try:
        value = st.query_params.get(key, "")
    except Exception:
        return ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "").strip()


def _clear_oauth_query_params(st: Any) -> None:
    try:
        for key in ("code", "state", "scope", "session_state", "error", "error_description"):
            try:
                del st.query_params[key]
            except Exception:
                pass
    except Exception:
        pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ensure_org() -> int:
    ensure_organization_schema()
    organization_id = get_organization_id(DEFAULT_ORGANIZATION_SLUG)
    if organization_id is None:
        raise RuntimeError("Default JobHub organization could not be initialised.")
    return int(organization_id)


def _integration_row(organization_id: int) -> dict[str, Any] | None:
    try:
        df = _df_query(
            """
            SELECT status, external_tenant_id, external_tenant_name, scopes,
                   connected_at, refreshed_at, disconnected_at
            FROM organization_integrations
            WHERE organization_id=? AND provider='xero'
            LIMIT 1
            """,
            (int(organization_id),),
        )
        if df is None or getattr(df, "empty", True):
            return None
        return dict(df.iloc[0].to_dict())
    except Exception:
        return None


def _save_connection(
    organization_id: int,
    connection: dict[str, Any],
    encrypted_token_payload: str,
    scopes: str = "",
) -> None:
    tenant_id = str(connection.get("tenantId") or connection.get("tenant_id") or "").strip()
    tenant_name = str(connection.get("tenantName") or connection.get("tenant_name") or "").strip()
    if not tenant_id:
        raise ValueError("The selected Xero organization did not return a tenant ID.")
    now = _now_iso()
    _execute(
        """
        INSERT INTO organization_integrations
        (organization_id, provider, status, external_tenant_id, external_tenant_name,
         encrypted_token_payload, scopes, connected_at, refreshed_at, disconnected_at, notes)
        VALUES (?, 'xero', 'Connected', ?, ?, ?, ?, ?, ?, NULL, '')
        ON CONFLICT(organization_id, provider) DO UPDATE SET
            status='Connected',
            external_tenant_id=excluded.external_tenant_id,
            external_tenant_name=excluded.external_tenant_name,
            encrypted_token_payload=excluded.encrypted_token_payload,
            scopes=excluded.scopes,
            connected_at=COALESCE(organization_integrations.connected_at, excluded.connected_at),
            refreshed_at=excluded.refreshed_at,
            disconnected_at=NULL
        """,
        (int(organization_id), tenant_id, tenant_name, encrypted_token_payload, str(scopes or ""), now, now),
    )
    subscriber_setup_guard._set_setting("xero_connected", "yes")


def _disconnect(organization_id: int) -> None:
    _execute(
        """
        UPDATE organization_integrations
        SET status='Disconnected', encrypted_token_payload=NULL,
            disconnected_at=?, refreshed_at=?
        WHERE organization_id=? AND provider='xero'
        """,
        (_now_iso(), _now_iso(), int(organization_id)),
    )
    subscriber_setup_guard._set_setting("xero_connected", "no")


def _render_sync_preferences(st: Any, organization_id: int) -> None:
    try:
        current_contacts = subscriber_setup_guard._get_setting("xero_contacts_source", "JobHub and Xero")
        current_financials = subscriber_setup_guard._get_setting("xero_financial_source", "Xero")
    except Exception:
        current_contacts = "JobHub and Xero"
        current_financials = "Xero"

    contacts_options = ["JobHub and Xero", "Xero", "JobHub"]
    financial_options = ["Xero", "JobHub"]
    c1, c2 = st.columns(2)
    contacts = c1.selectbox(
        "Contacts source of truth",
        contacts_options,
        index=contacts_options.index(current_contacts) if current_contacts in contacts_options else 0,
        key="xero_contacts_source_pref",
        help="Controls how future contact synchronization should resolve changes and duplicates.",
    )
    financials = c2.selectbox(
        "Financial data source of truth",
        financial_options,
        index=financial_options.index(current_financials) if current_financials in financial_options else 0,
        key="xero_financial_source_pref",
        help="Xero is recommended as the accounting source of truth.",
    )
    if st.button("Save Xero sync preferences", key="save_xero_sync_preferences"):
        subscriber_setup_guard._set_setting("xero_contacts_source", contacts)
        subscriber_setup_guard._set_setting("xero_financial_source", financials)
        st.success("Xero sync preferences saved.")


def _handle_callback(st: Any, config: dict[str, str], organization_id: int) -> None:
    oauth_error = _query_param(st, "error")
    if oauth_error:
        description = _query_param(st, "error_description")
        st.error(f"Xero connection was not completed: {description or oauth_error}")
        _clear_oauth_query_params(st)
        return

    code = _query_param(st, "code")
    returned_state = _query_param(st, "state")
    if not code:
        return

    expected_state = str(st.session_state.get(STATE_KEY, "") or "")
    if not expected_state or not returned_state or not secrets.compare_digest(returned_state, expected_state):
        st.error("Xero connection could not be verified. Start the connection again from JobHub.")
        _clear_oauth_query_params(st)
        st.session_state.pop(STATE_KEY, None)
        return

    try:
        token_payload = exchange_authorization_code(
            code,
            config["client_id"],
            config["client_secret"],
            config["redirect_uri"],
        )
        connections = list_connections(str(token_payload.get("access_token") or ""))
        if not connections:
            raise RuntimeError("No Xero organizations were available for this login.")
        encrypted = encrypt_token_payload(token_payload, config["encryption_key"])
        st.session_state[PENDING_TOKEN_KEY] = encrypted
        st.session_state[PENDING_CONNECTIONS_KEY] = connections
        st.session_state.pop(STATE_KEY, None)
        _clear_oauth_query_params(st)
        if len(connections) == 1:
            _save_connection(
                organization_id,
                connections[0],
                encrypted,
                str(token_payload.get("scope") or ""),
            )
            st.session_state.pop(PENDING_TOKEN_KEY, None)
            st.session_state.pop(PENDING_CONNECTIONS_KEY, None)
            st.success(f"Xero connected to {connections[0].get('tenantName') or 'your organization'}.")
    except Exception as exc:
        st.error(f"Could not complete the Xero connection: {exc}")


def _render_pending_connection_picker(st: Any, organization_id: int) -> None:
    connections = st.session_state.get(PENDING_CONNECTIONS_KEY) or []
    encrypted = str(st.session_state.get(PENDING_TOKEN_KEY, "") or "")
    if not connections or not encrypted:
        return

    options = {
        str(item.get("tenantName") or item.get("tenantId") or f"Xero organization {index + 1}"): item
        for index, item in enumerate(connections)
        if isinstance(item, dict)
    }
    if not options:
        return
    selected_name = st.selectbox("Choose the Xero organization to connect", list(options), key="jobhub_xero_org_picker")
    c1, c2 = st.columns(2)
    if c1.button("Connect selected Xero organization", type="primary", key="jobhub_xero_confirm_org"):
        try:
            _save_connection(organization_id, options[selected_name], encrypted)
            st.session_state.pop(PENDING_TOKEN_KEY, None)
            st.session_state.pop(PENDING_CONNECTIONS_KEY, None)
            st.success(f"Xero connected to {selected_name}.")
        except Exception as exc:
            st.error(f"Could not save the Xero connection: {exc}")
    if c2.button("Cancel Xero connection", key="jobhub_xero_cancel_org"):
        st.session_state.pop(PENDING_TOKEN_KEY, None)
        st.session_state.pop(PENDING_CONNECTIONS_KEY, None)
        st.info("Xero connection cancelled.")


def render_xero_setup_panel() -> None:
    st = _st()
    if st is None:
        return

    st.divider()
    st.subheader("Xero connection")
    st.caption("Connect the subscriber's accounting organisation securely. JobHub never stores the Xero password and OAuth tokens are encrypted server-side.")

    try:
        organization_id = _ensure_org()
    except Exception as exc:
        st.error(f"Xero setup is unavailable until the organization schema is ready: {exc}")
        return

    config = _xero_config(st)
    missing = [name for name, value in config.items() if not value]
    row = _integration_row(organization_id)
    connected = bool(row and str(row.get("status") or "").casefold() == "connected")

    if connected:
        tenant_name = str(row.get("external_tenant_name") or "Xero organization")
        st.success(f"Connected to Xero: {tenant_name}")
        _render_sync_preferences(st, organization_id)
        if st.button("Disconnect Xero from JobHub", key="jobhub_disconnect_xero"):
            try:
                _disconnect(organization_id)
                st.success("Xero disconnected from JobHub and the stored token was removed.")
            except Exception as exc:
                st.error(f"Could not disconnect Xero: {exc}")
        return

    if missing:
        labels = {
            "client_id": "XERO_CLIENT_ID",
            "client_secret": "XERO_CLIENT_SECRET",
            "redirect_uri": "XERO_REDIRECT_URI",
            "encryption_key": "JOBHUB_INTEGRATION_ENCRYPTION_KEY",
        }
        required = ", ".join(labels[item] for item in missing)
        st.warning(f"Xero is ready in JobHub but the server still needs: {required}.")
        return

    _handle_callback(st, config, organization_id)
    if _integration_row(organization_id):
        row = _integration_row(organization_id)
        if row and str(row.get("status") or "").casefold() == "connected":
            return

    _render_pending_connection_picker(st, organization_id)
    if st.session_state.get(PENDING_CONNECTIONS_KEY):
        return

    state = str(st.session_state.get(STATE_KEY, "") or "")
    if not state:
        state = secrets.token_urlsafe(32)
        st.session_state[STATE_KEY] = state
    try:
        authorize_url = build_authorization_url(
            config["client_id"],
            config["redirect_uri"],
            state,
        )
        st.link_button("Connect Xero", authorize_url, type="primary", use_container_width=True)
        st.caption("You will sign in at Xero, approve access, then return to JobHub to choose the organisation.")
    except Exception as exc:
        st.error(f"Could not prepare the Xero connection: {exc}")


def install_xero_setup_guard() -> bool:
    original = getattr(subscriber_setup_guard, "render_subscriber_setup", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    def wrapped_render_subscriber_setup() -> None:
        original()
        render_xero_setup_panel()

    wrapped_render_subscriber_setup._pb_xero_setup_guard = True
    wrapped_render_subscriber_setup._pb_original = original
    subscriber_setup_guard.render_subscriber_setup = wrapped_render_subscriber_setup
    return True
