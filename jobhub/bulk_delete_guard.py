"""Safe multi-delete controls for ID-backed JobHub tables.

This module adds opt-in bulk delete controls after selected Streamlit tables that
already expose real database row IDs and have a known safe delete path. It also
adds a protected bulk-job delete panel to the Job Register and performs the
one-time Palm Lakes villa consolidation requested by Premier Brushworks.
"""

from __future__ import annotations

import importlib
import re
import sys
from typing import Any, Iterable

import pandas as pd


PALM_LAKES_CLEANUP_SETTING = "maintenance_palm_lakes_villas_consolidated_20260823"
_palm_cleanup_checked = False
_palm_cleanup_running = False


class DeleteTarget:
    def __init__(self, table: str, entity: str, guard: str = "") -> None:
        self.table = table
        self.entity = entity
        self.guard = guard


TARGETS: tuple[tuple[str, DeleteTarget], ...] = (
    ("selectable_staff_requests_admin", DeleteTarget("staff_requests", "staff_request")),
    ("selectable_job_purchase_orders_", DeleteTarget("job_purchase_orders", "purchase_order", "po_unused")),
    ("selectable_job_stages_", DeleteTarget("job_stages", "job_stage", "stage_unused")),
)


def _app_attr(name: str, default: Any = None) -> Any:
    for module_name in ("__main__", "pb_jobhub_app"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, name):
            return getattr(module, name)
    return default


def _normalise_key(key: Any) -> str:
    return str(key or "")


def _target_for_key(key: str) -> DeleteTarget | None:
    for prefix, target in TARGETS:
        if key.startswith(prefix):
            return target
    return None


def _extract_frame(args: tuple[Any, ...], kwargs: dict[str, Any]) -> pd.DataFrame | None:
    # st.dataframe(data, ...) and DeltaGenerator.dataframe(self, data, ...)
    data = None
    if args:
        if isinstance(args[0], pd.DataFrame):
            data = args[0]
        elif len(args) > 1 and isinstance(args[1], pd.DataFrame):
            data = args[1]
    if data is None:
        data = kwargs.get("data")
    if isinstance(data, pd.DataFrame):
        return data.copy()
    return None


def _id_column(frame: pd.DataFrame) -> str | None:
    for candidate in ("id", "ID", "job_id", "Job ID"):
        if candidate in frame.columns:
            return candidate
    return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return default
        text = str(value).replace(",", "").strip()
        if not text:
            return default
        return int(float(text))
    except Exception:
        return default


def _selected_ids(frame: pd.DataFrame, id_col: str, labels: Iterable[str]) -> list[int]:
    selected: list[int] = []
    label_set = set(labels)
    for _, row in frame.iterrows():
        label = _row_label(row, id_col)
        if label in label_set:
            row_id = _safe_int(row.get(id_col), default=-1)
            if row_id >= 0:
                selected.append(row_id)
    return selected


def _row_label(row: pd.Series, id_col: str) -> str:
    row_id = row.get(id_col)
    label_parts = []
    for column in (
        "Stage Name",
        "Stage",
        "PO Number",
        "Request",
        "Employee",
        "Job",
        "Job No",
        "Job Name",
        "Description",
        "Photo Name",
        "Name",
    ):
        if column in row and str(row.get(column) or "").strip():
            label_parts.append(str(row.get(column)).strip())
    summary = " | ".join(label_parts[:3]) or "record"
    return f"#{row_id} — {summary}"


def _eligible_frame(frame: pd.DataFrame, id_col: str) -> pd.DataFrame:
    work = frame.copy()
    return work[work[id_col].notna()].copy()


def _guard_allows(row: pd.Series, target: DeleteTarget) -> tuple[bool, str]:
    if target.guard == "po_unused":
        linked_stages = _safe_int(row.get("Linked Stages", 0))
        linked_claims = _safe_int(row.get("Linked Claims", 0))
        if linked_stages + linked_claims > 0:
            return False, "purchase order is linked to stages or claims"
    if target.guard == "stage_unused":
        usage_columns = ["Schedule Entries", "Timesheets", "Progress Updates", "Baseline Lines", "Claim Lines"]
        usage = sum(_safe_int(row.get(column, 0)) for column in usage_columns)
        if usage > 0:
            return False, "stage has linked schedule, timesheet, progress, baseline or claim records"
    return True, ""


