"""Prevent unnecessary full JobHub reruns and database-wide reconciliation.

The legacy app makes every dataframe selectable, even when it is only being used
as a report. Selecting one of those tables reruns the entire Streamlit script.
This guard keeps explicitly keyed tables interactive while making unkeyed report
tables display-only.

The app also calls both linked scheduler and linked progress reconciliation after
every interaction. The scheduler sync is now only run when linked dates are
actually dirty, and progress reconciliation is only run when its linked estimate
source signature has changed.
"""

from __future__ import annotations

import functools
import sys
from typing import Any, Callable


PATCH_MARKER = "_pb_runtime_performance_guard"
PROGRESS_SIGNATURE_KEY = "_pb_progress_source_signature"


def _streamlit() -> Any:
    return sys.modules.get("streamlit")


def _install_dataframe_guard(st: Any) -> bool:
    original = getattr(st, "dataframe", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    @functools.wraps(original)
    def dataframe_guard(*args: Any, **kwargs: Any):
        # pb_jobhub_app.py adds rerun selection to every dataframe globally.
        # Preserve tables with explicit keys because those are the tables used by
        # edit/delete workflows. Ordinary unkeyed reports should not become
        # widgets or rerun the entire 22k-line application when clicked.
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
    return True


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
