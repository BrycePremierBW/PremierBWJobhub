"""Safe multi-delete controls for ID-backed JobHub tables.

This module adds opt-in bulk delete controls after selected Streamlit tables that
already expose real database row IDs and have a known safe delete path. It does
not delete calculated KPI tiles or arbitrary tables.
"""

from __future__ import annotations

import sys
from typing import Any, Iterable

import pandas as pd


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
    data = args[0] if args else kwargs.get("data")
    if isinstance(data, pd.DataFrame):
        return data.copy()
    return None


def _id_column(frame: pd.DataFrame) -> str | None:
    if "id" in frame.columns:
        return "id"
    if "ID" in frame.columns:
        return "ID"
    return None


def _selected_ids(frame: pd.DataFrame, id_col: str, labels: Iterable[str]) -> list[int]:
    selected = []
    label_set = set(labels)
    for _, row in frame.iterrows():
        label = _row_label(row, id_col)
        if label in label_set:
            try:
                selected.append(int(row[id_col]))
            except Exception:
                continue
    return selected


def _row_label(row: pd.Series, id_col: str) -> str:
    row_id = row.get(id_col)
    label_parts = []
    for column in ("Stage Name", "Stage", "PO Number", "Request", "Employee", "Job", "Description", "Photo Name", "Name"):
        if column in row and str(row.get(column) or "").strip():
            label_parts.append(str(row.get(column)).strip())
    summary = " | ".join(label_parts[:3]) or "record"
    return f"#{row_id} — {summary}"


def _eligible_frame(frame: pd.DataFrame, id_col: str) -> pd.DataFrame:
    work = frame.copy()
    return work[work[id_col].notna()].copy()


def _guard_allows(row: pd.Series, target: DeleteTarget) -> tuple[bool, str]:
    if target.guard == "po_unused":
        linked_stages = int(row.get("Linked Stages", 0) or 0)
        linked_claims = int(row.get("Linked Claims", 0) or 0)
        if linked_stages + linked_claims > 0:
            return False, "purchase order is linked to stages or claims"
    if target.guard == "stage_unused":
        usage_columns = ["Schedule Entries", "Timesheets", "Progress Updates", "Baseline Lines", "Claim Lines"]
        usage = sum(int(row.get(column, 0) or 0) for column in usage_columns)
        if usage > 0:
            return False, "stage has linked schedule, timesheet, progress, baseline or claim records"
    return True, ""


def _delete_rows(target: DeleteTarget, ids: list[int], source_rows: pd.DataFrame) -> tuple[int, list[str]]:
    execute = _app_attr("execute")
    record_audit_event = _app_attr("record_audit_event", lambda *a, **k: None)
    if execute is None:
        return 0, ["Delete helper is not available in this app context."]

    deleted = 0
    skipped = []
    for row_id in ids:
        row_match = source_rows[source_rows[_id_column(source_rows)].astype(int) == int(row_id)]
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
        review = eligible[eligible[id_col].astype(int).isin(ids)].copy()
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


def install_bulk_delete_guard() -> bool:
    st = sys.modules.get("streamlit")
    if st is None:
        return False

    original_dataframe = getattr(st, "dataframe", None)
    if original_dataframe is None or getattr(original_dataframe, "_pb_bulk_delete_guard", False):
        return False

    def pb_bulk_delete_dataframe(*args: Any, **kwargs: Any):
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
    st.dataframe = pb_bulk_delete_dataframe
    return True