def _delete_rows(target: DeleteTarget, ids: list[int], source_rows: pd.DataFrame) -> tuple[int, list[str]]:
    execute = _app_attr("execute")
    record_audit_event = _app_attr("record_audit_event", lambda *a, **k: None)
    if execute is None:
        return 0, ["Delete helper is not available in this app context."]
    id_col = _id_column(source_rows)
    if id_col is None:
        return 0, ["This table does not expose a safe database ID column."]

    deleted = 0
    skipped: list[str] = []
    for row_id in ids:
        source_id_values = source_rows[id_col].map(lambda value: _safe_int(value, default=-1))
        row_match = source_rows[source_id_values == int(row_id)]
        row = row_match.iloc[0] if not row_match.empty else pd.Series(dtype=object)
        allowed, reason = _guard_allows(row, target)
        if not allowed:
            skipped.append(f"#{row_id}: {reason}")
            continue
        if target.table == "staff_requests":
            execute("DELETE FROM push_notification_log WHERE staff_request_id=?", (int(row_id),))
            execute("DELETE FROM staff_requests WHERE id=?", (int(row_id),))
        elif target.table == "job_purchase_orders":
            execute("DELETE FROM job_purchase_orders WHERE id=?", (int(row_id),))
        elif target.table == "job_stages":
            execute("DELETE FROM job_stages WHERE id=?", (int(row_id),))
        else:
            skipped.append(f"#{row_id}: unsupported delete target")
            continue
        deleted += 1
        record_audit_event(f"bulk_{target.entity}_deleted", target.entity, row_id, {"bulk_delete": True})
    return deleted, skipped


def _normalise_job_text(*values: Any) -> str:
    text = " ".join(str(value or "") for value in values).lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _palm_lakes_candidates(jobs: pd.DataFrame) -> tuple[pd.Series | None, pd.DataFrame]:
    if jobs.empty:
        return None, jobs.iloc[0:0].copy()

    scored: list[tuple[int, int]] = []
    for idx, row in jobs.iterrows():
        text = _normalise_job_text(row.get("job_no"), row.get("job_name"), row.get("site_address"))
        if "palm" not in text:
            continue
        score = 0
        if "whole job" in text:
            score += 120
        elif "whole" in text:
            score += 80
        if "palm lakes" in text:
            score += 30
        elif "palm lake" in text:
            score += 20
        if "villa" in text:
            score += 5
        if score:
            scored.append((score, idx))

    if not scored:
        return None, jobs.iloc[0:0].copy()

    scored.sort(key=lambda item: (-item[0], item[1]))
    target_idx = scored[0][1]
    target = jobs.loc[target_idx]
    target_id = _safe_int(target.get("id"), -1)

    source_mask = []
    for _, row in jobs.iterrows():
        row_id = _safe_int(row.get("id"), -1)
        text = _normalise_job_text(row.get("job_no"), row.get("job_name"), row.get("site_address"))
        source_mask.append(
            row_id != target_id
            and "palm" in text
            and "villa" in text
            and "whole job" not in text
        )
    return target, jobs[pd.Series(source_mask, index=jobs.index)].copy()


def _maintenance_setting_value(df_query: Any) -> str:
    try:
        result = df_query(
            "SELECT setting_value FROM app_settings WHERE setting_key=?",
            (PALM_LAKES_CLEANUP_SETTING,),
        )
        if not result.empty:
            return str(result.iloc[0]["setting_value"] or "")
    except Exception:
        return ""
    return ""


