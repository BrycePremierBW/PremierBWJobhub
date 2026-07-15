"""Visual staff scheduler embedded inside Premier Brushworks JobHub.

This module is rendered by JobHub's existing Staff Scheduling Board. It uses
JobHub's own database helpers, so jobs, employees and schedule rows live in one
application and one database.
"""
from __future__ import annotations

import html
import os
import sqlite3
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd
import streamlit as st


PALETTE = [
    "#dbeafe", "#dcfce7", "#fef3c7", "#fce7f3", "#ede9fe",
    "#cffafe", "#ffedd5", "#e2e8f0", "#fae8ff", "#ccfbf1",
]
BORDER_PALETTE = [
    "#2563eb", "#16a34a", "#d97706", "#db2777", "#7c3aed",
    "#0891b2", "#ea580c", "#475569", "#c026d3", "#0f766e",
]


def _safe_date(value, fallback: date | None = None) -> date:
    if value is None or str(value).strip() == "" or pd.isna(value):
        return fallback or date.today()
    try:
        return pd.to_datetime(value).date()
    except Exception:
        return fallback or date.today()


def _time_hours(start_value: str, finish_value: str, fallback: float = 7.6) -> float:
    try:
        start_dt = datetime.strptime(str(start_value or "07:00")[:5], "%H:%M")
        finish_dt = datetime.strptime(str(finish_value or "15:00")[:5], "%H:%M")
        hours = (finish_dt - start_dt).total_seconds() / 3600
        return round(hours if hours > 0 else fallback, 2)
    except Exception:
        return fallback


def _table_columns(df_query: Callable, table: str, use_postgres: bool) -> set[str]:
    if use_postgres:
        frame = df_query(
            "SELECT column_name AS name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=?",
            (table,),
        )
        return set(frame["name"].astype(str)) if not frame.empty else set()
    frame = df_query(f"PRAGMA table_info({table})")
    if frame.empty:
        return set()
    name_column = "name" if "name" in frame.columns else frame.columns[1]
    return set(frame[name_column].astype(str))


def ensure_scheduler_schema(*, df_query: Callable, execute: Callable, use_postgres: bool) -> None:
    """Create scheduler support tables and safely add missing audit columns."""
    execute(
        """
        CREATE TABLE IF NOT EXISTS staff_leave_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            leave_type TEXT NOT NULL DEFAULT 'Annual Leave',
            status TEXT NOT NULL DEFAULT 'Pending',
            reason TEXT DEFAULT '',
            reviewed_by TEXT DEFAULT '',
            reviewed_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(employee_id) REFERENCES employees(id)
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS scheduler_import_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT,
            jobs_added INTEGER DEFAULT 0,
            employees_added INTEGER DEFAULT 0,
            assignments_added INTEGER DEFAULT 0,
            leave_added INTEGER DEFAULT 0,
            imported_by TEXT,
            imported_at TEXT,
            notes TEXT
        )
        """
    )

    existing = _table_columns(df_query, "staff_schedule", use_postgres)
    additions = {
        "period_type": "TEXT",
        "period_start": "TEXT",
        "period_end": "TEXT",
        "planned_hours": "REAL",
        "created_by": "TEXT",
        "source_app": "TEXT DEFAULT 'JobHub'",
        "updated_at": "TEXT",
    }
    for column, definition in additions.items():
        if column not in existing:
            if use_postgres:
                execute(f"ALTER TABLE staff_schedule ADD COLUMN IF NOT EXISTS {column} {definition}")
            else:
                execute(f"ALTER TABLE staff_schedule ADD COLUMN {column} {definition}")


def _first_value(*values):
    for value in values:
        if value is None:
            continue
        try:
            if pd.isna(value):
                continue
        except Exception:
            pass
        if str(value).strip():
            return value
    return None


