"""Keep automatic overdue reminders from blocking normal JobHub page renders.

JobHub creates overdue staff-request inbox notifications during ordinary Streamlit
reruns.  The legacy path also mirrors each reminder to OneSignal synchronously.
With up to 25 overdue requests and a 12-second HTTP timeout per request, a slow or
unavailable OneSignal endpoint can make the app appear frozen for several minutes.

This guard leaves explicit/manual push notifications unchanged.  It only prevents
the external OneSignal HTTP call while ``notify_overdue_staff_requests`` is on the
call stack.  The in-app notification has already been written before that call, so
staff still see the overdue reminder in JobHub without blocking the page render.
"""

from __future__ import annotations

import functools
import inspect
import sys
from typing import Any


PATCH_MARKER = "_pb_overdue_notification_freeze_guard"
ONESIGNAL_NOTIFICATIONS_URL = "https://api.onesignal.com/notifications"
DEFERRED_MESSAGE = (
    "Automatic overdue push delivery was skipped during page rendering; "
    "the in-app JobHub notification was still created."
)


def _is_automatic_overdue_dispatch() -> bool:
    frame = inspect.currentframe()
    try:
        frame = frame.f_back if frame is not None else None
        checked = 0
        while frame is not None and checked < 40:
            if frame.f_code.co_name == "notify_overdue_staff_requests":
                return True
            frame = frame.f_back
            checked += 1
        return False
    finally:
        del frame


def _deferred_response(requests_module: Any, url: str) -> Any:
    response_type = getattr(requests_module, "Response", None)
    if response_type is None:
        class DeferredResponse:
            status_code = 503
            ok = False
            text = DEFERRED_MESSAGE

            @staticmethod
            def json() -> dict[str, Any]:
                return {}

        return DeferredResponse()

    response = response_type()
    response.status_code = 503
    response.url = url
    response._content = DEFERRED_MESSAGE.encode("utf-8")
    response.encoding = "utf-8"
    return response


def install_notification_freeze_guard() -> bool:
    requests_module = sys.modules.get("requests")
    if requests_module is None:
        return False

    original = getattr(requests_module, "post", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    @functools.wraps(original)
    def guarded_post(url: Any, *args: Any, **kwargs: Any):
        if (
            str(url).rstrip("/") == ONESIGNAL_NOTIFICATIONS_URL
            and _is_automatic_overdue_dispatch()
        ):
            return _deferred_response(requests_module, str(url))
        return original(url, *args, **kwargs)

    setattr(guarded_post, PATCH_MARKER, True)
    guarded_post._pb_original_post = original
    requests_module.post = guarded_post
    return True
