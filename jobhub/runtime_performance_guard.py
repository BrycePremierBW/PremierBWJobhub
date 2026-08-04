"""Prevent unnecessary full JobHub reruns and database-wide reconciliation.

The legacy app wraps every ``st.dataframe`` call and adds ``on_select='rerun'``
when the caller did not request selection.  The first freeze guard could only
remove that behaviour from unkeyed tables.  JobHub also uses keys on many normal
report tables to avoid duplicate Streamlit element IDs, so clicking those tables
still restarted the entire 22,000-line application.

This module now intercepts the legacy wrapper when it is assigned to Streamlit.
Calls that did not explicitly request selection are marked display-only *before*
the legacy wrapper sees them.  Tables that deliberately pass ``on_select`` or
``selection_mode`` remain interactive, regardless of whether they have a key.

The scheduler and linked-progress reconciliation guards remain unchanged: they
only run database-wide synchronisation when source data is actually dirty.
"""

from __future__ import annotations

import functools
import sys
from typing import Any, Callable


PATCH_MARKER = "_pb_runtime_performance_guard"
ASSIGNMENT_MARKER = "_pb_dataframe_assignment_guard"
DISPLAY_ONLY_MARKER = "_pb_dataframe_display_only_default"
PROGRESS_SIGNATURE_KEY = "_pb_progress_source_signature"


def _streamlit() -> Any:
    return sys.modules.get("streamlit")


def _protect_legacy_dataframe_wrapper(function: Callable[..., Any]) -> Callable[..., Any]:
    """Make selection opt-in before JobHub's legacy dataframe wrapper runs.

    JobHub's wrapper checks only whether ``on_select`` exists.  Supplying
    ``on_select='ignore'`` here prevents it from injecting a full-app rerun.  An
    explicit selection argument from the real caller is never changed.
    """
    if getattr(function, DISPLAY_ONLY_MARKER, False):
        return function

    @functools.wraps(function)
    def display_only_by_default(*args: Any, **kwargs: Any):
        selection_was_explicit = "on_select" in kwargs or "selection_mode" in kwargs
        if not selection_was_explicit:
            kwargs["on_select"] = "ignore"
        return function(*args, **kwargs)

    setattr(display_only_by_default, DISPLAY_ONLY_MARKER, True)
    # Keep the attributes expected by pb_jobhub_app.py on later Streamlit reruns.
    # This stops the app from wrapping this protected function again and building
    # a deeper wrapper chain during a long browser session.
    setattr(display_only_by_default, "_pb_selectable_wrapper", True)
    display_only_by_default._pb_original_dataframe = getattr(
        function,
        "_pb_original_dataframe",
        function,
    )
    display_only_by_default._pb_legacy_selectable_wrapper = function
    return display_only_by_default


def _install_dataframe_assignment_guard(st: Any) -> bool:
    """Intercept the later assignment of JobHub's global dataframe wrapper.

    The ``jobhub`` package is imported before ``pb_jobhub_app.py`` creates its
    legacy selectable wrapper.  A small ModuleType subclass lets us protect that
    one later assignment without editing or replacing the main application.
    """
    module_class = type(st)
    if getattr(module_class, ASSIGNMENT_MARKER, False):
        return False

    try:
        class GuardedStreamlitModule(module_class):
            def __setattr__(self, name: str, value: Any) -> None:
                if (
                    name == "dataframe"
                    and callable(value)
                    and getattr(value, "_pb_selectable_wrapper", False)
                    and not getattr(value, DISPLAY_ONLY_MARKER, False)
                ):
                    value = _protect_legacy_dataframe_wrapper(value)
                super().__setattr__(name, value)

        setattr(GuardedStreamlitModule, ASSIGNMENT_MARKER, True)
        st.__class__ = GuardedStreamlitModule
    except (AttributeError, TypeError):
        # Test doubles and unusual embedded runtimes may not allow module class
        # replacement.  The existing underlying dataframe guard still applies.
        return False

    current = getattr(st, "dataframe", None)
    if (
        callable(current)
        and getattr(current, "_pb_selectable_wrapper", False)
        and not getattr(current, DISPLAY_ONLY_MARKER, False)
    ):
        setattr(st, "dataframe", current)
    return True


def _install_dataframe_guard(st: Any) -> bool:
    original = getattr(st, "dataframe", None)
    installed = False

    if original is not None and not getattr(original, PATCH_MARKER, False):
        @functools.wraps(original)
        def dataframe_guard(*args: Any, **kwargs: Any):
            # Backward-compatible fallback for a legacy wrapper that was already
            # installed before this module.  The assignment guard above is the
            # primary fix because it can distinguish explicit selection from the
            # legacy wrapper's automatic selection.
            if (
                kwargs.get("on_select") == "rerun"
                and kwargs.get("selection_mode") == "single-row"
                and not kwargs.get("key")
            ):
                kwargs["on_select"] = "ignore"
                kwargs.pop("selection_mode", None)
            return original(*args, **kwargs)

        setattr(dataframe_guard, PATCH_MARKER, True)
        dataframe_guard._pb_original_dataframe = original
        st.dataframe = dataframe_guard
        installed = True

    installed = _install_dataframe_assignment_guard(st) or installed
    return installed