def _expanded_schedule(schedule: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    columns = [
        "schedule_id", "work_date", "employee_id", "employee", "employee_role",
        "job_id", "job_no", "job_name", "site_address", "job_status",
        "start_time", "finish_time", "site_role", "notes", "display_hours",
        "source_app", "created_by",
    ]
    if schedule.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict] = []
    for _, item in schedule.iterrows():
        row_start = _safe_date(_first_value(item.get("period_start"), item.get("schedule_date")), start)
        row_end = _safe_date(_first_value(item.get("period_end"), item.get("schedule_date")), row_start)
        row_start = max(row_start, start)
        row_end = min(row_end, end)
        if row_end < row_start:
            continue

        dates: list[date] = []
        current = row_start
        while current <= row_end:
            is_single = row_start == row_end
            if is_single or current.weekday() < 5:
                dates.append(current)
            current += timedelta(days=1)
        if not dates:
            dates = [row_start]

        planned = float(item.get("planned_hours") or 0)
        if planned > 0 and len(dates) > 1:
            hours = round(planned / len(dates), 2)
        elif planned > 0:
            hours = planned
        else:
            hours = _time_hours(item.get("start_time"), item.get("finish_time"))

        for work_day in dates:
            rows.append(
                {
                    "schedule_id": int(item["schedule_id"]),
                    "work_date": work_day,
                    "employee_id": int(item["employee_id"]),
                    "employee": str(item.get("employee") or ""),
                    "employee_role": str(item.get("employee_role") or ""),
                    "job_id": int(item["job_id"]),
                    "job_no": str(item.get("job_no") or ""),
                    "job_name": str(item.get("job_name") or ""),
                    "site_address": str(item.get("site_address") or ""),
                    "job_status": str(item.get("job_status") or ""),
                    "start_time": str(item.get("start_time") or ""),
                    "finish_time": str(item.get("finish_time") or ""),
                    "site_role": str(item.get("site_role") or ""),
                    "notes": str(item.get("notes") or ""),
                    "display_hours": hours,
                    "source_app": str(item.get("source_app") or "JobHub"),
                    "created_by": str(item.get("created_by") or ""),
                }
            )
    return pd.DataFrame(rows, columns=columns)


def _legacy_tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row[0]) for row in rows}


def inspect_legacy_scheduler_db(source_path: str | os.PathLike) -> dict:
    """Return table counts for a standalone PB Staff Scheduler SQLite backup."""
    with sqlite3.connect(str(source_path)) as conn:
        tables = _legacy_tables(conn)
        if not {"staff", "jobs", "assignments"}.issubset(tables):
            raise ValueError(
                "This is not a standalone PB Staff Scheduler backup. "
                "Expected staff, jobs and assignments tables."
            )
        counts = {}
        for table in ["staff", "jobs", "assignments", "leave_requests"]:
            counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) if table in tables else 0
        return counts


