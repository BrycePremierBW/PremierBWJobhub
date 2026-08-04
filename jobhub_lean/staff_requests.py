from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import streamlit as st

from .common import AppContext, _clean, _int, employee_options, job_options
from .compat import create_management_notifications
from .ui import header, rerun_success, selected_row


REQUEST_TYPES = (
    "Stage progress update",
    "Timesheet correction",
    "Photo / evidence",
    "Material order",
    "Equipment check",
    "Safety / compliance",
    "Other",
)
PRIORITIES = ("Low", "Normal", "High", "Urgent")
STATUSES = ("Requested", "In Progress", "Completed", "Cancelled")


def _stage_options(ctx: AppContext, job_id: int | None) -> dict[str, int | None]:
    options: dict[str, int | None] = {"Whole job / no stage": None}
    if not job_id:
        return options
    frame = ctx.db.query(
        "SELECT id,stage_name FROM job_stages WHERE job_id=? ORDER BY sequence_order,id",
        (int(job_id),),
    )
    for _, row in frame.iterrows():
        options[_clean(row.get("stage_name"))] = _int(row.get("id"))
    return options


def staff_requests_page(ctx: AppContext) -> None:
    header("Staff Requests", "Assign work, evidence and follow-up requests to individual employees.")
    employees = employee_options(ctx, active_only=False)
    jobs = job_options(ctx, include_archived=False)
    if not employees:
        st.info("Add an employee before creating staff requests.")
        return

    filters = st.columns(3)
    employee_filter = filters[0].selectbox("Employee filter", ["All"] + list(employees), key="staff_request_employee_filter")
    status_filter = filters[1].selectbox("Status filter", ["All"] + list(STATUSES), key="staff_request_status_filter")
    job_filter = filters[2].selectbox("Job filter", ["All"] + list(jobs), key="staff_request_job_filter") if jobs else "All"

    conditions: list[str] = []
    params: list[Any] = []
    if employee_filter != "All":
        conditions.append("r.employee_id=?")
        params.append(employees[employee_filter])
    if status_filter != "All":
        conditions.append("LOWER(COALESCE(r.status,''))=LOWER(?)")
        params.append(status_filter)
    if job_filter != "All":
        conditions.append("r.job_id=?")
        params.append(jobs[job_filter])
    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    frame = ctx.db.query(
        f"""
        SELECT r.id,COALESCE(e.name,'') AS "Employee",COALESCE(j.job_no,'') AS "Job",
               COALESCE(s.stage_name,'') AS "Stage",r.request_type AS "Type",r.title AS "Title",
               COALESCE(r.priority,'Normal') AS "Priority",COALESCE(r.due_at,'') AS "Due",
               COALESCE(r.status,'Requested') AS "Status",COALESCE(r.instructions,'') AS "Instructions",
               COALESCE(r.response_notes,'') AS "Response"
        FROM staff_requests r
        LEFT JOIN employees e ON e.id=r.employee_id
        LEFT JOIN jobs j ON j.id=r.job_id
        LEFT JOIN job_stages s ON s.id=r.job_stage_id
        {where}
        ORDER BY CASE LOWER(COALESCE(r.status,'')) WHEN 'requested' THEN 0 WHEN 'in progress' THEN 1 ELSE 2 END,
                 CASE LOWER(COALESCE(r.priority,'')) WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 ELSE 2 END,
                 r.due_at,r.id DESC
        """,
        params,
    )
    row = selected_row(frame, key="staff_requests_table")
    if row:
        st.session_state["lean_selected_staff_request_id"] = _int(row.get("id"))
    selected_id = _int(st.session_state.get("lean_selected_staff_request_id"))

    with st.expander("Create staff request", expanded=frame.empty):
        with st.form("staff_request_create"):
            employee_label = st.selectbox("Employee", list(employees))
            job_labels = ["No job"] + list(jobs)
            job_label = st.selectbox("Job", job_labels)
            selected_job_id = jobs.get(job_label)
            stages = _stage_options(ctx, selected_job_id)
            stage_label = st.selectbox("Stage", list(stages))
            c1, c2 = st.columns(2)
            request_type = c1.selectbox("Request type", list(REQUEST_TYPES))
            priority = c2.selectbox("Priority", list(PRIORITIES), index=1)
            title = st.text_input("Title")
            instructions = st.text_area("Instructions")
            due_date = st.date_input("Due date", value=datetime.now().date() + timedelta(days=1))
            create = st.form_submit_button("Send request", type="primary")
        if create:
            if not title.strip():
                st.error("Enter a request title.")
            else:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                request_id = ctx.db.insert_id(
                    """
                    INSERT INTO staff_requests
                    (requested_by_user_id,employee_id,job_id,job_stage_id,request_type,title,instructions,
                     priority,due_at,status,requested_at)
                    VALUES (?,?,?,?,?,?,?,?,?,'Requested',?)
                    """,
                    (
                        _int(ctx.user.get("id")) or None,
                        employees[employee_label],
                        selected_job_id,
                        stages[stage_label],
                        request_type,
                        title.strip(),
                        instructions.strip(),
                        priority,
                        f"{due_date.isoformat()} 17:00:00",
                        now,
                    ),
                )
                linked_user = ctx.db.query(
                    "SELECT id FROM app_users WHERE employee_id=? AND COALESCE(active,1)=1 ORDER BY id LIMIT 1",
                    (employees[employee_label],),
                )
                if not linked_user.empty:
                    ctx.db.execute(
                        """
                        INSERT INTO app_notifications
                        (recipient_user_id,event_type,title,message,job_id,entity_type,entity_id,created_by,created_at,read_at)
                        VALUES (?,?,?,?,?,'staff_request',?,?,?,'')
                        """,
                        (
                            _int(linked_user.iloc[0].get("id")),
                            "staff_request",
                            title.strip(),
                            instructions.strip(),
                            selected_job_id,
                            str(request_id),
                            _clean(ctx.user.get("username")),
                            now,
                        ),
                    )
                ctx.audit("create", "staff_requests", request_id, title.strip())
                rerun_success("Staff request sent.")

    if selected_id:
        detail = ctx.db.query("SELECT * FROM staff_requests WHERE id=?", (selected_id,))
        if detail.empty:
            st.session_state.pop("lean_selected_staff_request_id", None)
            st.rerun()
        item = detail.iloc[0].to_dict()
        statuses = list(STATUSES)
        current_status = _clean(item.get("status")) or "Requested"
        if current_status not in statuses:
            statuses.append(current_status)
        with st.expander("Update selected request", expanded=True):
            with st.form(f"staff_request_edit_{selected_id}"):
                status = st.selectbox("Status", statuses, index=statuses.index(current_status))
                response = st.text_area("Management notes / response", value=_clean(item.get("response_notes")))
                update = st.form_submit_button("Update request", type="primary")
            if update:
                completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if status == "Completed" else None
                completed_by = _clean(ctx.user.get("username")) if status == "Completed" else None
                ctx.db.execute(
                    "UPDATE staff_requests SET status=?,response_notes=?,completed_at=?,completed_by=? WHERE id=?",
                    (status, response.strip(), completed_at, completed_by, selected_id),
                )
                ctx.audit("update", "staff_requests", selected_id, status)
                rerun_success("Staff request updated.")


