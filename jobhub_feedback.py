from __future__ import annotations

import time
from typing import Any

import streamlit as st

_RECENT_KEY = "_pb_recent_action_feedback"
_REPLAY_KEY = "_pb_replay_action_feedback"
_MAX_REPLAY_AGE_SECONDS = 15.0

_LEVEL_DETAILS = {
    "success": {"icon": "✅", "duration": 5},
    "error": {"icon": "❌", "duration": 8},
}


def _message_text(body: Any) -> str:
    text = str(body if body is not None else "").strip()
    return text or "Action completed."


def _remember(level: str, body: Any) -> None:
    st.session_state[_RECENT_KEY] = {
        "level": level,
        "message": _message_text(body),
        "created_at": time.time(),
    }


def _toast(level: str, body: Any) -> None:
    details = _LEVEL_DETAILS[level]
    try:
        st.toast(
            _message_text(body),
            icon=details["icon"],
            duration=details["duration"],
        )
    except Exception:
        # Inline feedback still remains visible if a browser or older Streamlit
        # build cannot display a toast.
        pass


def success(body: Any, *args: Any, **kwargs: Any):
    """Show inline success feedback and a popup notification."""
    result = st.success(body, *args, **kwargs)
    _toast("success", body)
    _remember("success", body)
    return result


def error(body: Any, *args: Any, **kwargs: Any):
    """Show inline error feedback and a popup notification."""
    result = st.error(body, *args, **kwargs)
    _toast("error", body)
    _remember("error", body)
    return result


def prepare_replay() -> None:
    """Carry the most recent action message across an immediate Streamlit rerun."""
    recent = st.session_state.pop(_RECENT_KEY, None)
    if not isinstance(recent, dict):
        return
    try:
        age = time.time() - float(recent.get("created_at", 0.0))
    except Exception:
        return
    if 0 <= age <= _MAX_REPLAY_AGE_SECONDS:
        st.session_state[_REPLAY_KEY] = recent


def rerun() -> None:
    """Rerun without losing the action notification that triggered it."""
    prepare_replay()
    st.rerun()


def replay_pending() -> None:
    """Display a queued action popup once after a rerun, then clear it."""
    note = st.session_state.pop(_REPLAY_KEY, None)
    if not isinstance(note, dict):
        return
    level = str(note.get("level", "success"))
    if level not in _LEVEL_DETAILS:
        level = "success"
    _toast(level, note.get("message", "Action completed."))
