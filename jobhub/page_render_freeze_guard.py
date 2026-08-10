"""Remove slow maintenance work from ordinary JobHub page renders.

Three legacy maintenance jobs were called before every signed-in page could be
shown: daily CSV backup creation, linked scheduler reconciliation and linked
progress reconciliation.  The backup can export every database table, while the
progress sync can scan every linked estimate.  Those are useful maintenance
operations, but they must not run synchronously during an unrelated button click.

This guard keeps normal pages fast:

* automatic daily backup creation is deferred; the existing manual backup tools
  remain available in Operations Hub > System / Backups;
* linked scheduler dates are checked only while a scheduler view is open and no
  more than once per minute in the same browser session;
* linked estimate quantities refresh only when the user explicitly requests it
  from the progress page.
"""

from __future__ import annotations

from datetime import date
import functools
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable

from jobhub_time import jobhub_today


PATCH_MARKER = "_pb_page_render_freeze_guard"
ORIGINAL_SYNC_ATTR = "_pb_original_sync"
DAILY_BACKUP_DUE_KEY = "_pb_daily_backup_due"
SCHEDULER_LAST_SYNC_KEY = "_pb_scheduler_last_view_sync"
SCHEDULER_SYNC_INTERVAL_SECONDS = 60.0


def _streamlit() -> Any:
    return sys.modules.get("streamlit")


def _unwrap(function: Callable[..., Any], attribute: str) -> Callable[..., Any]:
    current = function
    seen: set[int] = set()
    while callable(current) and id(current) not in seen:
        seen.add(id(current))
        original = getattr(current, attribute, None)
        if not callable(original):
            break
        current = original
    return current


def _daily_backup_exists(context: dict[str, Any]) -> bool:
    data_dir = Path(str(context.get("DATA_DIR") or "/var/data"))
    backup_dir = data_dir / "backups"
    prefix = f"PB_JobHub_Daily_Data_{jobhub_today().strftime('%Y%m%d')}"
    try:
        return any(path.name.startswith(prefix) for path in backup_dir.glob("*.zip"))
    except OSError:
        return False


def _wrap_daily_backup(function: Callable[..., Any], st: Any) -> Callable[..., Any]:
    if getattr(function, PATCH_MARKER, False):
        return function
    original = _unwrap(function, "_pb_original_daily_backup")

    @functools.wraps(function)
    def deferred_daily_backup(context: dict[str, Any], *args: Any, **kwargs: Any):
        enabled = str(os.getenv("JOBHUB_BACKUP_ON_PAGE_LOAD", "")).strip().lower()
        if enabled in {"1", "true", "yes", "on"}:
            return original(context, *args, **kwargs)

        try:
            due = not _daily_backup_exists(context)
            st.session_state[DAILY_BACKUP_DUE_KEY] = due
        except Exception:
            pass
        return None

    setattr(deferred_daily_backup, PATCH_MARKER, True)
    deferred_daily_backup._pb_original_daily_backup = original
    return deferred_daily_backup


def _skip_startup_sync(function: Callable[..., Any], kind: str) -> Callable[..., Any]:
    if getattr(function, PATCH_MARKER, False):
        return function
    original = _unwrap(function, ORIGINAL_SYNC_ATTR)

    @functools.wraps(function)
    def skipped_sync(*args: Any, **kwargs: Any) -> int:
        return 0

    setattr(skipped_sync, PATCH_MARKER, True)
    setattr(skipped_sync, ORIGINAL_SYNC_ATTR, original)
    skipped_sync._pb_sync_kind = kind
    return skipped_sync


def _wrap_scheduler_renderer(
    renderer: Callable[..., Any],
    sync_function: Callable[..., Any],
    st: Any,
) -> Callable[..., Any]:
    if getattr(renderer, PATCH_MARKER, False):
        return renderer

    @functools.wraps(renderer)
    def scheduler_renderer(*args: Any, **kwargs: Any):
        now = time.monotonic()
        try:
            previous = float(st.session_state.get(SCHEDULER_LAST_SYNC_KEY, 0.0) or 0.0)
        except Exception:
            previous = 0.0
        if now - previous >= SCHEDULER_SYNC_INTERVAL_SECONDS:
            try:
                moved = int(sync_function() or 0)
                st.session_state[SCHEDULER_LAST_SYNC_KEY] = now
                if moved:
                    try:
                        st.toast(
                            f"Updated {moved} linked scheduler assignment(s).",
                            icon="✅",
                        )
                    except Exception:
                        pass
            except Exception as exc:
                # Database timeout errors should not prevent the scheduler itself
                # from opening.  The next view retries after the throttle window.
                try:
                    st.session_state[SCHEDULER_LAST_SYNC_KEY] = now
                    st.warning(f"Linked date refresh was skipped: {exc}")
                except Exception:
                    pass
        return renderer(*args, **kwargs)

    setattr(scheduler_renderer, PATCH_MARKER, True)
    scheduler_renderer._pb_original_renderer = renderer
    scheduler_renderer._pb_original_sync = sync_function
    return scheduler_renderer