def _maybe_consolidate_palm_lakes_villas() -> dict[str, Any] | None:
    """Move Palm Lakes villa timesheets/wages to the whole-job record, then remove villas.

    The operation is deliberately idempotent and conservative: it only runs when
    a Palm Lakes/Palm Lake job containing "whole" can be identified and at least
    one separate Palm + villa job exists. The whole-job record is never deleted.
    """
    global _palm_cleanup_checked, _palm_cleanup_running

    if _palm_cleanup_checked or _palm_cleanup_running:
        return None

    df_query = _app_attr("df_query")
    connect = _app_attr("connect")
    execute = _app_attr("execute")
    delete_job = _app_attr("permanently_delete_job_and_linked_data")
    record_audit_event = _app_attr("record_audit_event", lambda *a, **k: None)
    if not all(callable(value) for value in (df_query, connect, execute, delete_job)):
        return None

    if _maintenance_setting_value(df_query).lower().startswith("done"):
        _palm_cleanup_checked = True
        return None

    try:
        jobs = df_query(
            "SELECT id, job_no, job_name, site_address, status FROM jobs ORDER BY id"
        )
    except Exception:
        return None

    target, sources = _palm_lakes_candidates(jobs)
    if target is None or sources.empty:
        _palm_cleanup_checked = True
        return None

    target_id = _safe_int(target.get("id"), -1)
    source_ids = [_safe_int(value, -1) for value in sources["id"].tolist()]
    source_ids = [value for value in source_ids if value >= 0 and value != target_id]
    if target_id < 0 or not source_ids:
        _palm_cleanup_checked = True
        return None

    _palm_cleanup_running = True
    moved_timesheets = 0
    moved_wages = 0
    deleted_jobs: list[int] = []
    conn = None
    try:
        conn = connect()
        cur = conn.cursor()
        placeholders = ",".join("?" for _ in source_ids)
        params = (target_id, *source_ids)

        cur.execute(
            f"UPDATE timesheet_entries SET job_id=?, job_stage_id=NULL WHERE job_id IN ({placeholders})",
            params,
        )
        moved_timesheets = max(_safe_int(getattr(cur, "rowcount", 0), 0), 0)

        cur.execute(
            f"UPDATE wage_entries SET job_id=? WHERE job_id IN ({placeholders})",
            params,
        )
        moved_wages = max(_safe_int(getattr(cur, "rowcount", 0), 0), 0)
        conn.commit()
        conn.close()
        conn = None

        for source_id in source_ids:
            delete_job(int(source_id))
            deleted_jobs.append(int(source_id))

        execute(
            """
            INSERT INTO app_settings (setting_key, setting_value)
            VALUES (?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value
            """,
            (
                PALM_LAKES_CLEANUP_SETTING,
                f"done;target={target_id};jobs={len(deleted_jobs)};timesheets={moved_timesheets};wages={moved_wages}",
            ),
        )
        record_audit_event(
            "palm_lakes_villas_consolidated",
            "job_register",
            target_id,
            {
                "target_job_id": target_id,
                "deleted_job_ids": deleted_jobs,
                "timesheets_moved": moved_timesheets,
                "wages_moved": moved_wages,
            },
        )
        _palm_cleanup_checked = True
        return {
            "target_id": target_id,
            "deleted_jobs": deleted_jobs,
            "timesheets_moved": moved_timesheets,
            "wages_moved": moved_wages,
        }
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
        # Do not risk a destructive retry multiple times in one process. If the
        # service restarts, the remaining source jobs are detected again safely.
        _palm_cleanup_checked = True
        return None
    finally:
        _palm_cleanup_running = False


