"""Clarify web-push wording in JobHub.

The push feature registers whichever browser/app/device the user is currently
using.  Desktop browsers can receive the same web push notifications as phones,
so labels that say only "phone notifications" confuse managers testing from a
computer.  This guard rewrites the visible text to "device notifications" while
leaving function names and delivery behaviour unchanged.
"""

from __future__ import annotations

import sys
from typing import Any


_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("Phone notifications enabled", "Device notifications enabled"),
    ("Enable phone notifications on this device", "Enable notifications on this device"),
    ("Retry phone notification setup", "Retry notification setup"),
    ("Loading phone notifications…", "Loading device notifications…"),
    ("Phone push provider is configured.", "Device push provider is configured."),
    ("Phone push is installed", "Device push is installed"),
    ("Phone push code is installed", "Device push code is installed"),
    ("Phone notification diagnostics", "Device notification diagnostics"),
    ("phone notifications", "device notifications"),
    ("Phone notifications", "Device notifications"),
    ("phone notification", "device notification"),
    ("Phone notification", "Device notification"),
    ("phone push", "device push"),
    ("Phone push", "Device push"),
)


def _rewrite(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    updated = value
    for before, after in _REPLACEMENTS:
        updated = updated.replace(before, after)
    return updated


def _wrap_text_function(streamlit_module: Any, name: str) -> bool:
    original = getattr(streamlit_module, name, None)
    if original is None or getattr(original, "_pb_notification_wording_guard", False):
        return False

    def pb_notification_wording_wrapper(body: Any, *args: Any, **kwargs: Any):
        return original(_rewrite(body), *args, **kwargs)

    pb_notification_wording_wrapper._pb_notification_wording_guard = True  # type: ignore[attr-defined]
    pb_notification_wording_wrapper._pb_original = original  # type: ignore[attr-defined]
    setattr(streamlit_module, name, pb_notification_wording_wrapper)
    return True


def install_notification_wording_guard() -> bool:
    streamlit_module = sys.modules.get("streamlit")
    if streamlit_module is None:
        return False
    installed = False
    for function_name in ("html", "info", "warning", "success", "caption", "markdown", "toast"):
        installed = _wrap_text_function(streamlit_module, function_name) or installed
    return installed
