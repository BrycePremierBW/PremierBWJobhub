"""Runtime configuration helpers for JobHub web push.

JobHub's phone notification flow uses OneSignal from the main Streamlit app.
Render/environment variable naming has changed a few times while the app has
been developed, so this guard makes the installed code tolerant of the common
secure variable names without rendering anything before ``st.set_page_config``.
"""

from __future__ import annotations

import os
from typing import Any


_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    # The main app reads ONESIGNAL_APP_ID and ONESIGNAL_REST_API_KEY.  Accept a
    # few sensible aliases so Render can be configured once without another code
    # change if the variable was named slightly differently.
    "ONESIGNAL_APP_ID": (
        "ONESIGNAL_APP_ID",
        "ONE_SIGNAL_APP_ID",
        "ONESIGNAL_WEB_APP_ID",
    ),
    "ONESIGNAL_REST_API_KEY": (
        "ONESIGNAL_REST_API_KEY",
        "ONESIGNAL_API_KEY",
        "ONESIGNAL_REST_KEY",
        "ONESIGNAL_AUTH_KEY",
    ),
    # Push opens JobHub from the notification.  Render exposes the public host in
    # different ways depending on service type, so fall back to those values.
    "JOBHUB_PUBLIC_URL": (
        "JOBHUB_PUBLIC_URL",
        "RENDER_EXTERNAL_URL",
        "RENDER_SERVICE_URL",
    ),
}

_ORIGINAL_GETENV = os.getenv


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _render_hostname_url() -> str:
    hostname = _clean(os.environ.get("RENDER_EXTERNAL_HOSTNAME"))
    if not hostname:
        return ""
    if hostname.startswith("http://") or hostname.startswith("https://"):
        return hostname.rstrip("/")
    return f"https://{hostname}".rstrip("/")


def _first_non_empty(names: tuple[str, ...]) -> str:
    for name in names:
        value = _clean(os.environ.get(name))
        if value:
            return value.rstrip("/") if name.endswith("URL") else value
    return ""


def pb_jobhub_getenv(key: str, default: Any = None) -> Any:
    if key in _ENV_ALIASES:
        value = _first_non_empty(_ENV_ALIASES[key])
        if value:
            return value
        if key == "JOBHUB_PUBLIC_URL":
            render_url = _render_hostname_url()
            if render_url:
                return render_url
    return _ORIGINAL_GETENV(key, default)


def install_push_configuration_guard() -> bool:
    """Install idempotently and return whether this call changed os.getenv."""
    current = os.getenv
    if getattr(current, "_pb_jobhub_push_config_guard", False):
        return False
    pb_jobhub_getenv._pb_jobhub_push_config_guard = True  # type: ignore[attr-defined]
    pb_jobhub_getenv._pb_original_getenv = _ORIGINAL_GETENV  # type: ignore[attr-defined]
    os.getenv = pb_jobhub_getenv  # type: ignore[assignment]
    return True
