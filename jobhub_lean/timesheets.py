from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

import streamlit as st

from .auth import can_manage
from .common import AppContext, _clean, _date_text, _date_value, _float, _int, _time_value, employee_options, job_options, shift_hours
from .ui import header, rerun_success, selected_row


def _sync_wage_from_timesheet(ctx: AppContext, timesheet_id: int) -> None:
    row = ctx.db.query(
        """
        SELECT t.job_id,t.employee_id,t.work_date,t.total_hours,
               COALESCE(NULLIF(e.rate_plus_10,0),e.base_hourly_rate,0) AS hourly_rate
        FROM timesheet_entries t JOIN employees e ON e.id=t.employee_id
        WHERE t.id=?
        """,
        (timesheet_id,),
    )
    if row.empty:
        return
    item = row.iloc[0]
    values = (
        _int(item.get("job_id")), _int(item.get("employee_id")),
        _date_text(item.get("work_date")), _float(item.get("total_hours")),
        f"Timesheet #{timesheet_id}", _float(item.get("hourly_rate")), timesheet_id,
    )
    existing = _int(ctx.db.scalar("SELECT id FROM wage_entries WHERE timesheet_id=? ORDER BY id LIMIT 1", (timesheet_id,), 0))
    if existing:
        ctx.db.execute(
            """
            UPDATE wage_entries SET job_id=?,employee_id=?,work_date=?,hours=?,notes=?,
                hourly_rate_snapshot=?,source='Timesheet' WHERE id=?
            """,
            (*values[:-1], existing),
        )
    else:
        ctx.db.execute(
            """
            INSERT INTO wage_entries
            (job_id,employee_id,work_date,hours,notes,hourly_rate_snapshot,timesheet_id,source)
            VALUES (?,?,?,?,?,?,?,'Timesheet')
            """,
            values,
        )


