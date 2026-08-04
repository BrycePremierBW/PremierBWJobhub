"""Throttle expensive whole-database synchronisation during normal app use.

The main JobHub script invokes scheduler-date and linked-progress synchronisation
after every Streamlit interaction. This guard keeps the first synchronisation,
then suppresses duplicate work for a short per-session interval. It deliberately
does not alter Streamlit widget behaviour or database schemas.
"""

from __future__ import annotations

import functools
import sys
import time
from typing import Any, Callable


PATCH_MARKER = "_pb_performance_guard"
SYNC_INTERVAL_SECONDS = 120.0


def _streamlit() -> Any:
    return sys.modules.get("streamlit")


def _throttled_sync(st: Any, name: str, function: Callable[..., Any]) -> Callable[..., Any]:
    if getattr(function, PATCH_MARKER, False):
        return function

    state_key = f"_pb_last_{name}_at"

    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any):
        now = time.monotonic()
        try:
            last = float(st.session_state.get(state_key, 0.0) or 0.0)
        except Exception:
            last = 0.0
        if last and now - last < SYNC_INTERVAL_SECONDS:
            return 0
        result = function(*args, **kwargs)
        try:
            st.session_state[state_key] = now
        except Exception:
            pass
        return result

    setattr(wrapper, PATCH_MARKER, True)
    wrapper._pb_original_sync = function
    return wrapper


def _install_sync_guards(st: Any) -> bool:
    installed = False
    # Streamlit executes the application as __main__. During the import of the
    # jobhub package, the scheduler/progress functions have already been imported
    # into that module and can be replaced without editing the large app file.
    candidates = [sys.modules.get("__main__")]
    for module in tuple(sys.modules.values()):
        module_file = str(getattr(module, "__file__", "") or "") if module is not None else ""
        if module_file.endswith("pb_jobhub_app.py"):
            candidates.append(module)

    seen: set[int] = set()
    for module in candidates:
        if module is None or id(module) in seen:
            continue
        seen.add(id(module))
        for name in ("sync_linked_job_dates", "sync_all_linked_progress"):
            function = getattr(module, name, None)
            if callable(function) and not getattr(function, PATCH_MARKER, False):
                setattr(module, name, _throttled_sync(st, name, function))
                installed = True
    return installed


def install_performance_guard() -> bool:
    st = _streamlit()
    if st is None:
        return False
    return _install_sync_guards(st)