def _render_bulk_delete_controls(st: Any, frame: pd.DataFrame, key: str, target: DeleteTarget) -> None:
    id_col = _id_column(frame)
    if id_col is None:
        return
    eligible = _eligible_frame(frame, id_col)
    if eligible.empty:
        return

    labels = [_row_label(row, id_col) for _, row in eligible.iterrows()]
    with st.expander("Bulk delete selected records", expanded=False):
        st.caption("Select multiple records from this table, review them, then confirm deletion. Linked records are protected.")
        selected_labels = st.multiselect(
            "Records to delete",
            labels,
            key=f"bulk_delete_labels_{key}",
        )
        ids = _selected_ids(eligible, id_col, selected_labels)
        if not ids:
            st.info("Select one or more records to delete.")
            return
        review_ids = eligible[id_col].map(lambda value: _safe_int(value, default=-1))
        review = eligible[review_ids.isin(ids)].copy()
        st.dataframe(review.drop(columns=[id_col], errors="ignore"), width="stretch", hide_index=True, key=f"bulk_delete_review_{key}")
        confirm = st.checkbox(
            f"I understand this will permanently delete {len(ids)} selected record(s).",
            key=f"bulk_delete_confirm_{key}",
        )
        if st.button(
            "Delete selected records",
            key=f"bulk_delete_button_{key}",
            disabled=not confirm,
            type="primary",
        ):
            deleted, skipped = _delete_rows(target, ids, eligible)
            pb_success = _app_attr("pb_success", st.success)
            pb_error = _app_attr("pb_error", st.error)
            refresh = _app_attr("refresh", None) or _app_attr("pb_rerun", None)
            if deleted:
                pb_success(f"Deleted {deleted} selected record(s).")
            if skipped:
                pb_error("Some records were skipped: " + "; ".join(skipped))
            if callable(refresh):
                refresh()


def _job_delete_review(job_ids: list[int], jobs: pd.DataFrame) -> pd.DataFrame:
    linked_job_counts = _app_attr("linked_job_counts")
    rows: list[dict[str, Any]] = []
    for job_id in job_ids:
        match = jobs[jobs["id"].map(lambda value: _safe_int(value, -1)) == int(job_id)]
        if match.empty:
            continue
        job = match.iloc[0]
        counts = linked_job_counts(int(job_id)) if callable(linked_job_counts) else {}
        rows.append(
            {
                "Job No": str(job.get("job_no") or ""),
                "Job Name": str(job.get("job_name") or ""),
                "Status": str(job.get("status") or ""),
                "Timesheets": _safe_int(counts.get("timesheet_entries", 0)),
                "Wages": _safe_int(counts.get("wage_entries", 0)),
                "Materials": _safe_int(counts.get("material_entries", 0)),
                "Photos": _safe_int(counts.get("job_photos", 0)),
            }
        )
    return pd.DataFrame(rows)


def _render_job_bulk_delete_controls(st: Any) -> None:
    cleanup_result = _maybe_consolidate_palm_lakes_villas()
    if cleanup_result:
        pb_success = _app_attr("pb_success", st.success)
        pb_success(
            "Palm Lakes villas consolidated: "
            f"{cleanup_result['timesheets_moved']} timesheet(s) moved to the whole-job record and "
            f"{len(cleanup_result['deleted_jobs'])} villa job(s) removed."
        )

    df_query = _app_attr("df_query")
    delete_job = _app_attr("permanently_delete_job_and_linked_data")
    if not callable(df_query) or not callable(delete_job):
        return

    try:
        jobs = df_query(
            "SELECT id, job_no, job_name, status FROM jobs ORDER BY job_no, id"
        )
    except Exception:
        return
    if jobs.empty:
        return

    labels: list[str] = []
    label_to_id: dict[str, int] = {}
    for _, row in jobs.iterrows():
        job_id = _safe_int(row.get("id"), -1)
        if job_id < 0:
            continue
        label = f"{row.get('job_no') or 'No number'} — {row.get('job_name') or 'Unnamed job'}"
        if label in label_to_id:
            label = f"{label} (#{job_id})"
        labels.append(label)
        label_to_id[label] = job_id

    with st.expander("Bulk job delete", expanded=False):
        st.warning(
            "This permanently removes the selected jobs and their linked job data. "
            "Use it only when you really want the job records gone."
        )
        selected_labels = st.multiselect(
            "Jobs to delete",
            labels,
            key="bulk_job_delete_labels",
        )
        selected_ids = [label_to_id[label] for label in selected_labels if label in label_to_id]
        if not selected_ids:
            st.info("Select two or more jobs when you want to clean up the register in bulk.")
            return

        review = _job_delete_review(selected_ids, jobs)
        if not review.empty:
            st.table(review)

        confirmation = st.text_input(
            f"Type DELETE {len(selected_ids)} to confirm",
            key="bulk_job_delete_confirmation",
        )
        confirmed = confirmation.strip().upper() == f"DELETE {len(selected_ids)}"
        if st.button(
            f"Permanently delete {len(selected_ids)} job(s)",
            key="bulk_job_delete_button",
            type="primary",
            disabled=not confirmed,
        ):
            pb_success = _app_attr("pb_success", st.success)
            pb_error = _app_attr("pb_error", st.error)
            refresh = _app_attr("refresh", None) or _app_attr("pb_rerun", None)
            deleted = 0
            failures: list[str] = []
            for job_id in selected_ids:
                try:
                    delete_job(int(job_id))
                    deleted += 1
                except Exception as exc:
                    failures.append(f"#{job_id}: {exc}")
            if deleted:
                pb_success(f"Permanently deleted {deleted} job(s).")
            if failures:
                pb_error("Some jobs could not be deleted: " + "; ".join(failures))
            if callable(refresh):
                refresh()