def _wrap_progress_renderer(
    renderer: Callable[..., Any],
    sync_function: Callable[..., Any],
    st: Any,
) -> Callable[..., Any]:
    if getattr(renderer, PATCH_MARKER, False):
        return renderer

    @functools.wraps(renderer)
    def progress_renderer(context: dict[str, Any], *args: Any, **kwargs: Any):
        try:
            requested = st.button(
                "Refresh linked estimate quantities",
                key="pb_refresh_linked_progress_quantities",
                help=(
                    "Run the estimator-to-progress reconciliation now. This is no "
                    "longer run during every unrelated JobHub click."
                ),
            )
        except Exception:
            requested = False

        if requested:
            try:
                spinner = getattr(st, "spinner", None)
                if callable(spinner):
                    with spinner("Refreshing linked progress quantities..."):
                        changed = int(sync_function(context) or 0)
                else:
                    changed = int(sync_function(context) or 0)
                success = context.get("pb_success")
                message = (
                    f"Linked progress refreshed. {changed} new external line(s) added."
                    if changed
                    else "Linked progress is already up to date."
                )
                if callable(success):
                    success(message)
                else:
                    st.success(message)
            except Exception as exc:
                error = context.get("pb_error")
                message = f"Linked progress refresh stopped safely: {exc}"
                if callable(error):
                    error(message)
                else:
                    st.error(message)

        return renderer(context, *args, **kwargs)

    setattr(progress_renderer, PATCH_MARKER, True)
    progress_renderer._pb_original_renderer = renderer
    progress_renderer._pb_original_sync = sync_function
    return progress_renderer


def _candidate_app_modules() -> list[Any]:
    candidates: list[Any] = []
    seen: set[int] = set()
    for module in tuple(sys.modules.values()):
        if module is None or id(module) in seen:
            continue
        module_file = str(getattr(module, "__file__", "") or "")
        if module is sys.modules.get("__main__") or module_file.endswith("pb_jobhub_app.py"):
            seen.add(id(module))
            candidates.append(module)
    return candidates


def install_page_render_freeze_guard() -> bool:
    st = _streamlit()
    if st is None:
        return False

    installed = False
    for module in _candidate_app_modules():
        daily_backup = getattr(module, "ensure_daily_backup", None)
        if callable(daily_backup) and not getattr(daily_backup, PATCH_MARKER, False):
            setattr(module, "ensure_daily_backup", _wrap_daily_backup(daily_backup, st))
            installed = True

        scheduler_sync = getattr(module, "sync_linked_job_dates", None)
        progress_sync = getattr(module, "sync_all_linked_progress", None)
        scheduler_original = (
            _unwrap(scheduler_sync, ORIGINAL_SYNC_ATTR) if callable(scheduler_sync) else None
        )
        progress_original = (
            _unwrap(progress_sync, ORIGINAL_SYNC_ATTR) if callable(progress_sync) else None
        )

        if callable(scheduler_original):
            for name in ("render_jobhub_staff_scheduler", "render_job_folder_schedule_editor"):
                renderer = getattr(module, name, None)
                if callable(renderer) and not getattr(renderer, PATCH_MARKER, False):
                    setattr(
                        module,
                        name,
                        _wrap_scheduler_renderer(renderer, scheduler_original, st),
                    )
                    installed = True
            if callable(scheduler_sync) and not getattr(scheduler_sync, PATCH_MARKER, False):
                setattr(module, "sync_linked_job_dates", _skip_startup_sync(scheduler_sync, "scheduler"))
                installed = True

        if callable(progress_original):
            renderer = getattr(module, "render_progress_tracker", None)
            if callable(renderer) and not getattr(renderer, PATCH_MARKER, False):
                setattr(
                    module,
                    "render_progress_tracker",
                    _wrap_progress_renderer(renderer, progress_original, st),
                )
                installed = True
            if callable(progress_sync) and not getattr(progress_sync, PATCH_MARKER, False):
                setattr(module, "sync_all_linked_progress", _skip_startup_sync(progress_sync, "progress"))
                installed = True

    return installed
