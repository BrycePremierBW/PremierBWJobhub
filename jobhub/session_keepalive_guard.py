"""Keep Streamlit sessions active while JobHub tabs sit idle.

Streamlit authentication is stored in session_state. Some browsers suspend an
inactive tab long enough for the server-side session to be discarded, which
returns users to the login screen. The heartbeat is intentionally infrequent so
it protects long-idle sessions without repeatedly waking an active JobHub tab.
"""

from __future__ import annotations

import sys
from typing import Any


PATCH_MARKER = "_pb_session_keepalive_guard"
HEARTBEAT_INTERVAL = "30m"


def _render_heartbeat(st: Any) -> None:
    fragment = getattr(st, "fragment", None)
    if not callable(fragment):
        return

    @fragment(run_every=HEARTBEAT_INTERVAL)
    def _jobhub_session_heartbeat() -> None:
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
            pass
        return result

    set_page_config_with_keepalive._pb_session_keepalive_guard = True
    set_page_config_with_keepalive._pb_original_set_page_config = original
    st.set_page_config = set_page_config_with_keepalive
    return True