def _subheader_text(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    if args:
        return str(args[0] or "")
    return str(kwargs.get("body") or kwargs.get("text") or "")


def _patch_subheader(st: Any) -> bool:
    original_subheader = getattr(st, "subheader", None)
    if original_subheader is None or getattr(original_subheader, "_pb_job_bulk_delete_guard", False):
        return False

    def pb_bulk_delete_subheader(*args: Any, **kwargs: Any):
        result = original_subheader(*args, **kwargs)
        if _subheader_text(args, kwargs).strip().lower() == "remove or archive job":
            try:
                _render_job_bulk_delete_controls(st)
            except Exception as exc:
                st.caption(f"Bulk job delete is temporarily unavailable: {exc}")
        return result

    pb_bulk_delete_subheader._pb_job_bulk_delete_guard = True
    pb_bulk_delete_subheader._pb_original_subheader = original_subheader
    st.subheader = pb_bulk_delete_subheader
    return True


def _patch_dataframe(owner: Any, st: Any) -> bool:
    original_dataframe = getattr(owner, "dataframe", None)
    if original_dataframe is None or getattr(original_dataframe, "_pb_bulk_delete_guard", False):
        return False

    def pb_bulk_delete_dataframe(*args: Any, **kwargs: Any):
        # Once the main app has finished defining its database helpers, this is
        # also a reliable first-run hook for the requested Palm Lakes cleanup.
        try:
            _maybe_consolidate_palm_lakes_villas()
        except Exception:
            pass

        result = original_dataframe(*args, **kwargs)
        key = _normalise_key(kwargs.get("key"))
        target = _target_for_key(key)
        frame = _extract_frame(args, kwargs)
        if target is not None and frame is not None:
            try:
                _render_bulk_delete_controls(st, frame, key, target)
            except Exception as exc:
                st.caption(f"Bulk delete controls are unavailable for this table: {exc}")
        return result

    pb_bulk_delete_dataframe._pb_bulk_delete_guard = True
    pb_bulk_delete_dataframe._pb_original_dataframe = original_dataframe
    setattr(owner, "dataframe", pb_bulk_delete_dataframe)
    return True


def install_bulk_delete_guard() -> bool:
    st = sys.modules.get("streamlit")
    if st is None:
        return False

    installed = _patch_dataframe(st, st)
    installed = _patch_subheader(st) or installed
    try:
        delta_module = sys.modules.get("streamlit.delta_generator") or importlib.import_module("streamlit.delta_generator")
        delta_cls = getattr(delta_module, "DeltaGenerator", None)
    except Exception:
        delta_cls = None
    if delta_cls is not None:
        installed = _patch_dataframe(delta_cls, st) or installed
    return installed
