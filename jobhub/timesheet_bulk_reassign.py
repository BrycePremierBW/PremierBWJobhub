"""Admin bulk reassignment for existing JobHub timesheets.

Installed before the main Streamlit app starts. The wrapper recognises the
existing bulk-timesheet selector and adds one safe action: change the Job / Stage
for every selected timesheet while preserving each employee, date, shift, hours,
work type and notes. Existing approved/paid wage postings stay consistent because
updates are routed through pb_jobhub_app.update_timesheet_entry().
"""
from __future__ import annotations

import importlib
import sys
from typing import Any

import pandas as pd


_PATCH_MARKER = "_pb_bulk_timesheet_job_reassign"


def _app_attr(name: str, default: Any = None) -> Any:
    for module_name in ("__main__", "pb_jobhub_app"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, name):
            return getattr(module, name)
    return default


def _is_admin() -> bool:
    role_fn = _app_attr("current_role")
    if callable(role_fn):
        try:
            return str(role_fn() or "").strip().lower() == "admin"
        except Exception:
            return False
    return False


def _is_timesheet_selector(key: Any, frame: Any) -> bool:
    if not isinstance(frame, pd.DataFrame):
        return False
    key_text = str(key or "")
    if "_checkbox_table_" not in key_text:
        return False
    required = {"Select", "id", "Status"}
    if not required.issubset(set(frame.columns)):
        return False
    # At least one of the normal timesheet display fields must also be present.
    return bool({"Employee", "Date", "Hours", "Job", "Job Name"} & set(frame.columns))


def _selected_ids(edited_frame: pd.DataFrame) -> list[int]:
    if "Select" not in edited_frame.columns or "id" not in edited_frame.columns:
        return []
    selected = edited_frame.loc[edited_frame["Select"].fillna(False), "id"]
    ids: list[int] = []
    for value in selected.tolist():
        try:
            ids.append(int(value))
        except Exception:
            continue
    return ids


def _load_selected_timesheets(ids: list[int]) -> pd.DataFrame:
    df_query = _app_attr("df_query")
    if not callable(df_query) or not ids:
        return pd.DataFrame()
    placeholders = ",".join("?" for _ in ids)
    return df_query(
        f"""
        SELECT t.id, t.job_id, t.job_stage_id, t.employee_id, t.work_date,
               t.start_time, t.finish_time, t.break_minutes, t.total_hours,
               t.work_type, t.notes, t.site_location,
               COALESCE(t.status, 'Submitted') AS status,
               e.name AS employee_name,
               j.job_no, j.job_name
        FROM timesheet_entries t
        JOIN employees e ON e.id = t.employee_id
        JOIN jobs j ON j.id = t.job_id
        WHERE t.id IN ({placeholders})
        ORDER BY t.work_date, e.name, t.id
        """,
        tuple(ids),
    )


def _render_bulk_reassign(st: Any, edited_frame: pd.DataFrame, key: str) -> None:
    if not _is_admin():
        return

    ids = _selected_ids(edited_frame)
    if not ids:
        return

    get_job_options = _app_attr("get_job_options")
    get_job_stage_options = _app_attr("get_job_stage_options")
    update_timesheet_entry = _app_attr("update_timesheet_entry")
    if not all(callable(fn) for fn in (get_job_options, get_job_stage_options, update_timesheet_entry)):
        return

    rows = _load_selected_timesheets(ids)
    if rows.empty:
        return

    job_options = get_job_options()
    if not job_options:
        return
    stage_options = get_job_stage_options(job_options)
    if not stage_options:
        return

    with st.container(border=True):
        st.markdown("### Change Job for Selected Timesheets")
        st.caption(
            "Choose the correct job once. Every selected employee/date stays unchanged; "
            "only the Job / Stage is reassigned. Approved or paid wage postings are updated too."
        )

        target_label = st.selectbox(
            "New Job / Stage",
            list(stage_options.keys()),
            key=f"{key}_bulk_reassign_target",
        )
        target = stage_options[target_label]

        review = rows[[
            "id", "employee_name", "work_date", "job_no", "job_name",
            "total_hours", "status",
        ]].copy()
        review.columns = ["ID", "Employee", "Date", "Current Job No", "Current Job", "Hours", "Status"]
        st.dataframe(review, width="stretch", hide_index=True)

        accepted = st.checkbox(
            f"I have reviewed these {len(ids)} timesheets and want to move all of them to {target_label}.",
            key=f"{key}_bulk_reassign_confirm",
        )

        if st.button(
            f"Change job on {len(ids)} selected timesheet(s)",
            key=f"{key}_bulk_reassign_button",
            type="primary",
            disabled=not accepted,
        ):
            changed = 0
            failures: list[str] = []
            for _, row in rows.iterrows():
                try:
                    update_timesheet_entry(
                        int(row["id"]),
                        int(target["job_id"]),
                        int(row["employee_id"]),
                        str(row["work_date"]),
                        str(row["start_time"]),
                        str(row["finish_time"]),
                        int(row["break_minutes"] or 0),
                        float(row["total_hours"] or 0),
                        str(row["work_type"] or ""),
                        str(row["notes"] or ""),
                        target.get("job_stage_id"),
                        row["site_location"],
                    )
                    changed += 1
                except Exception as exc:
                    failures.append(f"#{int(row['id'])}: {exc}")

            pb_success = _app_attr("pb_success", st.success)
            pb_error = _app_attr("pb_error", st.error)
            refresh = _app_attr("refresh", None) or _app_attr("pb_rerun", None)
            if changed:
                pb_success(
                    f"Changed the job on {changed} selected timesheet(s) to {target_label}."
                )
            if failures:
                pb_error("Some timesheets could not be changed: " + "; ".join(failures))
            if callable(refresh):
                refresh()


def install() -> bool:
    """Patch Streamlit's data_editor once, before pb_jobhub_app is imported."""
    st = sys.modules.get("streamlit")
    if st is None:
        try:
            st = importlib.import_module("streamlit")
        except Exception:
            return False

    original = getattr(st, "data_editor", None)
    if original is None or getattr(original, _PATCH_MARKER, False):
        return False

    def pb_timesheet_bulk_data_editor(*args: Any, **kwargs: Any):
        result = original(*args, **kwargs)
        frame = None
        if args and isinstance(args[0], pd.DataFrame):
            frame = args[0]
        elif isinstance(kwargs.get("data"), pd.DataFrame):
            frame = kwargs.get("data")
        key = str(kwargs.get("key") or "")
        if _is_timesheet_selector(key, frame) and isinstance(result, pd.DataFrame):
            try:
                _render_bulk_reassign(st, result, key)
            except Exception as exc:
                st.caption(f"Bulk timesheet job change is temporarily unavailable: {exc}")
        return result

    setattr(pb_timesheet_bulk_data_editor, _PATCH_MARKER, True)
    pb_timesheet_bulk_data_editor._pb_original_data_editor = original
    st.data_editor = pb_timesheet_bulk_data_editor
    return True
