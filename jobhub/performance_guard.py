"""Reduce unnecessary full-app reruns and repeated startup synchronisation.

This module is installed while ``pb_jobhub_app.py`` is still importing. It keeps
all explicit selectable tables working, but prevents ordinary display-only
``st.dataframe`` calls from inheriting the application's global ``on_select='rerun'``
behaviour. It also throttles the two whole-database synchronisers that the main
script invokes after every Streamlit interaction.
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


def _install_dataframe_guard(st: Any) -> bool:
    original = getattr(st, "dataframe", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    @functools.wraps(original)
    def dataframe_without_implicit_rerun(*args: Any, **kwargs: Any):
        # The legacy main app globally adds on_select='rerun' to every dataframe.
        # Keep explicitly keyed/selectable tables unchanged, while making normal
        # reporting tables display-only so scrolling/clicking them cannot rerun
        # the entire 22k-line application.
        if (
            kwargs.get("on_select") == "rerun"
            and kwargs.get("selection_mode") == "single-row"
            and not kwargs.get("key")
        ):
            kwargs.pop("on_select", None)
            kwargs.pop("selection_mode", None)
        return original(*args, **kwargs)

    setattr(dataframe_without_implicit_rerun, PATCH_MARKER, True)
    dataframe_without_implicit_rerun._pb_original_dataframe = original
    st.dataframe = dataframe_without_implicit_rerun
    return True


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
    installed = _install_dataframe_guard(st)
    installed = _install_sync_guards(st) or installed
    return installed