def _scheduler_has_dirty_links() -> bool:
    scheduler = sys.modules.get("pb_jobhub_visual_scheduler")
    scalar = getattr(scheduler, "scalar", None) if scheduler is not None else None
    if not callable(scalar):
        return True
    changed = scalar(
        """
        SELECT COUNT(*)
        FROM staff_schedule s
        JOIN jobs j ON j.id=s.job_id
        WHERE COALESCE(s.linked_to_job_dates,0)=1
          AND s.job_day_offset IS NOT NULL
          AND COALESCE(j.start_date,'')<>''
          AND COALESCE(s.last_job_start_date,'')<>COALESCE(j.start_date,'')
        """,
        (),
        0,
    )
    return bool(int(changed or 0))


def _progress_source_signature(context: dict[str, Any]) -> tuple[Any, ...] | None:
    query = context.get("df_query")
    if not callable(query):
        return None
    frame = query(
        """
        SELECT COUNT(DISTINCT s.job_id) AS linked_jobs,
               COUNT(l.id) AS line_count,
               COALESCE(MAX(l.id),0) AS max_line_id,
               COALESCE(MAX(COALESCE(e.updated_at,'')),'') AS latest_estimate_update,
               COALESCE(SUM(COALESCE(l.qty,0)),0) AS total_qty,
               COALESCE(SUM(COALESCE(l.line_total,0)),0) AS total_value,
               COALESCE(SUM(
                   LENGTH(COALESCE(l.item_description,''))
                   + LENGTH(COALESCE(l.substrate,''))
                   + LENGTH(COALESCE(l.work_location,''))
               ),0) AS text_size
        FROM job_progress_settings s
        JOIN estimate_working_sheets e ON e.id=s.linked_estimate_id
        LEFT JOIN estimate_line_items l ON l.estimate_id=e.id
        WHERE s.linked_estimate_id IS NOT NULL
        """
    )
    if frame is None or frame.empty:
        return (0, 0, 0, "", 0.0, 0.0, 0)
    row = frame.iloc[0]
    return (
        int(row.get("linked_jobs") or 0),
        int(row.get("line_count") or 0),
        int(row.get("max_line_id") or 0),
        str(row.get("latest_estimate_update") or ""),
        round(float(row.get("total_qty") or 0), 4),
        round(float(row.get("total_value") or 0), 4),
        int(row.get("text_size") or 0),
    )


def _wrap_scheduler_sync(function: Callable[..., Any]) -> Callable[..., Any]:
    if getattr(function, PATCH_MARKER, False):
        return function

    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any):
        try:
            if not _scheduler_has_dirty_links():
                return 0
        except Exception:
            # Fall back to the existing behaviour if a legacy schema differs.
            pass
        return function(*args, **kwargs)

    setattr(wrapper, PATCH_MARKER, True)
    wrapper._pb_original_sync = function
    return wrapper


def _wrap_progress_sync(st: Any, function: Callable[..., Any]) -> Callable[..., Any]:
    if getattr(function, PATCH_MARKER, False):
        return function

    @functools.wraps(function)
    def wrapper(context: dict[str, Any], *args: Any, **kwargs: Any):
        try:
            signature = _progress_source_signature(context)
            previous = st.session_state.get(PROGRESS_SIGNATURE_KEY)
            if signature is not None and previous == signature:
                return 0
        except Exception:
            signature = None

        result = function(context, *args, **kwargs)
        if signature is not None:
            try:
                st.session_state[PROGRESS_SIGNATURE_KEY] = signature
            except Exception:
                pass
        return result

    setattr(wrapper, PATCH_MARKER, True)
    wrapper._pb_original_sync = function
    return wrapper


def _install_sync_guards(st: Any) -> bool:
    installed = False
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

        scheduler_sync = getattr(module, "sync_linked_job_dates", None)
        if callable(scheduler_sync) and not getattr(scheduler_sync, PATCH_MARKER, False):
            setattr(module, "sync_linked_job_dates", _wrap_scheduler_sync(scheduler_sync))
            installed = True

        progress_sync = getattr(module, "sync_all_linked_progress", None)
        if callable(progress_sync) and not getattr(progress_sync, PATCH_MARKER, False):
            setattr(module, "sync_all_linked_progress", _wrap_progress_sync(st, progress_sync))
            installed = True

    return installed


def install_runtime_performance_guard() -> bool:
    st = _streamlit()
    if st is None:
        return False
    installed = _install_dataframe_guard(st)
    installed = _install_sync_guards(st) or installed
    return installed
