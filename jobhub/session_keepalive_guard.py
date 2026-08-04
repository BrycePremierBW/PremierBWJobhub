"""Keep Streamlit sessions active while JobHub tabs sit idle.

Streamlit authentication is stored in session_state. Some browsers suspend an
inactive tab long enough for the server-side session to be discarded, which
returns users to the login screen. This guard installs a small fragment heartbeat
after page configuration so the existing session remains attached without full
page refreshes or changing logout behaviour.
"""

from __future__ import annotations

import sys
from typing import Any


PATCH_MARKER = "_pb_session_keepalive_guard"
HEARTBEAT_INTERVAL = "5m"


def _render_heartbeat(st: Any) -> None:
    fragment = getattr(st, "fragment", None)
    if not callable(fragment):
        return

    @fragment(run_every=HEARTBEAT_INTERVAL)
    def _jobhub_session_heartbeat() -> None:
        # A fragment rerun keeps the websocket/session active without rerunning
        # the full application or altering authentication state.
        st.empty()

    _jobhub_session_heartbeat()


def install_session_keepalive_guard() -> bool:
    st = sys.modules.get("streamlit")
    if st is None:
        return False

    original = getattr(st, "set_page_config", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    def set_page_config_with_keepalive(*args: Any, **kwargs: Any):
        result = original(*args, **kwargs)
        try:
            _render_heartbeat(st)
        except Exception:
            # Never prevent JobHub from loading if a Streamlit version does not
            # support timed fragments.
            pass
        return result

    set_page_config_with_keepalive._pb_session_keepalive_guard = True
    set_page_config_with_keepalive._pb_original_set_page_config = original
    st.set_page_config = set_page_config_with_keepalive
    return True