def employee_portal_page(ctx: AppContext) -> None:
    header("Employee Portal", "Your requests, schedule and recent timesheets.")
    employee_id = _int(ctx.user.get("employee_id"))
    if not employee_id:
        st.warning("Your JobHub account is not linked to an employee record.")
        return

    st.subheader("My Requests")
    requests = ctx.db.query(
        """
        SELECT r.id,COALESCE(j.job_no,'') AS "Job",COALESCE(s.stage_name,'') AS "Stage",
               r.title AS "Title",COALESCE(r.instructions,'') AS "Instructions",
               COALESCE(r.priority,'Normal') AS "Priority",COALESCE(r.due_at,'') AS "Due",
               COALESCE(r.status,'Requested') AS "Status",COALESCE(r.response_notes,'') AS "Response"
        FROM staff_requests r
        LEFT JOIN jobs j ON j.id=r.job_id
        LEFT JOIN job_stages s ON s.id=r.job_stage_id
        WHERE r.employee_id=?
        ORDER BY CASE LOWER(COALESCE(r.status,'')) WHEN 'requested' THEN 0 WHEN 'in progress' THEN 1 ELSE 2 END,
                 r.due_at,r.id DESC
        """,
        (employee_id,),
    )
    selected = selected_row(requests, key=f"employee_requests_{employee_id}")
    if selected:
        request_id = _int(selected.get("id"))
        current_status = _clean(selected.get("Status")) or "Requested"
        with st.expander("Respond to selected request", expanded=True):
            with st.form(f"employee_request_response_{request_id}"):
                status_options = ["Requested", "In Progress", "Completed"]
                if current_status not in status_options:
                    status_options.append(current_status)
                status = st.selectbox("Status", status_options, index=status_options.index(current_status))
                response = st.text_area("Response / completion notes", value=_clean(selected.get("Response")))
                save = st.form_submit_button("Save response", type="primary")
            if save:
                completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if status == "Completed" else None
                ctx.db.execute(
                    """
                    UPDATE staff_requests
                    SET status=?,response_notes=?,completed_at=?,completed_by=?
                    WHERE id=? AND employee_id=?
                    """,
                    (status, response.strip(), completed_at, _clean(ctx.user.get("username")), request_id, employee_id),
                )
                create_management_notifications(
                    ctx,
                    event_type="staff_request_response",
                    title=f"Staff request updated: {_clean(selected.get('Title'))}",
                    message=f"{_clean(ctx.user.get('employee_name') or ctx.user.get('username'))}: {status}. {response.strip()}",
                    job_id=None,
                    entity_type="staff_request",
                    entity_id=request_id,
                )
                ctx.audit("respond", "staff_requests", request_id, status)
                rerun_success("Request response saved.")

    schedule_tab, timesheet_tab = st.tabs(["My Schedule", "My Timesheets"])
    with schedule_tab:
        if ctx.db.table_exists("staff_schedule"):
            schedule = ctx.db.query(
                """
                SELECT s.schedule_date AS "Date",COALESCE(j.job_no,'') AS "Job",
                       COALESCE(j.job_name,'') AS "Job Name",COALESCE(js.stage_name,'') AS "Stage",
                       COALESCE(s.start_time,'') AS "Start",COALESCE(s.finish_time,'') AS "Finish",
                       COALESCE(s.site_role,'') AS "Role",COALESCE(s.notes,'') AS "Notes"
                FROM staff_schedule s
                LEFT JOIN jobs j ON j.id=s.job_id
                LEFT JOIN job_stages js ON js.id=s.job_stage_id
                WHERE s.employee_id=? AND s.schedule_date>=?
                ORDER BY s.schedule_date,s.start_time LIMIT 60
                """,
                (employee_id, datetime.now().date().isoformat()),
            )
            st.dataframe(schedule, hide_index=True, use_container_width=True)
        else:
            st.info("The scheduler has not been initialised yet.")
    with timesheet_tab:
        timesheets = ctx.db.query(
            """
            SELECT t.work_date AS "Date",COALESCE(j.job_no,'') AS "Job",
                   COALESCE(t.work_type,'') AS "Area / Work Type",COALESCE(t.total_hours,0) AS "Hours",
                   COALESCE(t.status,'') AS "Status",COALESCE(t.notes,'') AS "Notes"
            FROM timesheet_entries t LEFT JOIN jobs j ON j.id=t.job_id
            WHERE t.employee_id=? ORDER BY t.work_date DESC,t.id DESC LIMIT 60
            """,
            (employee_id,),
        )
        st.dataframe(timesheets, hide_index=True, use_container_width=True)