def migrate_legacy_scheduler_db(
    source_path: str | os.PathLike,
    *,
    connect: Callable,
    imported_by: str = "JobHub migration",
    source_name: str = "PB Staff Scheduler backup",
) -> dict:
    """Migrate standalone scheduler jobs, staff, assignments and leave into JobHub.

    Existing JobHub jobs are matched by job number, employees by name, and
    assignments by job/staff/date/time. Re-running the import is therefore safe.
    """
    stats = {
        "jobs_added": 0,
        "employees_added": 0,
        "assignments_added": 0,
        "leave_added": 0,
        "jobs_matched": 0,
        "employees_matched": 0,
        "assignments_skipped": 0,
        "leave_skipped": 0,
    }

    with sqlite3.connect(str(source_path)) as legacy:
        legacy.row_factory = sqlite3.Row
        tables = _legacy_tables(legacy)
        if not {"staff", "jobs", "assignments"}.issubset(tables):
            raise ValueError("The uploaded database is not a PB Staff Scheduler backup.")
        legacy_staff = legacy.execute("SELECT * FROM staff ORDER BY id").fetchall()
        legacy_jobs = legacy.execute("SELECT * FROM jobs ORDER BY id").fetchall()
        legacy_assignments = legacy.execute("SELECT * FROM assignments ORDER BY id").fetchall()
        legacy_leave = legacy.execute("SELECT * FROM leave_requests ORDER BY id").fetchall() if "leave_requests" in tables else []

    target = connect()
    try:
        cur = target.cursor()
        employee_map: dict[int, int] = {}
        job_map: dict[int, int] = {}

        for row in legacy_staff:
            name = str(row["name"] or "").strip()
            if not name:
                continue
            cur.execute("SELECT id FROM employees WHERE LOWER(name)=LOWER(?)", (name,))
            existing = cur.fetchone()
            if existing:
                employee_id = int(existing[0])
                stats["employees_matched"] += 1
            else:
                status = "Active" if int(row["active"] or 0) else "Inactive"
                cur.execute(
                    """
                    INSERT INTO employees
                    (name, role, phone, base_hourly_rate, rate_plus_10, status, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        str(row["position"] or "Painter"),
                        str(row["phone"] or ""),
                        0.0,
                        0.0,
                        status,
                        str(row["notes"] or ""),
                    ),
                )
                cur.execute("SELECT id FROM employees WHERE LOWER(name)=LOWER(?)", (name,))
                employee_id = int(cur.fetchone()[0])
                stats["employees_added"] += 1
            employee_map[int(row["id"])] = employee_id

        for row in legacy_jobs:
            job_no = str(row["job_no"] or "").strip()
            if not job_no:
                continue
            cur.execute("SELECT id FROM jobs WHERE job_no=?", (job_no,))
            existing = cur.fetchone()
            if existing:
                job_id = int(existing[0])
                stats["jobs_matched"] += 1
            else:
                builder_name = str(row["builder"] or "").strip()
                builder_id = None
                if builder_name:
                    cur.execute("SELECT id FROM builders_clients WHERE LOWER(name)=LOWER(?)", (builder_name,))
                    builder_row = cur.fetchone()
                    if not builder_row:
                        cur.execute(
                            """
                            INSERT INTO builders_clients
                            (type, name, contact_name, phone, email, address, qbcc, abn, terms, notes)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            ("Builder / Client", builder_name, "", "", "", "", "", "", "", "Imported from Staff Scheduler"),
                        )
                        cur.execute("SELECT id FROM builders_clients WHERE LOWER(name)=LOWER(?)", (builder_name,))
                        builder_row = cur.fetchone()
                    builder_id = int(builder_row[0]) if builder_row else None

                cur.execute(
                    """
                    INSERT INTO jobs
                    (job_no, job_name, builder_client_id, site_address, status,
                     leading_hand, start_date, end_date, contract_value, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_no,
                        str(row["job_name"] or ""),
                        builder_id,
                        str(row["address"] or ""),
                        str(row["status"] or "Upcoming"),
                        str(row["leading_hand"] or ""),
                        str(row["start_date"] or ""),
                        str(row["end_date"] or ""),
                        0.0,
                        str(row["notes"] or ""),
                    ),
                )
                cur.execute("SELECT id FROM jobs WHERE job_no=?", (job_no,))
                job_id = int(cur.fetchone()[0])
                stats["jobs_added"] += 1
            job_map[int(row["id"])] = job_id

        for row in legacy_assignments:
            employee_id = employee_map.get(int(row["staff_id"]))
            job_id = job_map.get(int(row["job_id"]))
            if not employee_id or not job_id:
                continue
            work_date = str(row["work_date"] or "")
            start_time = str(row["start_time"] or "07:00")
            finish_time = str(row["end_time"] or "15:00")
            cur.execute(
                """
                SELECT COUNT(*) FROM staff_schedule
                WHERE job_id=? AND employee_id=? AND schedule_date=?
                  AND COALESCE(start_time,'')=? AND COALESCE(finish_time,'')=?
                """,
                (job_id, employee_id, work_date, start_time, finish_time),
            )
            if int(cur.fetchone()[0]) > 0:
                stats["assignments_skipped"] += 1
                continue
            cur.execute(
                """
                INSERT INTO staff_schedule
                (job_id, employee_id, schedule_date, start_time, finish_time,
                 site_role, notes, created_at, period_type, period_start,
                 period_end, planned_hours, created_by, source_app, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    employee_id,
                    work_date,
                    start_time,
                    finish_time,
                    str(row["assignment_type"] or "Site Work"),
                    str(row["notes"] or ""),
                    str(row["created_at"] or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    "Single Day",
                    work_date,
                    work_date,
                    float(row["hours"] or _time_hours(start_time, finish_time)),
                    str(row["created_by"] or imported_by),
                    "Legacy Staff Scheduler",
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            stats["assignments_added"] += 1

        for row in legacy_leave:
            employee_id = employee_map.get(int(row["staff_id"]))
            if not employee_id:
                continue
            start_date = str(row["start_date"] or "")
            end_date = str(row["end_date"] or "")
            leave_type = str(row["leave_type"] or "Annual Leave")
            cur.execute(
                """
                SELECT COUNT(*) FROM staff_leave_requests
                WHERE employee_id=? AND start_date=? AND end_date=? AND leave_type=?
                """,
                (employee_id, start_date, end_date, leave_type),
            )
            if int(cur.fetchone()[0]) > 0:
                stats["leave_skipped"] += 1
                continue
            cur.execute(
                """
                INSERT INTO staff_leave_requests
                (employee_id, start_date, end_date, leave_type, status, reason,
                 reviewed_by, reviewed_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    employee_id,
                    start_date,
                    end_date,
                    leave_type,
                    str(row["status"] or "Approved"),
                    str(row["notes"] or ""),
                    imported_by,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    str(row["created_at"] or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                ),
            )
            stats["leave_added"] += 1

        cur.execute(
            """
            INSERT INTO scheduler_import_log
            (source_name, jobs_added, employees_added, assignments_added,
             leave_added, imported_by, imported_at, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_name,
                stats["jobs_added"],
                stats["employees_added"],
                stats["assignments_added"],
                stats["leave_added"],
                imported_by,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Idempotent import: existing matching records were skipped.",
            ),
        )
        target.commit()
    except Exception:
        try:
            target.rollback()
        except Exception:
            pass
        raise
    finally:
        target.close()
    return stats


def _job_colour_map(jobs: pd.DataFrame) -> dict[int, tuple[str, str]]:
    colours = {}
    if jobs.empty:
        return colours
    for index, (_, row) in enumerate(jobs.sort_values("job_no").iterrows()):
        colours[int(row["job_id"])] = (
            PALETTE[index % len(PALETTE)],
            BORDER_PALETTE[index % len(BORDER_PALETTE)],
        )
    return colours


def _render_matrix(expanded: pd.DataFrame, employees: pd.DataFrame, start: date, end: date, colours: dict) -> None:
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)

    styles = """
    <style>
    .pb-sched-wrap{overflow-x:auto;border:1px solid #e5e7eb;border-radius:16px;background:#fff}
    .pb-sched{border-collapse:separate;border-spacing:0;min-width:100%;font-size:12px}
    .pb-sched th{position:sticky;top:0;background:#1f2937;color:white;padding:10px;border-right:1px solid #374151;white-space:nowrap;z-index:2}
    .pb-sched th:first-child{left:0;z-index:4}
    .pb-sched td{vertical-align:top;min-width:142px;padding:7px;border-right:1px solid #e5e7eb;border-top:1px solid #e5e7eb;background:#fff}
    .pb-sched td:first-child{position:sticky;left:0;background:#f8fafc;min-width:155px;font-weight:700;z-index:3}
    .pb-assignment{border-left:5px solid;padding:7px 8px;border-radius:8px;margin-bottom:5px;line-height:1.25}
    .pb-free{color:#15803d;font-weight:700;padding:8px}
    .pb-weekend{background:#f8fafc!important}
    </style>
    """
    head = "<tr><th>Staff</th>" + "".join(
        f"<th>{html.escape(day.strftime('%a'))}<br>{html.escape(day.strftime('%d %b'))}</th>" for day in days
    ) + "</tr>"
    body_rows = []
    for _, employee in employees.iterrows():
        employee_id = int(employee["employee_id"])
        cells = [f"<td>{html.escape(str(employee['employee']))}<br><small>{html.escape(str(employee.get('role') or ''))}</small></td>"]
        for day in days:
            matches = expanded[(expanded["employee_id"] == employee_id) & (expanded["work_date"] == day)] if not expanded.empty else pd.DataFrame()
            class_name = " class='pb-weekend'" if day.weekday() >= 5 else ""
            if matches.empty:
                value = "<div class='pb-free'>Available</div>" if day.weekday() < 5 else ""
            else:
                chunks = []
                for _, assignment in matches.iterrows():
                    bg, border = colours.get(int(assignment["job_id"]), ("#f1f5f9", "#64748b"))
                    label = f"{assignment['job_no']} · {assignment['job_name']}"
                    details = f"{assignment['start_time']}–{assignment['finish_time']} · {assignment['display_hours']:.1f}h"
                    chunks.append(
                        f"<div class='pb-assignment' style='background:{bg};border-color:{border}'>"
                        f"<strong>{html.escape(label)}</strong><br><small>{html.escape(details)}</small></div>"
                    )
                value = "".join(chunks)
            cells.append(f"<td{class_name}>{value}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    st.markdown(styles + "<div class='pb-sched-wrap'><table class='pb-sched'>" + head + "".join(body_rows) + "</table></div>", unsafe_allow_html=True)


def render_jobhub_staff_scheduler(
    *,
    df_query: Callable,
    execute: Callable,
    connect: Callable,
    use_postgres: bool,
    current_username: Callable | None = None,
    refresh: Callable | None = None,
) -> None:
    """Render the all-in-one visual scheduler within JobHub."""
    ensure_scheduler_schema(df_query=df_query, execute=execute, use_postgres=use_postgres)
    username = "JobHub user"
    if current_username:
        try:
            username = str(current_username() or username)
        except Exception:
            pass

    st.subheader("Visual Staff Scheduling Board")
    st.caption("One JobHub app, one database: jobs, staff, leave and scheduling are managed here together.")

    filter_c1, filter_c2, filter_c3 = st.columns([1.2, 1, 1])
    today = date.today()
    default_start = today - timedelta(days=today.weekday())
    board_start = filter_c1.date_input("Board starts", value=default_start, key="pb_visual_board_start")
    weeks = int(filter_c2.selectbox("Weeks visible", [1, 2, 3, 4, 6], index=1, key="pb_visual_board_weeks"))
    include_completed = filter_c3.checkbox("Include completed jobs", value=False, key="pb_visual_include_completed")
    board_end = board_start + timedelta(days=(weeks * 7) - 1)

    status_where = "" if include_completed else "WHERE LOWER(COALESCE(j.status,'')) NOT IN ('complete','completed','archived','closed','cancelled')"
    jobs = df_query(
        f"""
        SELECT j.id AS job_id, j.job_no, j.job_name, j.site_address, j.status,
               j.leading_hand, j.start_date, j.end_date,
               COALESCE(b.name,'') AS builder
        FROM jobs j
        LEFT JOIN builders_clients b ON b.id=j.builder_client_id
        {status_where}
        ORDER BY j.job_no
        """
    )
    employees = df_query(
        """
        SELECT id AS employee_id, name AS employee, role,
               COALESCE(status,'Active') AS status
        FROM employees
        WHERE LOWER(COALESCE(status,'Active'))='active'
        ORDER BY name
        """
    )
    schedule = df_query(
        """
        SELECT s.id AS schedule_id, s.job_id, s.employee_id, s.schedule_date,
               s.start_time, s.finish_time, s.site_role, s.notes,
               s.period_type, s.period_start, s.period_end, s.planned_hours,
               COALESCE(s.source_app,'JobHub') AS source_app,
               COALESCE(s.created_by,'') AS created_by,
               e.name AS employee, e.role AS employee_role,
               j.job_no, j.job_name, j.site_address, j.status AS job_status
        FROM staff_schedule s
        JOIN employees e ON e.id=s.employee_id
        JOIN jobs j ON j.id=s.job_id
        WHERE COALESCE(NULLIF(s.period_end,''),s.schedule_date) >= ?
          AND COALESCE(NULLIF(s.period_start,''),s.schedule_date) <= ?
        ORDER BY COALESCE(NULLIF(s.period_start,''),s.schedule_date), e.name, j.job_no
        """,
        (str(board_start), str(board_end)),
    )
    expanded = _expanded_schedule(schedule, board_start, board_end)
    colours = _job_colour_map(jobs)

    scheduled_job_ids = set(expanded["job_id"].astype(int)) if not expanded.empty else set()
    scheduled_employee_ids = set(expanded["employee_id"].astype(int)) if not expanded.empty else set()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Jobs shown", len(jobs))
    m2.metric("Jobs with crew", len(scheduled_job_ids))
    m3.metric("Staff scheduled", len(scheduled_employee_ids))
    m4.metric("Visible assignments", len(expanded))

    with st.expander("Add crew to a job", expanded=True):
        if jobs.empty or employees.empty:
            st.info("Create at least one job and one active employee first.")
        else:
            job_labels = {f"{row['job_no']} — {row['job_name']}": int(row["job_id"]) for _, row in jobs.iterrows()}
            employee_labels = {str(row["employee"]): int(row["employee_id"]) for _, row in employees.iterrows()}
            with st.form("pb_all_in_one_schedule_form"):
                c1, c2 = st.columns(2)
                selected_job_label = c1.selectbox("Job", list(job_labels), key="pb_vis_job")
                selected_staff_names = c2.multiselect("Staff", list(employee_labels), key="pb_vis_staff")
                c3, c4, c5, c6 = st.columns(4)
                from_day = c3.date_input("From", value=board_start, key="pb_vis_from")
                to_day = c4.date_input("To", value=board_start, key="pb_vis_to")
                start_time = c5.text_input("Start", value="07:00", key="pb_vis_start")
                finish_time = c6.text_input("Finish", value="15:00", key="pb_vis_finish")
                c7, c8, c9 = st.columns(3)
                planned_hours = c7.number_input("Hours per day", min_value=0.0, step=0.25, value=7.6, key="pb_vis_hours")
                site_role = c8.selectbox("Site role", ["Painter", "Leading Hand", "Supervisor", "Apprentice", "Subcontractor", "Other"], key="pb_vis_role")
                weekdays_only = c9.checkbox("Weekdays only", value=True, key="pb_vis_weekdays")
                notes = st.text_area("Notes", key="pb_vis_notes")
                allow_conflicts = st.checkbox("Allow double-booking conflicts", value=False, key="pb_vis_allow_conflicts")
                save_schedule = st.form_submit_button("Schedule selected crew")

            if save_schedule:
                if not selected_staff_names:
                    st.error("Select at least one staff member.")
                elif to_day < from_day:
                    st.error("The finishing date must be on or after the starting date.")
                else:
                    days: list[date] = []
                    cursor = from_day
                    while cursor <= to_day:
                        if not weekdays_only or cursor.weekday() < 5:
                            days.append(cursor)
                        cursor += timedelta(days=1)
                    created = 0
                    blocked: list[str] = []
                    for staff_name in selected_staff_names:
                        employee_id = employee_labels[staff_name]
                        for work_day in days:
                            leave = df_query(
                                """
                                SELECT COUNT(*) AS c FROM staff_leave_requests
                                WHERE employee_id=? AND LOWER(COALESCE(status,''))='approved'
                                  AND ? BETWEEN start_date AND end_date
                                """,
                                (employee_id, str(work_day)),
                            )
                            if not leave.empty and int(leave.iloc[0]["c"]) > 0:
                                blocked.append(f"{staff_name} is on approved leave on {work_day:%d %b}.")
                                continue
                            clashes = df_query(
                                """
                                SELECT COUNT(*) AS c FROM staff_schedule
                                WHERE employee_id=?
                                  AND COALESCE(NULLIF(period_start,''),schedule_date) <= ?
                                  AND COALESCE(NULLIF(period_end,''),schedule_date) >= ?
                                  AND NOT (COALESCE(finish_time,'23:59') <= ? OR COALESCE(start_time,'00:00') >= ?)
                                """,
                                (employee_id, str(work_day), str(work_day), start_time, finish_time),
                            )
                            if not allow_conflicts and not clashes.empty and int(clashes.iloc[0]["c"]) > 0:
                                blocked.append(f"{staff_name} is already booked on {work_day:%d %b}.")
                                continue
                            now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            execute(
                                """
                                INSERT INTO staff_schedule
                                (job_id,employee_id,schedule_date,start_time,finish_time,site_role,
                                 notes,created_at,period_type,period_start,period_end,planned_hours,
                                 created_by,source_app,updated_at)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                                """,
                                (
                                    job_labels[selected_job_label], employee_id, str(work_day), start_time,
                                    finish_time, site_role, notes, now_text, "Single Day", str(work_day),
                                    str(work_day), float(planned_hours), username, "JobHub Visual Scheduler", now_text,
                                ),
                            )
                            created += 1
                    if created:
                        st.success(f"Created {created} schedule assignment(s) in JobHub.")
                    if blocked:
                        st.warning("\n".join(blocked[:12]) + ("\nMore conflicts were skipped." if len(blocked) > 12 else ""))
                    if created and refresh:
                        refresh()

    board_tab, jobs_tab, staff_tab, workload_tab, manage_tab, import_tab = st.tabs(
        ["Weekly board", "Jobs → Crew", "Staff → Jobs", "Workload", "Manage", "Import old scheduler"]
    )

    with board_tab:
        st.caption("Every active staff member stays visible. Green cells mean available; coloured cards show their job allocation.")
        _render_matrix(expanded, employees, board_start, board_end, colours)

    with jobs_tab:
        if jobs.empty:
            st.info("No jobs found.")
        else:
            card_columns = st.columns(3)
            for index, (_, job) in enumerate(jobs.iterrows()):
                job_id = int(job["job_id"])
                matches = expanded[expanded["job_id"] == job_id] if not expanded.empty else pd.DataFrame()
                bg, border = colours.get(job_id, ("#f8fafc", "#64748b"))
                if matches.empty:
                    allocation = "<div style='color:#b45309;font-weight:700'>No crew scheduled in this period</div>"
                else:
                    grouped = []
                    for work_day, group in matches.groupby("work_date"):
                        names = ", ".join(sorted(group["employee"].astype(str).unique()))
                        grouped.append(f"<div><strong>{work_day:%a %d %b}</strong>: {html.escape(names)}</div>")
                    allocation = "".join(grouped)
                address = html.escape(str(job.get("site_address") or "No site address"))
                card_columns[index % 3].markdown(
                    f"""
                    <div style='background:{bg};border:1px solid {border};border-left:7px solid {border};border-radius:14px;padding:14px;margin-bottom:12px;min-height:175px'>
                    <div style='font-weight:800;font-size:16px'>{html.escape(str(job['job_no']))} · {html.escape(str(job['job_name']))}</div>
                    <div style='font-size:12px;color:#475569;margin:5px 0 10px'>{address}</div>
                    <div style='font-size:12px;line-height:1.65'>{allocation}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    with staff_tab:
        if employees.empty:
            st.info("No active employees found.")
        else:
            card_columns = st.columns(3)
            for index, (_, employee) in enumerate(employees.iterrows()):
                employee_id = int(employee["employee_id"])
                matches = expanded[expanded["employee_id"] == employee_id] if not expanded.empty else pd.DataFrame()
                if matches.empty:
                    allocation = "<div style='color:#15803d;font-weight:700'>Available for this period</div>"
                else:
                    grouped = []
                    for work_day, group in matches.groupby("work_date"):
                        jobs_text = ", ".join(sorted((group["job_no"] + " · " + group["job_name"]).astype(str).unique()))
                        grouped.append(f"<div><strong>{work_day:%a %d %b}</strong>: {html.escape(jobs_text)}</div>")
                    allocation = "".join(grouped)
                card_columns[index % 3].markdown(
                    f"""
                    <div style='background:#fff;border:1px solid #e2e8f0;border-left:7px solid #334155;border-radius:14px;padding:14px;margin-bottom:12px;min-height:165px'>
                    <div style='font-weight:800;font-size:16px'>{html.escape(str(employee['employee']))}</div>
                    <div style='font-size:12px;color:#64748b;margin:4px 0 10px'>{html.escape(str(employee.get('role') or ''))}</div>
                    <div style='font-size:12px;line-height:1.65'>{allocation}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    with workload_tab:
        if expanded.empty:
            st.info("No scheduled hours in the selected period.")
        else:
            workload = expanded.groupby("employee", as_index=False)["display_hours"].sum().sort_values("display_hours", ascending=False)
            workload = workload.rename(columns={"display_hours": "Scheduled hours"}).set_index("employee")
            st.bar_chart(workload)
            st.dataframe(workload.reset_index(), width="stretch", hide_index=True)

    with manage_tab:
        if schedule.empty:
            st.info("No schedule rows in the selected period.")
        else:
            display = schedule.copy()
            display["label"] = (
                display["schedule_id"].astype(str) + " — " + display["employee"].astype(str) + " — " +
                display["job_no"].astype(str) + " — " +
                display["period_start"].fillna(display["schedule_date"]).astype(str) + " to " +
                display["period_end"].fillna(display["schedule_date"]).astype(str)
            )
            selected_labels = st.multiselect("Select schedule rows to delete", display["label"].tolist(), key="pb_visual_delete_rows")
            if st.button("Delete selected schedule rows", type="secondary", key="pb_visual_delete_button"):
                selected_ids = display.loc[display["label"].isin(selected_labels), "schedule_id"].astype(int).tolist()
                for schedule_id in selected_ids:
                    execute("DELETE FROM staff_schedule WHERE id=?", (schedule_id,))
                st.success(f"Deleted {len(selected_ids)} schedule row(s).")
                if selected_ids and refresh:
                    refresh()
            export_columns = [
                "schedule_id", "employee", "job_no", "job_name", "schedule_date", "period_start", "period_end",
                "start_time", "finish_time", "planned_hours", "site_role", "source_app", "created_by", "notes",
            ]
            st.dataframe(schedule[export_columns], width="stretch", hide_index=True)
            st.download_button(
                "Download visible schedule CSV",
                schedule[export_columns].to_csv(index=False).encode("utf-8"),
                file_name=f"PB_JobHub_schedule_{board_start}_{board_end}.csv",
                mime="text/csv",
            )

    with import_tab:
        st.write("Bring the jobs and scheduling data from the original standalone Staff Scheduler into this JobHub database.")
        st.info("In the old scheduler, open Data & Backup and download the complete database backup. Upload that .db file here.")
        uploaded = st.file_uploader("Standalone Staff Scheduler database backup", type=["db", "sqlite", "sqlite3"], key="pb_legacy_scheduler_upload")
        if uploaded is not None:
            suffix = Path(uploaded.name).suffix or ".db"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
                handle.write(uploaded.getbuffer())
                temporary_path = handle.name
            try:
                counts = inspect_legacy_scheduler_db(temporary_path)
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Legacy jobs", counts["jobs"])
                c2.metric("Legacy staff", counts["staff"])
                c3.metric("Legacy assignments", counts["assignments"])
                c4.metric("Legacy leave", counts["leave_requests"])
                st.caption("Existing JobHub jobs are matched by job number and staff by name. Matching assignments are skipped, so the import can be run again safely.")
                if st.button("Import everything into JobHub", type="primary", key="pb_run_legacy_import"):
                    result = migrate_legacy_scheduler_db(
                        temporary_path,
                        connect=connect,
                        imported_by=username,
                        source_name=uploaded.name,
                    )
                    st.success(
                        f"Imported {result['jobs_added']} new jobs, {result['employees_added']} new staff, "
                        f"{result['assignments_added']} assignments and {result['leave_added']} leave records."
                    )
                    st.write(
                        f"Matched existing: {result['jobs_matched']} jobs and {result['employees_matched']} staff. "
                        f"Skipped duplicates: {result['assignments_skipped']} assignments and {result['leave_skipped']} leave records."
                    )
                    if refresh:
                        refresh()
            except Exception as exc:
                st.error(f"Could not import this backup: {exc}")
            finally:
                try:
                    os.unlink(temporary_path)
                except Exception:
                    pass

        imports = df_query(
            """
            SELECT source_name, jobs_added, employees_added, assignments_added,
                   leave_added, imported_by, imported_at
            FROM scheduler_import_log
            ORDER BY id DESC
            LIMIT 10
            """
        )
        if not imports.empty:
            st.subheader("Recent scheduler imports")
            st.dataframe(imports, width="stretch", hide_index=True)
