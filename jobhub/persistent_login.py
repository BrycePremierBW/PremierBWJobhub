"""Browser-persistent login support for Premier Brushworks JobHub.

The core JobHub authentication already issues a random, revocable auth token and
stores it server-side.  This module only remembers that token in this browser;
it never stores a username or password.

It is intentionally installed before ``pb_jobhub_app`` is imported so it can:
- bootstrap a remembered token after Streamlit config is initialised;
- add a "Stay signed in on this device" checkbox to the existing login form;
- save the issued token after a successful login; and
- discard stale/revoked tokens when the login form is shown again.
"""

from __future__ import annotations

import json
from typing import Any

import streamlit as st

_STORAGE_KEY = "pb_jobhub_remember_token_v1"
_REMEMBER_KEY = "_pb_remember_login"
_INSTALLED = False


def _js_string(value: Any) -> str:
    return json.dumps(str(value or ""))


def _emit_script(script: str) -> None:
    """Emit same-origin browser JavaScript without taking visible page space."""
    try:
        st.html(f"<script>{script}</script>", unsafe_allow_javascript=True)
    except Exception:
        # Stay-signed-in is a convenience feature; auth must continue working
        # normally if a Streamlit/browser version blocks the helper script.
        pass


def _bootstrap_remembered_token() -> None:
    """Restore a remembered token by briefly placing it in the auth query param."""
    storage_key = _js_string(_STORAGE_KEY)
    _emit_script(
        f"""
        (() => {{
          try {{
            const w = window.parent;
            const url = new URL(w.location.href);
            if (!url.searchParams.get('auth')) {{
              const token = w.localStorage.getItem({storage_key});
              if (token) {{
                url.searchParams.set('auth', token);
                w.location.replace(url.toString());
              }}
            }}
          }} catch (e) {{}}
        }})();
        """
    )


def _sync_token_to_browser() -> None:
    """Persist/clear the current token according to the login checkbox."""
    token = st.session_state.get("_pb_auth_token")
    user = st.session_state.get("user")
    if not token or not user:
        return

    remember = bool(st.session_state.get(_REMEMBER_KEY, False))
    storage_key = _js_string(_STORAGE_KEY)
    token_js = _js_string(token)
    if remember:
        action = f"w.localStorage.setItem({storage_key}, {token_js});"
    else:
        action = f"w.localStorage.removeItem({storage_key});"

    _emit_script(
        f"""
        (() => {{
          try {{
            const w = window.parent;
            {action}
            // The query-string token is only a bootstrap bridge. Remove it
            // from the visible URL once the server has restored the session.
            const url = new URL(w.location.href);
            if (url.searchParams.has('auth')) {{
              url.searchParams.delete('auth');
              w.history.replaceState({{}}, '', url.toString());
            }}
          }} catch (e) {{}}
        }})();
        """
    )


def _clear_stale_browser_token() -> None:
    """Clear a remembered token when the server has rejected/revoked it."""
    try:
        auth_in_url = bool(st.query_params.get("auth"))
    except Exception:
        auth_in_url = False
    if not auth_in_url or st.session_state.get("user"):
        return

    storage_key = _js_string(_STORAGE_KEY)
    _emit_script(
        f"""
        (() => {{
          try {{
            const w = window.parent;
            w.localStorage.removeItem({storage_key});
            const url = new URL(w.location.href);
            url.searchParams.delete('auth');
            w.history.replaceState({{}}, '', url.toString());
          }} catch (e) {{}}
        }})();
        """
    )


class _RememberForm:
    """Context-manager proxy that inserts the remember checkbox in login form."""

    def __init__(self, original_form: Any, key: str):
        self._original_form = original_form
        self._key = key

    def __enter__(self):
        entered = self._original_form.__enter__()
        _clear_stale_browser_token()
        st.checkbox(
            "Stay signed in on this device",
            key=_REMEMBER_KEY,
            help="Keeps this device signed in until you log out or the saved session is revoked.",
        )
        return entered

    def __exit__(self, exc_type, exc, tb):
        return self._original_form.__exit__(exc_type, exc, tb)


def install() -> None:
    """Install the persistence hooks once for the current Python process."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    # Delay all UI/JS until the core app calls set_page_config so we do not
    # violate Streamlit's requirement that page config be the first UI command.
    original_set_page_config = st.set_page_config

    def set_page_config_with_login_bootstrap(*args, **kwargs):
        result = original_set_page_config(*args, **kwargs)
        _bootstrap_remembered_token()
        return result

    st.set_page_config = set_page_config_with_login_bootstrap

    try:
        from streamlit.delta_generator import DeltaGenerator

        original_form = DeltaGenerator.form
        original_write = DeltaGenerator.write

        def form_with_remember(self, key, *args, **kwargs):
            form = original_form(self, key, *args, **kwargs)
            if str(key) == "login_form":
                return _RememberForm(form, str(key))
            return form

        def write_with_login_sync(self, *args, **kwargs):
            # JobHub renders "Logged in as ..." immediately after auth succeeds.
            # That is the first reliable point at which the core token exists.
            if st.session_state.get("user") and st.session_state.get("_pb_auth_token"):
                _sync_token_to_browser()
            return original_write(self, *args, **kwargs)

        DeltaGenerator.form = form_with_remember
        DeltaGenerator.write = write_with_login_sync
    except Exception:
        # The core login remains fully functional even if Streamlit changes an
        # internal rendering class in a future release.
        pass