def timesheets_page(ctx: AppContext) -> None:
    header("Timesheets", "Fast entry and approval without global dataframe reruns.")
    jobs = job_options(ctx)
    employees = employee_options(ctx, active_only=False)
    if not jobs or not employees:
        st.info("Add at least one job and employee first.")
        return
    job_labels = list(jobs)
    employee_labels = list(employees)
    c1, c2, c3 = st.columns(3)
    filter_job = c1.selectbox("Job filter", ["All"] + job_labels)
    filter_employee = c2.selectbox("Employee filter", ["All"] + employee_labels)
    filter_status = c3.selectbox("Status filter", ["All", "Submitted", "Approved", "Rejected"])
    where = []
    params: list[Any] = []
    if filter_job != "All":
        where.append("t.job_id=?"); params.append(jobs[filter_job])
    if filter_employee != "All":
        where.append("t.employee_id=?"); params.append(employees[filter_employee])
    if filter_status != "All":
        where.append("LOWER(COALESCE(t.status,''))=LOWER(?)"); params.append(filter_status)
    clause = "WHERE " + " AND ".join(where) if where else ""
    frame = ctx.db.query(
        f"""
        SELECT t.id,t.work_date AS "Date",COALESCE(e.name,'') AS "Employee",
               COALESCE(j.job_no,'') AS "Job",COALESCE(j.job_name,'') AS "Job Name",
               COALESCE(t.work_type,'') AS "Area / Work Type",
               COALESCE(t.start_time,'') AS "Start",COALESCE(t.finish_time,'') AS "Finish",
               COALESCE(t.break_minutes,0) AS "Break Minutes",COALESCE(t.total_hours,0) AS "Hours",
               COALESCE(t.status,'') AS "Status",COALESCE(t.notes,'') AS "Notes"
        FROM timesheet_entries t
        LEFT JOIN employees e ON e.id=t.employee_id
        LEFT JOIN jobs j ON j.id=t.job_id
        {clause}
        ORDER BY t.work_date DESC,t.id DESC LIMIT 1000
        """,
        params,
    )
    row = selected_row(frame, key="timesheets_table")
    if row:
        st.session_state["lean_selected_timesheet_id"] = _int(row.get("id"))
    selected_id = _int(st.session_state.get("lean_selected_timesheet_id"))

    with st.expander("Add timesheet", expanded=frame.empty):
        with st.form("timesheet_add"):
            c1, c2, c3 = st.columns(3)
            job_label = c1.selectbox("Job", job_labels)
            employee_label = c2.selectbox("Employee", employee_labels)
            work_date = c3.date_input("Date", value=date.today())
            c4, c5, c6 = st.columns(3)
            start = c4.time_input("Start", value=time(7, 0))
            finish = c5.time_input("Finish", value=time(15, 0))
            break_hours = c6.number_input("Unpaid break hours", min_value=0.0, max_value=8.0, value=0.0, step=0.25)
            c7, c8 = st.columns(2)
            area = c7.text_input("Area / stage")
            work_type = c8.text_input("Work type")
            notes = st.text_area("Notes")
            submit = st.form_submit_button("Submit timesheet", type="primary")
        if submit:
            hours = shift_hours(start, finish, break_hours)
            now = datetime.now().isoformat(timespec="seconds")
            timesheet_id = ctx.db.insert_id(
                """
                INSERT INTO timesheet_entries
                (job_id,employee_id,work_date,start_time,finish_time,break_minutes,total_hours,work_type,status,notes,submitted_by,submitted_at)
                VALUES (?,?,?,?,?,?,?,?,'Submitted',?,?,?)
                """,
                (jobs[job_label], employees[employee_label], work_date.isoformat(), start.strftime("%H:%M"), finish.strftime("%H:%M"), break_hours * 60, hours, " / ".join(part for part in (area.strip(), work_type.strip()) if part), notes.strip(), ctx.user.get("username", ""), now),
            )
            _sync_wage_from_timesheet(ctx, timesheet_id)
            ctx.audit("create", "timesheet_entries", timesheet_id, f"{hours:.2f} hours")
            rerun_success(f"Timesheet submitted: {hours:.2f} hours.")

    if selected_id:
        detail = ctx.db.query("SELECT * FROM timesheet_entries WHERE id=?", (selected_id,))
        if detail.empty:
            st.session_state.pop("lean_selected_timesheet_id", None); st.rerun()
        item = detail.iloc[0].to_dict()
        current_job = next((label for label, value in jobs.items() if value == _int(item.get("job_id"))), job_labels[0])
        current_employee = next((label for label, value in employees.items() if value == _int(item.get("employee_id"))), employee_labels[0])
        with st.expander("Edit selected timesheet", expanded=True):
            with st.form(f"timesheet_edit_{selected_id}"):
                c1, c2, c3 = st.columns(3)
                job_label = c1.selectbox("Job", job_labels, index=job_labels.index(current_job))
                employee_label = c2.selectbox("Employee", employee_labels, index=employee_labels.index(current_employee))
                work_date = c3.date_input("Date", value=_date_value(item.get("work_date")))
                c4, c5, c6 = st.columns(3)
                start = c4.time_input("Start", value=_time_value(item.get("start_time")))
                finish = c5.time_input("Finish", value=_time_value(item.get("finish_time"), time(15, 0)))
                break_hours = c6.number_input("Unpaid break hours", min_value=0.0, max_value=8.0, value=_float(item.get("break_minutes")) / 60, step=0.25)
                c7, c8 = st.columns(2)
                area = c7.text_input("Area / stage", value="")
                work_type = c8.text_input("Work type", value=_clean(item.get("work_type")))
                statuses = ["Submitted", "Approved", "Rejected"] if can_manage() else [_clean(item.get("status")) or "Submitted"]
                current_status = _clean(item.get("status")) or statuses[0]
                if current_status not in statuses:
                    statuses.append(current_status)
                status = st.selectbox("Status", statuses, index=statuses.index(current_status))
                notes = st.text_area("Notes", value=_clean(item.get("notes")))
                update = st.form_submit_button("Update timesheet", type="primary")
            if update:
                hours = shift_hours(start, finish, break_hours)
                ctx.db.execute(
                    """
                    UPDATE timesheet_entries SET job_id=?,employee_id=?,work_date=?,start_time=?,finish_time=?,
                        break_minutes=?,total_hours=?,work_type=?,status=?,notes=?,approved_by=?,approved_at=? WHERE id=?
                    """,
                    (jobs[job_label], employees[employee_label], work_date.isoformat(), start.strftime("%H:%M"), finish.strftime("%H:%M"), break_hours * 60, hours, " / ".join(part for part in (area.strip(), work_type.strip()) if part), status, notes.strip(), ctx.user.get("username", "") if status == "Approved" else None, datetime.now().isoformat(timespec="seconds") if status == "Approved" else None, selected_id),
                )
                _sync_wage_from_timesheet(ctx, selected_id)
                ctx.audit("update", "timesheet_entries", selected_id, f"{hours:.2f} hours / {status}")
                rerun_success("Timesheet updated.")
            if can_manage():
                confirm = st.checkbox("Delete this timesheet", key=f"delete_timesheet_confirm_{selected_id}")
                if st.button("Delete", disabled=not confirm, key=f"delete_timesheet_{selected_id}"):
                    ctx.db.execute("DELETE FROM wage_entries WHERE timesheet_id=?", (selected_id,))
                    ctx.db.execute("DELETE FROM timesheet_entries WHERE id=?", (selected_id,))
                    ctx.audit("delete", "timesheet_entries", selected_id)
                    st.session_state.pop("lean_selected_timesheet_id", None)
                    rerun_success("Timesheet deleted.")
