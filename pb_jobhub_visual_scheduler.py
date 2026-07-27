from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable, Iterator

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from jobhub_feedback import error as pb_error, replay_pending as pb_replay_pending, rerun as pb_rerun, success as pb_success

try:
    import psycopg2
    from psycopg2.pool import ThreadedConnectionPool
except ImportError:  # pragma: no cover - only used for local SQLite installs
    psycopg2 = None
    ThreadedConnectionPool = None


APP_NAME = "Premier Brushworks Staff Scheduler"
APP_VERSION = "2.3-clickable-board"
JOBHUB_URL = os.getenv("JOBHUB_URL", "https://premierbwjobhub.onrender.com/").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)
DATA_DIR = Path(os.getenv("DATA_DIR", "/var/data" if Path("/var/data").exists() else "."))
SQLITE_PATH = Path(os.getenv("JOBHUB_DB_PATH", str(DATA_DIR / "jobhub.db")))

def apply_embedded_scheduler_style() -> None:
    """Small scheduler-specific styling that sits inside the JobHub theme."""
    st.markdown(
        """
        <style>
            .pb-scheduler-note {
                padding: .8rem 1rem;
                border-radius: .65rem;
                border: 1px solid rgba(128,128,128,.24);
                background: rgba(128,128,128,.05);
            }
            div[data-testid="stMetric"] {
                border: 1px solid rgba(128,128,128,.20);
                padding: .85rem;
                border-radius: .72rem;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


# -----------------------------------------------------------------------------
# Shared JobHub database access
# -----------------------------------------------------------------------------

def sql_text(sql: str) -> str:
    return sql.replace("?", "%s") if USE_POSTGRES else sql


@st.cache_resource(show_spinner=False)
def scheduler_postgres_pool():
    """Reuse Postgres connections instead of opening one for every scheduler query."""
    if not USE_POSTGRES:
        return None
    if ThreadedConnectionPool is None:
        raise RuntimeError("psycopg2-binary is required when DATABASE_URL is set.")
    return ThreadedConnectionPool(
        minconn=1,
        maxconn=4,
        dsn=DATABASE_URL,
        sslmode="require",
    )


@contextmanager
def db_conn():
    pool = None
    if USE_POSTGRES:
        pool = scheduler_postgres_pool()
        conn = pool.getconn()
    else:
        SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(SQLITE_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if pool is not None:
            try:
                pool.putconn(conn)
            except Exception:
                pool.putconn(conn, close=True)
        else:
            conn.close()


def execute(sql: str, params: Iterable | None = None) -> int:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql_text(sql), tuple(params or ()))
        row_id = getattr(cur, "lastrowid", None)
        return int(row_id or 0)


def query_df(sql: str, params: Iterable | None = None) -> pd.DataFrame:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql_text(sql), tuple(params or ()))
        rows = cur.fetchall()
        columns = [item[0] for item in cur.description] if cur.description else []
    return pd.DataFrame(rows, columns=columns)


def scalar(sql: str, params: Iterable | None = None, default=None):
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql_text(sql), tuple(params or ()))
        row = cur.fetchone()
    return row[0] if row else default


def table_exists(table: str) -> bool:
    if USE_POSTGRES:
        return bool(
            scalar(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name=?",
                (table,),
                0,
            )
        )
    return bool(scalar("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,), 0))


def table_columns(table: str) -> set[str]:
    if USE_POSTGRES:
        df = query_df(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=?",
            (table,),
        )
        return set(df["column_name"].astype(str)) if not df.empty else set()
    with db_conn() as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def ensure_column(table: str, column: str, definition: str) -> None:
    if column in table_columns(table):
        return
    execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def validate_jobhub_schema() -> tuple[bool, list[str]]:
    required = ["jobs", "employees", "app_users", "builders_clients"]
    missing = [name for name in required if not table_exists(name)]
    return not missing, missing


def init_linked_schema() -> None:
    if USE_POSTGRES:
        execute(
            """
            CREATE TABLE IF NOT EXISTS staff_schedule (
                id SERIAL PRIMARY KEY,
                job_id INTEGER,
                employee_id INTEGER,
                schedule_date TEXT,
                start_time TEXT,
                finish_time TEXT,
                site_role TEXT,
                notes TEXT,
                created_at TEXT,
                period_type TEXT,
                period_start TEXT,
                period_end TEXT,
                planned_hours REAL,
                created_by TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id),
                FOREIGN KEY(employee_id) REFERENCES employees(id)
            )
            """
        )
        execute(
            """
            CREATE TABLE IF NOT EXISTS staff_leave_requests (
                id SERIAL PRIMARY KEY,
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
            CREATE TABLE IF NOT EXISTS scheduler_employee_settings (
                employee_id INTEGER PRIMARY KEY,
                target_daily_hours REAL DEFAULT 7.6,
                schedule_colour TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                updated_at TEXT,
                FOREIGN KEY(employee_id) REFERENCES employees(id)
            )
            """
        )
    else:
        execute(
            """
            CREATE TABLE IF NOT EXISTS staff_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER,
                employee_id INTEGER,
                schedule_date TEXT,
                start_time TEXT,
                finish_time TEXT,
                site_role TEXT,
                notes TEXT,
                created_at TEXT,
                period_type TEXT,
                period_start TEXT,
                period_end TEXT,
                planned_hours REAL,
                created_by TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id),
                FOREIGN KEY(employee_id) REFERENCES employees(id)
            )
            """
        )
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
            CREATE TABLE IF NOT EXISTS scheduler_employee_settings (
                employee_id INTEGER PRIMARY KEY,
                target_daily_hours REAL DEFAULT 7.6,
                schedule_colour TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                updated_at TEXT,
                FOREIGN KEY(employee_id) REFERENCES employees(id)
            )
            """
        )

    for column, definition in [
        ("period_type", "TEXT"),
        ("period_start", "TEXT"),
        ("period_end", "TEXT"),
        ("planned_hours", "REAL"),
        ("created_by", "TEXT"),
        ("linked_to_job_dates", "INTEGER DEFAULT 1"),
        ("job_day_offset", "INTEGER"),
        ("last_job_start_date", "TEXT"),
    ]:
        ensure_column("staff_schedule", column, definition)

    execute("CREATE INDEX IF NOT EXISTS idx_staff_schedule_date ON staff_schedule(schedule_date)")
    execute("CREATE INDEX IF NOT EXISTS idx_staff_schedule_employee_date ON staff_schedule(employee_id, schedule_date)")
    execute("CREATE INDEX IF NOT EXISTS idx_staff_leave_dates ON staff_leave_requests(start_date, end_date)")


# -----------------------------------------------------------------------------
# Authentication shared with JobHub
# -----------------------------------------------------------------------------

def verify_password(password: str, stored_hash: str) -> bool:
    try:
        stored_hash = str(stored_hash or "")
        if stored_hash.startswith("pbkdf2_sha256$"):
            _, iterations_text, salt, expected = stored_hash.split("$", 3)
            digest = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations_text)
            ).hex()
            return hmac.compare_digest(digest, expected)
        jobhub_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(jobhub_hash, stored_hash)
    except (ValueError, TypeError):
        return False


def authenticate(username: str, password: str):
    df = query_df(
        """
        SELECT u.id, u.username, u.password_hash, u.role, u.employee_id, u.active,
               e.name AS employee_name
        FROM app_users u
        LEFT JOIN employees e ON e.id=u.employee_id
        WHERE LOWER(TRIM(u.username))=LOWER(TRIM(?)) AND COALESCE(u.active,1)=1
        """,
        (username.strip(),),
    )
    if df.empty:
        return None
    row = df.iloc[0].to_dict()
    return row if verify_password(password, row.get("password_hash", "")) else None


def login_screen() -> None:
    st.markdown(f"<div class='pb-title'>{APP_NAME}</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='pb-subtitle'>Linked directly to JobHub jobs, employees, users and scheduling data.</div>",
        unsafe_allow_html=True,
    )
    _, middle, _ = st.columns([1, 1.15, 1])
    with middle:
        with st.form("linked_login"):
            st.subheader("Sign in with your JobHub account")
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
        if submitted:
            user = authenticate(username, password)
            if user:
                st.session_state["linked_user"] = user
                pb_rerun()
            else:
                pb_error("Incorrect JobHub username or password.")
        if JOBHUB_URL:
            st.link_button("Open JobHub", JOBHUB_URL, use_container_width=True)
    st.stop()


# -----------------------------------------------------------------------------
# Scheduling data helpers
# -----------------------------------------------------------------------------

def week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def daterange(start: date, end: date) -> Iterator[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def to_date(value, default: date | None = None) -> date:
    if value in (None, "", pd.NaT):
        return default or date.today()
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return pd.to_datetime(value).date()


def time_text(value, default="07:00") -> str:
    text = str(value or default)
    return text[:5] if len(text) >= 5 else default


def time_value(value, default=time(7, 0)) -> time:
    try:
        return datetime.strptime(time_text(value), "%H:%M").time()
    except ValueError:
        return default


def calculated_hours(start_text: str, finish_text: str) -> float:
    start_dt = datetime.combine(date.today(), time_value(start_text))
    finish_dt = datetime.combine(date.today(), time_value(finish_text))
    if finish_dt <= start_dt:
        return 0.0
    return round((finish_dt - start_dt).total_seconds() / 3600, 2)


def active_staff() -> pd.DataFrame:
    return query_df(
        """
        SELECT e.id, e.name, COALESCE(e.role,'Painter') AS position,
               COALESCE(e.phone,'') AS phone, COALESCE(e.status,'Active') AS status,
               COALESCE(s.target_daily_hours,7.6) AS target_daily_hours,
               COALESCE(s.notes,'') AS scheduler_notes
        FROM employees e
        LEFT JOIN scheduler_employee_settings s ON s.employee_id=e.id
        WHERE LOWER(COALESCE(e.status,'active')) NOT IN ('inactive','archived')
        ORDER BY e.name
        """
    )


def schedulable_jobs() -> pd.DataFrame:
    return query_df(
        """
        SELECT j.id, j.job_no, j.job_name, COALESCE(bc.name,'') AS builder,
               COALESCE(j.site_address,'') AS address, COALESCE(j.status,'') AS status,
               COALESCE(j.leading_hand,'') AS leading_hand,
               j.start_date, j.end_date
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id=j.builder_client_id
        WHERE LOWER(COALESCE(j.status,'')) NOT IN ('completed','paid','archived','cancelled','closed')
        ORDER BY j.job_no
        """
    )


def assignment_rows(start: date, end: date, employee_id: int | None = None) -> pd.DataFrame:
    sql = """
        SELECT s.id, s.employee_id, e.name AS staff, COALESCE(e.role,'Painter') AS position,
               COALESCE(es.target_daily_hours,7.6) AS target_daily_hours,
               s.job_id, j.job_no, j.job_name, COALESCE(bc.name,'') AS builder,
               COALESCE(j.site_address,'') AS address,
               s.schedule_date, COALESCE(s.start_time,'07:00') AS start_time,
               COALESCE(s.finish_time,'15:00') AS finish_time,
               COALESCE(s.planned_hours,0) AS planned_hours,
               COALESCE(s.site_role,'Site Work') AS site_role,
               COALESCE(s.notes,'') AS notes, COALESCE(s.created_by,'') AS created_by,
               COALESCE(s.linked_to_job_dates,0) AS linked_to_job_dates,
               s.job_day_offset, s.last_job_start_date
        FROM staff_schedule s
        JOIN employees e ON e.id=s.employee_id
        JOIN jobs j ON j.id=s.job_id
        LEFT JOIN builders_clients bc ON bc.id=j.builder_client_id
        LEFT JOIN scheduler_employee_settings es ON es.employee_id=e.id
        WHERE s.schedule_date BETWEEN ? AND ?
    """
    params: list = [start.isoformat(), end.isoformat()]
    if employee_id is not None:
        sql += " AND s.employee_id=?"
        params.append(int(employee_id))
    sql += " ORDER BY s.schedule_date, e.name, s.start_time"
    df = query_df(sql, params)
    if not df.empty:
        df["start_time"] = df["start_time"].map(time_text)
        df["finish_time"] = df["finish_time"].map(lambda x: time_text(x, "15:00"))
        df["hours"] = df.apply(
            lambda row: float(row["planned_hours"] or 0)
            if float(row["planned_hours"] or 0) > 0
            else calculated_hours(row["start_time"], row["finish_time"]),
            axis=1,
        )
    return df


def leave_rows(start: date, end: date, employee_id: int | None = None) -> pd.DataFrame:
    sql = """
        SELECT l.id, l.employee_id, e.name AS staff, l.start_date, l.end_date,
               l.leave_type, l.status, COALESCE(l.reason,'') AS reason,
               COALESCE(l.reviewed_by,'') AS reviewed_by, l.created_at
        FROM staff_leave_requests l
        JOIN employees e ON e.id=l.employee_id
        WHERE l.end_date >= ? AND l.start_date <= ?
    """
    params: list = [start.isoformat(), end.isoformat()]
    if employee_id is not None:
        sql += " AND l.employee_id=?"
        params.append(int(employee_id))
    sql += " ORDER BY l.start_date, e.name"
    return query_df(sql, params)


def has_approved_leave(employee_id: int, work_date: date) -> bool:
    return bool(
        scalar(
            """
            SELECT COUNT(*) FROM staff_leave_requests
            WHERE employee_id=? AND LOWER(status)='approved' AND ? BETWEEN start_date AND end_date
            """,
            (employee_id, work_date.isoformat()),
            0,
        )
    )


def overlapping_assignment(
    employee_id: int,
    work_date: date,
    start_value: time,
    finish_value: time,
    exclude_id: int | None = None,
) -> bool:
    sql = """
        SELECT COUNT(*) FROM staff_schedule
        WHERE employee_id=? AND schedule_date=?
          AND NOT (COALESCE(finish_time,'15:00') <= ? OR COALESCE(start_time,'07:00') >= ?)
    """
    params: list = [
        employee_id,
        work_date.isoformat(),
        start_value.strftime("%H:%M"),
        finish_value.strftime("%H:%M"),
    ]
    if exclude_id is not None:
        sql += " AND id<>?"
        params.append(int(exclude_id))
    return bool(scalar(sql, params, 0))


def add_assignment(
    employee_id: int,
    job_id: int,
    work_date: date,
    start_value: time,
    finish_value: time,
    planned_hours: float,
    site_role: str,
    notes: str,
    created_by: str,
    linked_to_job_dates: bool = True,
) -> tuple[bool, str]:
    if finish_value <= start_value:
        return False, "Finish time must be after start time."
    if has_approved_leave(employee_id, work_date):
        return False, "Staff member is on approved leave."
    if overlapping_assignment(employee_id, work_date, start_value, finish_value):
        return False, "Staff member already has an overlapping assignment."
    job_row = query_df("SELECT start_date FROM jobs WHERE id=?", (job_id,))
    job_start = None
    day_offset = None
    if not job_row.empty and str(job_row.iloc[0].get("start_date") or "").strip():
        try:
            job_start = to_date(job_row.iloc[0]["start_date"])
            day_offset = (work_date - job_start).days
        except Exception:
            job_start = None
            day_offset = None
    execute(
        """
        INSERT INTO staff_schedule
        (job_id,employee_id,schedule_date,start_time,finish_time,site_role,notes,created_at,
         period_type,period_start,period_end,planned_hours,created_by,
         linked_to_job_dates,job_day_offset,last_job_start_date)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            job_id,
            employee_id,
            work_date.isoformat(),
            start_value.strftime("%H:%M"),
            finish_value.strftime("%H:%M"),
            site_role,
            notes.strip(),
            datetime.now().isoformat(timespec="seconds"),
            "Day",
            work_date.isoformat(),
            work_date.isoformat(),
            float(planned_hours),
            created_by,
            1 if linked_to_job_dates and job_start is not None else 0,
            day_offset,
            job_start.isoformat() if job_start else None,
        ),
    )
    return True, "Assignment added to JobHub."


def sync_linked_job_dates() -> int:
    """Move linked assignments when their master job start date changes."""
    linked = query_df(
        """
        SELECT s.id,s.job_day_offset,s.schedule_date,s.last_job_start_date,
               j.start_date AS current_job_start
        FROM staff_schedule s
        JOIN jobs j ON j.id=s.job_id
        WHERE COALESCE(s.linked_to_job_dates,0)=1
          AND s.job_day_offset IS NOT NULL
          AND COALESCE(j.start_date,'')<>''
        """
    )
    moved = 0
    for _, row in linked.iterrows():
        try:
            current_start = to_date(row["current_job_start"])
            new_date = current_start + timedelta(days=int(row["job_day_offset"]))
        except Exception:
            continue
        if new_date.isoformat() == str(row["schedule_date"] or "") and str(
            row["last_job_start_date"] or ""
        ) == current_start.isoformat():
            continue
        execute(
            """
            UPDATE staff_schedule
            SET schedule_date=?,period_start=?,period_end=?,last_job_start_date=?
            WHERE id=?
            """,
            (
                new_date.isoformat(), new_date.isoformat(), new_date.isoformat(),
                current_start.isoformat(), int(row["id"]),
            ),
        )
        moved += 1
    return moved


def conflict_report(assignments: pd.DataFrame, leaves: pd.DataFrame) -> list[str]:
    alerts: list[str] = []
    if not assignments.empty:
        data = assignments.copy()
        data["work_date"] = pd.to_datetime(data["schedule_date"]).dt.date
        totals = data.groupby(
            ["employee_id", "staff", "work_date", "target_daily_hours"], as_index=False
        )["hours"].sum()
        overload = totals[totals["hours"] > totals["target_daily_hours"] + 0.01]
        for _, row in overload.iterrows():
            alerts.append(
                f"{row['staff']} has {row['hours']:.1f}h on {row['work_date'].strftime('%a %d %b')} "
                f"(target {row['target_daily_hours']:.1f}h)."
            )
        for (staff, work_day), group in data.groupby(["staff", "work_date"]):
            previous_finish = None
            for _, row in group.sort_values("start_time").iterrows():
                start_t = time_value(row["start_time"])
                finish_t = time_value(row["finish_time"], time(15, 0))
                if previous_finish and start_t < previous_finish:
                    alerts.append(f"{staff} has overlapping jobs on {work_day.strftime('%a %d %b')}.")
                    break
                previous_finish = max(previous_finish, finish_t) if previous_finish else finish_t
    if not assignments.empty and not leaves.empty:
        approved = leaves[leaves["status"].astype(str).str.lower() == "approved"].copy()
        if not approved.empty:
            approved["start"] = pd.to_datetime(approved["start_date"]).dt.date
            approved["end"] = pd.to_datetime(approved["end_date"]).dt.date
            for _, assignment in assignments.iterrows():
                work_day = to_date(assignment["schedule_date"])
                clash = approved[
                    (approved["employee_id"] == assignment["employee_id"])
                    & (approved["start"] <= work_day)
                    & (approved["end"] >= work_day)
                ]
                if not clash.empty:
                    alerts.append(
                        f"{assignment['staff']} is scheduled while on approved leave on {work_day.strftime('%a %d %b')}."
                    )
    return list(dict.fromkeys(alerts))


def schedule_grid(assignments: pd.DataFrame, start: date, end: date, staff: pd.DataFrame) -> pd.DataFrame:
    days = list(daterange(start, end))
    columns = [d.strftime("%a\n%d %b") for d in days]
    grid = pd.DataFrame(index=staff["name"].tolist(), columns=columns).fillna("")
    for _, row in assignments.iterrows():
        day = to_date(row["schedule_date"])
        column = day.strftime("%a\n%d %b")
        text = f"{row['job_no']} · {row['job_name']}\n{row['hours']:.1f}h"
        current = grid.loc[row["staff"], column]
        grid.loc[row["staff"], column] = f"{current}\n{text}".strip()
    return grid


def selected_board_cell(event, board: pd.DataFrame) -> tuple[str, str] | None:
    """Return the selected employee and date-column from a Streamlit dataframe event."""
    if event is None:
        return None
    selection = getattr(event, "selection", None)
    if selection is None and isinstance(event, dict):
        selection = event.get("selection")
    if selection is None:
        return None
    cells = getattr(selection, "cells", None)
    if cells is None and isinstance(selection, dict):
        cells = selection.get("cells")
    if not cells:
        return None
    cell = cells[0]
    if isinstance(cell, dict):
        row_index = cell.get("row")
        column_name = cell.get("column")
    else:
        try:
            row_index, column_name = cell
        except (TypeError, ValueError):
            return None
    if not isinstance(row_index, int) or row_index < 0 or row_index >= len(board):
        return None
    if not column_name or str(column_name) == "Employee":
        return None
    return str(board.iloc[row_index]["Employee"]), str(column_name)


def clickable_schedule_board(
    assignments: pd.DataFrame,
    start: date,
    end: date,
    staff: pd.DataFrame,
    jobs: pd.DataFrame,
    role_options: list[str],
    user: dict,
) -> None:
    """Render a cell-selectable board with quick add and delete controls."""
    days = list(daterange(start, end))
    day_lookup = {day.strftime("%a %d %b"): day for day in days}
    grid = schedule_grid(assignments, start, end, staff)
    grid.columns = [to_date(day).strftime("%a %d %b") for day in days]
    board = grid.reset_index().rename(columns={"index": "Employee"})

    st.caption("Click a day beside an employee to add or remove their booking.")
    event = st.dataframe(
        board,
        use_container_width=True,
        hide_index=True,
        height=max(300, 80 + len(staff) * 38),
        on_select="rerun",
        selection_mode="single-cell",
        key="clickable_schedule_board",
    )
    selected = selected_board_cell(event, board)
    if not selected:
        st.info("Select a board cell to schedule that employee for that day.")
        return

    staff_name, column_name = selected
    work_day = day_lookup.get(column_name)
    if work_day is None:
        return
    employee_id = int(staff.loc[staff["name"] == staff_name, "id"].iloc[0])
    st.markdown(f"#### {staff_name} · {work_day.strftime('%A %d %B')}")

    existing = assignments[
        (assignments["employee_id"].astype(int) == employee_id)
        & (assignments["schedule_date"].astype(str) == work_day.isoformat())
    ]
    if not existing.empty:
        st.caption("Bookings already on this day")
        for _, booking in existing.iterrows():
            details, remove = st.columns([5, 1])
            details.write(
                f"**{booking['job_no']} · {booking['job_name']}** — "
                f"{booking['start_time']}–{booking['finish_time']} · {float(booking['hours']):.1f}h"
            )
            if remove.button(
                "Delete",
                key=f"board_delete_{int(booking['id'])}",
                type="secondary",
                use_container_width=True,
            ):
                execute("DELETE FROM staff_schedule WHERE id=?", (int(booking["id"]),))
                pb_success(f"Deleted {staff_name}'s booking for {work_day.strftime('%d %b')}.")
                pb_rerun()

    job_labels = (jobs["job_no"].astype(str) + " · " + jobs["job_name"].astype(str)).tolist()
    with st.form(f"board_quick_add_{employee_id}_{work_day.isoformat()}"):
        q1, q2, q3 = st.columns([2.2, 1.2, 1.2])
        job_label = q1.selectbox("Job", job_labels, key=f"board_job_{employee_id}_{work_day}")
        site_role = q1.selectbox("Role / type", role_options, key=f"board_role_{employee_id}_{work_day}")
        start_time = q2.time_input("Start", value=time(7, 0), key=f"board_start_{employee_id}_{work_day}")
        finish_time = q2.time_input("Finish", value=time(15, 0), key=f"board_finish_{employee_id}_{work_day}")
        hours = q3.number_input(
            "Hours",
            min_value=0.25,
            max_value=24.0,
            value=7.6,
            step=0.25,
            key=f"board_hours_{employee_id}_{work_day}",
        )
        linked_dates = q3.checkbox(
            "Link to job dates",
            value=True,
            key=f"board_linked_{employee_id}_{work_day}",
            help="If the job start date changes, this booking moves with it.",
        )
        notes = st.text_input(
            "Notes (optional)",
            key=f"board_notes_{employee_id}_{work_day}",
        )
        save = st.form_submit_button(
            f"Add {staff_name} to this job",
            type="primary",
            use_container_width=True,
        )
    if save:
        job_no = job_label.split(" · ", 1)[0]
        job_id = int(jobs.loc[jobs["job_no"].astype(str) == job_no, "id"].iloc[0])
        ok, message = add_assignment(
            employee_id,
            job_id,
            work_day,
            start_time,
            finish_time,
            hours,
            site_role,
            notes,
            str(user.get("username", "")),
            linked_dates,
        )
        (pb_success if ok else pb_error)(message)
        if ok:
            pb_rerun()


def timeline_chart(assignments: pd.DataFrame, title: str):
    if assignments.empty:
        return None
    data = assignments.copy()
    data["Start"] = pd.to_datetime(data["schedule_date"] + " " + data["start_time"])
    data["Finish"] = pd.to_datetime(data["schedule_date"] + " " + data["finish_time"])
    data["Job"] = data["job_no"].astype(str) + " · " + data["job_name"].astype(str)
    data["Details"] = data["site_role"].astype(str) + " · " + data["hours"].map(lambda x: f"{x:.1f}h")
    fig = px.timeline(
        data,
        x_start="Start",
        x_end="Finish",
        y="staff",
        color="Job",
        hover_data=["job_no", "job_name", "builder", "address", "Details", "notes"],
        title=title,
    )
    fig.update_yaxes(autorange="reversed", title="Staff")
    fig.update_xaxes(title="Date and time", tickformat="%a %d %b\n%H:%M")
    fig.update_layout(
        height=max(430, 72 + 48 * data["staff"].nunique()),
        legend_title_text="Job",
        margin=dict(l=20, r=20, t=60, b=20),
    )
    return fig


def workload_chart(assignments: pd.DataFrame, staff: pd.DataFrame, start: date, end: date):
    weekdays = sum(1 for day in daterange(start, end) if day.weekday() < 5)
    target = staff[["id", "name", "target_daily_hours"]].copy()
    target["Target hours"] = target["target_daily_hours"] * weekdays
    grouped = (
        assignments.groupby("employee_id", as_index=False)["hours"].sum().rename(columns={"hours": "Allocated hours"})
        if not assignments.empty
        else pd.DataFrame(columns=["employee_id", "Allocated hours"])
    )
    target = target.merge(grouped, left_on="id", right_on="employee_id", how="left")
    target["Allocated hours"] = target["Allocated hours"].fillna(0.0)
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            name="Allocated",
            x=target["name"],
            y=target["Allocated hours"],
            text=target["Allocated hours"].round(1),
            textposition="auto",
        )
    )
    fig.add_trace(go.Bar(name="Target", x=target["name"], y=target["Target hours"], opacity=0.42))
    fig.update_layout(
        barmode="group",
        title="Allocated hours compared with target",
        xaxis_title="Staff",
        yaxis_title="Hours",
        height=420,
        margin=dict(l=20, r=20, t=60, b=20),
    )
    return fig, target


# -----------------------------------------------------------------------------
# Pages
# -----------------------------------------------------------------------------

def sidebar(user: dict) -> str:
    st.sidebar.markdown("### PREMIER BRUSHWORKS")
    st.sidebar.caption("Linked Staff Scheduler")
    st.sidebar.markdown(f"**{user.get('employee_name') or user.get('username') or 'User'}**")
    st.sidebar.caption(str(user.get("role", "employee")).title())
    st.sidebar.divider()
    role = str(user.get("role", "employee")).lower()
    if role in {"admin", "manager"}:
        pages = ["Dashboard", "Schedule Board", "Leave", "Staff & Job Sync", "Export"]
    else:
        pages = ["My Schedule", "My Leave"]
    page = st.sidebar.radio("Navigation", pages, label_visibility="collapsed")
    st.sidebar.divider()
    if JOBHUB_URL:
        st.sidebar.link_button("Open JobHub", JOBHUB_URL, use_container_width=True)
    if st.sidebar.button("Sign out", use_container_width=True):
        st.session_state.pop("linked_user", None)
        pb_rerun()
    st.sidebar.caption(f"Version {APP_VERSION}")
    st.sidebar.caption("Database: shared PostgreSQL" if USE_POSTGRES else f"Database: {SQLITE_PATH}")
    return page


def page_dashboard() -> None:
    st.title("Staff Scheduler Dashboard")
    selected = st.date_input("Week commencing", value=week_start(date.today()))
    start = week_start(to_date(selected))
    end = start + timedelta(days=6)
    assignments = assignment_rows(start, end)
    leaves = leave_rows(start, end)
    staff = active_staff()

    scheduled_staff = assignments["employee_id"].nunique() if not assignments.empty else 0
    allocated = float(assignments["hours"].sum()) if not assignments.empty else 0.0
    covered_jobs = assignments["job_id"].nunique() if not assignments.empty else 0
    pending_leave = int((leaves["status"].astype(str).str.lower() == "pending").sum()) if not leaves.empty else 0
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Scheduled staff", scheduled_staff, f"of {len(staff)} active")
    m2.metric("Allocated hours", f"{allocated:,.1f}")
    m3.metric("Jobs covered", covered_jobs)
    m4.metric("Pending leave", pending_leave)

    alerts = conflict_report(assignments, leaves)
    if alerts:
        pb_error(f"{len(alerts)} scheduling warning{'s' if len(alerts) != 1 else ''}")
        with st.expander("Open warnings", expanded=True):
            for alert in alerts:
                st.write(f"• {alert}")
    else:
        pb_success("No leave clashes, overlapping bookings or daily hour overloads were found.")

    chart = timeline_chart(assignments, f"Crew timeline · {start.strftime('%d %b')} to {end.strftime('%d %b %Y')}")
    if chart:
        st.plotly_chart(chart, use_container_width=True)
    else:
        st.info("No JobHub schedule entries have been entered for this week.")

    left, right = st.columns([1.15, 1])
    with left:
        st.subheader("Weekly schedule grid")
        st.dataframe(schedule_grid(assignments, start, end, staff), use_container_width=True, height=max(280, 80 + len(staff) * 38))
    with right:
        fig, capacity = workload_chart(assignments, staff, start, end)
        st.plotly_chart(fig, use_container_width=True)
        available = capacity[capacity["Allocated hours"] < capacity["Target hours"] - 0.01]
        if not available.empty:
            st.caption(
                "Available capacity: "
                + ", ".join(
                    f"{row['name']} {row['Target hours'] - row['Allocated hours']:.1f}h"
                    for _, row in available.iterrows()
                )
            )


def page_schedule(user: dict) -> None:
    st.title("Schedule Board")
    staff = active_staff()
    jobs = schedulable_jobs()
    if staff.empty or jobs.empty:
        st.warning("Add active employees and open jobs in JobHub before scheduling.")
        return

    c1, c2 = st.columns(2)
    selected = c1.date_input("Board week", value=week_start(date.today()))
    display_days = c2.selectbox("Board range", [7, 14, 21, 28], index=1, format_func=lambda x: f"{x} days")
    start = week_start(to_date(selected))
    end = start + timedelta(days=display_days - 1)
    tabs = st.tabs(["Clickable board", "Add assignment", "Allocate crew", "Edit / delete"])
    staff_names = staff["name"].tolist()
    job_labels = (jobs["job_no"].astype(str) + " · " + jobs["job_name"].astype(str)).tolist()
    role_options = ["Site Work", "Leading Hand", "Supervision", "Quote / Measure", "Office / Planning", "Training", "Touch-ups", "Other"]

    with tabs[0]:
        assignments = assignment_rows(start, end)
        leaves = leave_rows(start, end)
        alerts = conflict_report(assignments, leaves)
        if alerts:
            st.warning("Warnings: " + " | ".join(alerts[:4]) + (" …" if len(alerts) > 4 else ""))
        chart = timeline_chart(assignments, f"JobHub schedule · {start.strftime('%d %b')} to {end.strftime('%d %b %Y')}")
        if chart:
            st.plotly_chart(chart, use_container_width=True)
        else:
            st.info("Nothing is scheduled in this date range.")
        clickable_schedule_board(assignments, start, end, staff, jobs, role_options, user)
        b1, b2 = st.columns(2)
        with b1:
            if st.button("Copy first week to next week", use_container_width=True):
                source = assignment_rows(start, start + timedelta(days=6))
                added = skipped = 0
                for _, row in source.iterrows():
                    ok, _ = add_assignment(
                        int(row["employee_id"]),
                        int(row["job_id"]),
                        to_date(row["schedule_date"]) + timedelta(days=7),
                        time_value(row["start_time"]),
                        time_value(row["finish_time"], time(15, 0)),
                        float(row["hours"]),
                        str(row["site_role"]),
                        str(row["notes"] or ""),
                        str(user.get("username", "")),
                    )
                    added += int(ok)
                    skipped += int(not ok)
                pb_success(f"Copied {added} assignment(s); skipped {skipped} conflict(s).")
                pb_rerun()
        with b2:
            csv = assignments.to_csv(index=False).encode("utf-8")
            st.download_button(
                "Download displayed schedule CSV",
                csv,
                f"PB_JobHub_schedule_{start.isoformat()}_{end.isoformat()}.csv",
                "text/csv",
                use_container_width=True,
            )

    with tabs[1]:
        with st.form("add_linked_assignment"):
            a1, a2, a3 = st.columns(3)
            staff_name = a1.selectbox("Staff member", staff_names)
            work_day = a1.date_input("Date", value=start)
            job_label = a2.selectbox("Job", job_labels)
            site_role = a2.selectbox("Role / type", role_options)
            start_time = a3.time_input("Start", value=time(7, 0))
            finish_time = a3.time_input("Finish", value=time(15, 0))
            hours = a3.number_input("Allocated hours", min_value=0.25, max_value=24.0, value=7.6, step=0.25)
            linked_dates = st.checkbox(
                "Keep this assignment linked to the job start date",
                value=True,
                help="If the job start date changes, this assignment moves by the same number of days.",
            )
            notes = st.text_area("Notes", placeholder="Access, supervisor, equipment or special instructions")
            save = st.form_submit_button("Add to JobHub schedule", type="primary", use_container_width=True)
        if save:
            employee_id = int(staff.loc[staff["name"] == staff_name, "id"].iloc[0])
            job_no = job_label.split(" · ", 1)[0]
            job_id = int(jobs.loc[jobs["job_no"].astype(str) == job_no, "id"].iloc[0])
            ok, message = add_assignment(
                employee_id,
                job_id,
                to_date(work_day),
                start_time,
                finish_time,
                hours,
                site_role,
                notes,
                str(user.get("username", "")),
                linked_dates,
            )
            (pb_success if ok else pb_error)(message)
            if ok:
                pb_rerun()

    with tabs[2]:
        with st.form("bulk_linked_assignment"):
            crew = st.multiselect("Crew", staff_names)
            job_label = st.selectbox("Job", job_labels, key="bulk_job")
            c1, c2, c3 = st.columns(3)
            range_start = c1.date_input("Start date", value=start, key="bulk_start")
            range_end = c1.date_input("End date", value=start + timedelta(days=4), key="bulk_end")
            weekdays = c2.multiselect(
                "Days",
                ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                default=["Mon", "Tue", "Wed", "Thu", "Fri"],
            )
            site_role = c2.selectbox("Role / type", role_options, key="bulk_role")
            start_time = c3.time_input("Start", value=time(7, 0), key="bulk_start_time")
            finish_time = c3.time_input("Finish", value=time(15, 0), key="bulk_finish_time")
            hours = c3.number_input("Hours per person / day", min_value=0.25, max_value=24.0, value=7.6, step=0.25)
            linked_dates = st.checkbox(
                "Keep crew dates linked to the job start date",
                value=True,
                key="bulk_linked_dates",
            )
            notes = st.text_area("Crew notes")
            save_bulk = st.form_submit_button("Allocate crew", type="primary", use_container_width=True)
        if save_bulk:
            if not crew:
                pb_error("Select at least one employee.")
            elif to_date(range_end) < to_date(range_start):
                pb_error("End date must be on or after start date.")
            else:
                day_numbers = {name: number for number, name in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])}
                selected_day_numbers = {day_numbers[name] for name in weekdays}
                job_no = job_label.split(" · ", 1)[0]
                job_id = int(jobs.loc[jobs["job_no"].astype(str) == job_no, "id"].iloc[0])
                added = 0
                skipped: list[str] = []
                for work_day in daterange(to_date(range_start), to_date(range_end)):
                    if work_day.weekday() not in selected_day_numbers:
                        continue
                    for staff_name in crew:
                        employee_id = int(staff.loc[staff["name"] == staff_name, "id"].iloc[0])
                        ok, message = add_assignment(
                            employee_id,
                            job_id,
                            work_day,
                            start_time,
                            finish_time,
                            hours,
                            site_role,
                            notes,
                            str(user.get("username", "")),
                            linked_dates,
                        )
                        if ok:
                            added += 1
                        else:
                            skipped.append(f"{staff_name} {work_day.strftime('%d %b')}: {message}")
                pb_success(f"Added {added} JobHub schedule entries.")
                if skipped:
                    st.warning("Skipped:\n\n" + "\n\n".join(f"• {item}" for item in skipped[:15]))
                if added:
                    pb_rerun()

    with tabs[3]:
        assignments = assignment_rows(start, end)
        if assignments.empty:
            st.info("There are no assignments in this range.")
        else:
            labels = assignments.apply(
                lambda row: f"#{row['id']} · {to_date(row['schedule_date']).strftime('%a %d %b')} · {row['staff']} · {row['job_no']} {row['job_name']}",
                axis=1,
            ).tolist()
            selected_label = st.selectbox("Assignment", labels)
            assignment_id = int(selected_label.split(" · ", 1)[0].replace("#", ""))
            row = assignments[assignments["id"] == assignment_id].iloc[0]
            with st.form("edit_linked_assignment"):
                e1, e2, e3 = st.columns(3)
                edit_staff = e1.selectbox("Staff member", staff_names, index=staff_names.index(row["staff"]))
                edit_date = e1.date_input("Date", value=to_date(row["schedule_date"]))
                current_job = f"{row['job_no']} · {row['job_name']}"
                edit_job = e2.selectbox("Job", job_labels, index=job_labels.index(current_job) if current_job in job_labels else 0)
                edit_role = e2.selectbox("Role / type", role_options, index=role_options.index(row["site_role"]) if row["site_role"] in role_options else 0)
                edit_start = e3.time_input("Start", value=time_value(row["start_time"]))
                edit_finish = e3.time_input("Finish", value=time_value(row["finish_time"], time(15, 0)))
                edit_hours = e3.number_input("Hours", min_value=0.25, max_value=24.0, value=float(row["hours"]), step=0.25)
                edit_linked = st.checkbox(
                    "Keep linked to job start date",
                    value=bool(int(row.get("linked_to_job_dates") or 0)),
                    key=f"edit_linked_{assignment_id}",
                )
                edit_notes = st.text_area("Notes", value=str(row["notes"] or ""))
                update = st.form_submit_button("Save changes", type="primary", use_container_width=True)
            if update:
                employee_id = int(staff.loc[staff["name"] == edit_staff, "id"].iloc[0])
                job_no = edit_job.split(" · ", 1)[0]
                job_id = int(jobs.loc[jobs["job_no"].astype(str) == job_no, "id"].iloc[0])
                if edit_finish <= edit_start:
                    pb_error("Finish time must be after start time.")
                elif has_approved_leave(employee_id, to_date(edit_date)):
                    pb_error("This staff member is on approved leave.")
                elif overlapping_assignment(employee_id, to_date(edit_date), edit_start, edit_finish, assignment_id):
                    pb_error("This would overlap another assignment.")
                else:
                    execute(
                        """
                        UPDATE staff_schedule
                        SET employee_id=?,job_id=?,schedule_date=?,start_time=?,finish_time=?,planned_hours=?,
                            site_role=?,notes=?,period_start=?,period_end=?,linked_to_job_dates=?,
                            job_day_offset=?,last_job_start_date=?
                        WHERE id=?
                        """,
                        (
                            employee_id,
                            job_id,
                            to_date(edit_date).isoformat(),
                            edit_start.strftime("%H:%M"),
                            edit_finish.strftime("%H:%M"),
                            edit_hours,
                            edit_role,
                            edit_notes.strip(),
                            to_date(edit_date).isoformat(),
                            to_date(edit_date).isoformat(),
                            1 if edit_linked else 0,
                            (
                                to_date(edit_date) - to_date(
                                    jobs.loc[jobs["id"] == job_id, "start_date"].iloc[0]
                                )
                            ).days
                            if edit_linked
                            and not jobs.loc[jobs["id"] == job_id, "start_date"].empty
                            and str(jobs.loc[jobs["id"] == job_id, "start_date"].iloc[0] or "").strip()
                            else None,
                            str(jobs.loc[jobs["id"] == job_id, "start_date"].iloc[0] or "")
                            if edit_linked
                            and not jobs.loc[jobs["id"] == job_id, "start_date"].empty
                            else None,
                            assignment_id,
                        ),
                    )
                    pb_success("JobHub schedule entry updated.")
                    pb_rerun()
            if st.button("Delete selected assignment", use_container_width=True):
                execute("DELETE FROM staff_schedule WHERE id=?", (assignment_id,))
                pb_success("JobHub schedule entry deleted.")
                pb_rerun()


def page_leave(user: dict) -> None:
    st.title("Leave Requests")
    staff = active_staff()
    tab_add, tab_review, tab_register = st.tabs(["Add request", "Approve / reject", "Leave register"])
    with tab_add:
        with st.form("manager_leave_request"):
            employee_name = st.selectbox("Employee", staff["name"].tolist())
            c1, c2, c3 = st.columns(3)
            start_date = c1.date_input("Start date", value=date.today())
            end_date = c2.date_input("End date", value=date.today())
            leave_type = c3.selectbox("Leave type", ["Annual Leave", "Personal Leave", "RDO", "Unpaid Leave", "Other"])
            reason = st.text_area("Reason / notes")
            status = st.selectbox("Initial status", ["Pending", "Approved"], index=0)
            save = st.form_submit_button("Save leave request", type="primary", use_container_width=True)
        if save:
            if to_date(end_date) < to_date(start_date):
                pb_error("End date must be on or after start date.")
            else:
                employee_id = int(staff.loc[staff["name"] == employee_name, "id"].iloc[0])
                execute(
                    """
                    INSERT INTO staff_leave_requests
                    (employee_id,start_date,end_date,leave_type,status,reason,reviewed_by,reviewed_at,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        employee_id,
                        to_date(start_date).isoformat(),
                        to_date(end_date).isoformat(),
                        leave_type,
                        status,
                        reason.strip(),
                        str(user.get("username", "")) if status == "Approved" else "",
                        datetime.now().isoformat(timespec="seconds") if status == "Approved" else None,
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
                pb_success("Leave request saved.")
                pb_rerun()

    with tab_review:
        pending = query_df(
            """
            SELECT l.id,e.name AS staff,l.start_date,l.end_date,l.leave_type,l.reason,l.created_at
            FROM staff_leave_requests l JOIN employees e ON e.id=l.employee_id
            WHERE LOWER(l.status)='pending' ORDER BY l.start_date
            """
        )
        if pending.empty:
            st.info("No pending leave requests.")
        else:
            label_list = pending.apply(
                lambda row: f"#{row['id']} · {row['staff']} · {row['start_date']} to {row['end_date']} · {row['leave_type']}", axis=1
            ).tolist()
            selected = st.selectbox("Pending request", label_list)
            request_id = int(selected.split(" · ", 1)[0].replace("#", ""))
            selected_row = pending[pending["id"] == request_id].iloc[0]
            st.write(selected_row["reason"] or "No reason supplied.")
            c1, c2 = st.columns(2)
            if c1.button("Approve", type="primary", use_container_width=True):
                execute(
                    "UPDATE staff_leave_requests SET status='Approved',reviewed_by=?,reviewed_at=? WHERE id=?",
                    (str(user.get("username", "")), datetime.now().isoformat(timespec="seconds"), request_id),
                )
                pb_success("Leave approved.")
                pb_rerun()
            if c2.button("Reject", use_container_width=True):
                execute(
                    "UPDATE staff_leave_requests SET status='Rejected',reviewed_by=?,reviewed_at=? WHERE id=?",
                    (str(user.get("username", "")), datetime.now().isoformat(timespec="seconds"), request_id),
                )
                pb_success("Leave rejected.")
                pb_rerun()

    with tab_register:
        records = query_df(
            """
            SELECT l.id,e.name AS staff,l.start_date,l.end_date,l.leave_type,l.status,l.reason,l.reviewed_by,l.created_at
            FROM staff_leave_requests l JOIN employees e ON e.id=l.employee_id
            ORDER BY l.start_date DESC,l.id DESC
            """
        )
        st.dataframe(records, use_container_width=True, hide_index=True)


def page_crew_suggestions(user: dict) -> None:
    st.title("Crew Suggestions")
    st.caption(
        "JobHub compares job dates, estimator labour hours, existing allocations, leave, "
        "staff roles and current progress. Suggestions never alter the schedule until approved."
    )
    start = to_date(st.date_input("Suggestion period starts", value=date.today()))
    days = st.selectbox("Planning range", [7, 14, 21, 28], index=1, format_func=lambda x: f"{x} days")
    end = start + timedelta(days=int(days) - 1)
    staff = active_staff()
    jobs = schedulable_jobs()
    assignments = assignment_rows(start, end)
    leaves = leave_rows(start, end)
    if staff.empty or jobs.empty:
        st.info("Active staff and open jobs are required before suggestions can be calculated.")
        return

    estimate_hours = query_df(
        """
        SELECT e.job_id, MAX(COALESCE(e.labour_hours,0)) AS estimated_hours
        FROM estimate_working_sheets e
        WHERE COALESCE(e.archived,0)=0
        GROUP BY e.job_id
        """
    )
    estimated_map = {
        int(row["job_id"]): float(row["estimated_hours"] or 0)
        for _, row in estimate_hours.iterrows()
    } if not estimate_hours.empty else {}
    allocated_map = (
        assignments.groupby("job_id")["hours"].sum().to_dict()
        if not assignments.empty else {}
    )
    progress_map = {}
    if table_exists("job_progress_settings") and table_exists("job_dwelling_progress"):
        progress = query_df(
            """
            SELECT d.job_id,
                   AVG(
                       (
                         CASE d.sealer WHEN 'Complete' THEN 1 WHEN 'In progress' THEN .5 ELSE 0 END * 15 +
                         CASE d.spray_walls WHEN 'Complete' THEN 1 WHEN 'In progress' THEN .5 ELSE 0 END * 25 +
                         CASE d.spray_ceilings WHEN 'Complete' THEN 1 WHEN 'In progress' THEN .5 ELSE 0 END * 20 +
                         CASE d.spray_gloss WHEN 'Complete' THEN 1 WHEN 'In progress' THEN .5 ELSE 0 END * 15 +
                         CASE d.pc WHEN 'Complete' THEN 1 WHEN 'In progress' THEN .5 ELSE 0 END * 15 +
                         CASE d.touchups WHEN 'Complete' THEN 1 WHEN 'In progress' THEN .5 ELSE 0 END * 10
                       )
                   ) AS progress_percent
            FROM job_dwelling_progress d
            GROUP BY d.job_id
            """
        )
        if not progress.empty:
            progress_map = {
                int(row["job_id"]): float(row["progress_percent"] or 0)
                for _, row in progress.iterrows()
            }

    workdays = max(1, sum(1 for day in daterange(start, end) if day.weekday() < 5))
    capacity = staff[["id", "name", "position", "target_daily_hours"]].copy()
    capacity["target_hours"] = capacity["target_daily_hours"].astype(float) * workdays
    employee_allocated = (
        assignments.groupby("employee_id")["hours"].sum().to_dict()
        if not assignments.empty else {}
    )
    capacity["allocated_hours"] = capacity["id"].map(employee_allocated).fillna(0.0)
    capacity["available_hours"] = (
        capacity["target_hours"] - capacity["allocated_hours"]
    ).clip(lower=0)
    approved_leave = leaves[
        leaves["status"].astype(str).str.lower() == "approved"
    ] if not leaves.empty else pd.DataFrame()
    on_leave_ids = set(approved_leave["employee_id"].astype(int).tolist()) if not approved_leave.empty else set()
    capacity.loc[capacity["id"].isin(on_leave_ids), "available_hours"] *= 0.5

    suggestions = []
    for _, job in jobs.iterrows():
        job_id = int(job["id"])
        estimated = float(estimated_map.get(job_id, 0))
        allocated = float(allocated_map.get(job_id, 0))
        progress = float(progress_map.get(job_id, 0))
        remaining_by_estimate = max(0.0, estimated * (1 - progress / 100) - allocated)
        try:
            job_start = to_date(job.get("start_date"), start)
            job_end = to_date(job.get("end_date"), end)
        except Exception:
            job_start, job_end = start, end
        overlaps = job_start <= end and job_end >= start
        urgency = "Now" if overlaps else ("Upcoming" if job_start <= end + timedelta(days=14) else "Later")
        if not overlaps and urgency == "Later" and remaining_by_estimate <= 0:
            continue
        required_hours = remaining_by_estimate if estimated > 0 else max(7.6, allocated)
        candidates = capacity[capacity["available_hours"] > 0.1].copy()
        if candidates.empty:
            crew_text = "No capacity"
        else:
            candidates["continuity"] = candidates["name"].astype(str).str.lower().eq(
                str(job.get("leading_hand") or "").lower()
            ).astype(int)
            candidates["trade_score"] = candidates["position"].astype(str).str.lower().map(
                lambda value: 2 if "painter" in value or "trades" in value else
                (1 if "apprentice" in value or "brush" in value else 0)
            )
            candidates = candidates.sort_values(
                ["continuity", "trade_score", "available_hours"],
                ascending=[False, False, False],
            )
            recommended = candidates.head(3)
            crew_text = ", ".join(
                f"{row['name']} ({row['available_hours']:.1f}h available)"
                for _, row in recommended.iterrows()
            )
        suggestions.append(
            {
                "job_id": job_id,
                "Job": f"{job['job_no']} · {job['job_name']}",
                "Urgency": urgency,
                "Estimator hours": round(estimated, 1),
                "Already scheduled": round(allocated, 1),
                "Progress %": round(progress, 1),
                "Suggested remaining hours": round(required_hours, 1),
                "Suggested crew": crew_text,
                "Reason": (
                    "Matches job dates; prioritises continuity, painting role, availability "
                    "and avoids approved leave."
                ),
            }
        )
    suggestion_df = pd.DataFrame(suggestions)
    if suggestion_df.empty:
        pb_success("No additional crew allocation is currently suggested.")
        return
    st.dataframe(
        suggestion_df.drop(columns=["job_id"]),
        width="stretch",
        hide_index=True,
    )
    st.subheader("Approve a suggestion")
    job_labels = suggestion_df["Job"].tolist()
    with st.form("approve_crew_suggestion"):
        selected_job = st.selectbox("Job", job_labels)
        selected_row = suggestion_df[suggestion_df["Job"] == selected_job].iloc[0]
        job_id = int(selected_row["job_id"])
        recommended_names = [
            part.split(" (", 1)[0]
            for part in str(selected_row["Suggested crew"]).split(", ")
            if part and part != "No capacity"
        ]
        crew = st.multiselect(
            "Crew to schedule",
            staff["name"].tolist(),
            default=[name for name in recommended_names if name in staff["name"].tolist()],
        )
        c1, c2, c3 = st.columns(3)
        job_row = jobs[jobs["id"] == job_id].iloc[0]
        default_start = max(start, to_date(job_row.get("start_date"), start))
        schedule_start = c1.date_input("Start date", value=default_start)
        schedule_end = c2.date_input("End date", value=min(end, default_start + timedelta(days=4)))
        hours = c3.number_input("Hours per person/day", min_value=0.25, value=7.6, step=0.25)
        approve = st.form_submit_button("Approve and add suggested crew", type="primary")
    if approve:
        if not crew:
            pb_error("Select at least one crew member.")
            return
        added = skipped = 0
        for work_day in daterange(to_date(schedule_start), to_date(schedule_end)):
            if work_day.weekday() >= 5:
                continue
            for name in crew:
                employee_id = int(staff.loc[staff["name"] == name, "id"].iloc[0])
                ok, _ = add_assignment(
                    employee_id, job_id, work_day, time(7, 0), time(15, 0),
                    float(hours), "Site Work", "JobHub approved crew suggestion",
                    str(user.get("username", "")), True,
                )
                added += int(ok)
                skipped += int(not ok)
        pb_success(f"Suggestion approved: {added} schedule entries added; {skipped} conflicts skipped.")
        pb_rerun()

def page_sync() -> None:
    st.title("Staff & Job Sync")
    st.markdown(
        "<div class='pb-note'><b>JobHub is the source of truth.</b> Employees and jobs are read live from JobHub. "
        "This scheduler writes only to JobHub's shared staff_schedule table and the linked leave/settings tables.</div>",
        unsafe_allow_html=True,
    )
    st.write("")
    staff = active_staff()
    jobs = schedulable_jobs()
    schedule_count = int(scalar("SELECT COUNT(*) FROM staff_schedule", default=0) or 0)
    m1, m2, m3 = st.columns(3)
    m1.metric("Active JobHub employees", len(staff))
    m2.metric("Open JobHub jobs", len(jobs))
    m3.metric("Shared schedule entries", schedule_count)

    tab1, tab2, tab3 = st.tabs(["Employees", "Jobs", "Target hours"])
    with tab1:
        st.caption("Edit names, roles, phone numbers and employment status in JobHub Management → Employees.")
        st.dataframe(staff, use_container_width=True, hide_index=True)
    with tab2:
        st.caption("Create and edit jobs in JobHub. They appear here automatically.")
        st.dataframe(jobs, use_container_width=True, hide_index=True)
    with tab3:
        if staff.empty:
            st.info("No active employees found.")
        else:
            selected = st.selectbox("Employee", staff["name"].tolist())
            row = staff[staff["name"] == selected].iloc[0]
            with st.form("target_hours_form"):
                target = st.number_input(
                    "Target daily hours",
                    min_value=0.5,
                    max_value=24.0,
                    value=float(row["target_daily_hours"]),
                    step=0.1,
                )
                notes = st.text_area("Scheduler notes", value=str(row["scheduler_notes"] or ""))
                save = st.form_submit_button("Save scheduler settings", type="primary", use_container_width=True)
            if save:
                employee_id = int(row["id"])
                if USE_POSTGRES:
                    execute(
                        """
                        INSERT INTO scheduler_employee_settings(employee_id,target_daily_hours,notes,updated_at)
                        VALUES (?,?,?,?)
                        ON CONFLICT(employee_id) DO UPDATE SET
                            target_daily_hours=EXCLUDED.target_daily_hours,
                            notes=EXCLUDED.notes,
                            updated_at=EXCLUDED.updated_at
                        """,
                        (employee_id, target, notes.strip(), datetime.now().isoformat(timespec="seconds")),
                    )
                else:
                    execute(
                        """
                        INSERT INTO scheduler_employee_settings(employee_id,target_daily_hours,notes,updated_at)
                        VALUES (?,?,?,?)
                        ON CONFLICT(employee_id) DO UPDATE SET
                            target_daily_hours=excluded.target_daily_hours,
                            notes=excluded.notes,
                            updated_at=excluded.updated_at
                        """,
                        (employee_id, target, notes.strip(), datetime.now().isoformat(timespec="seconds")),
                    )
                pb_success("Target hours saved.")
                pb_rerun()


def page_export() -> None:
    st.title("Export Shared Scheduling Data")
    start = st.date_input("Start date", value=week_start(date.today()))
    end = st.date_input("End date", value=week_start(date.today()) + timedelta(days=27))
    if to_date(end) < to_date(start):
        pb_error("End date must be on or after start date.")
        return
    assignments = assignment_rows(to_date(start), to_date(end))
    leaves = leave_rows(to_date(start), to_date(end))
    c1, c2 = st.columns(2)
    c1.download_button(
        "Download schedule CSV",
        assignments.to_csv(index=False).encode("utf-8"),
        f"PB_linked_schedule_{to_date(start).isoformat()}_{to_date(end).isoformat()}.csv",
        "text/csv",
        use_container_width=True,
    )
    c2.download_button(
        "Download leave CSV",
        leaves.to_csv(index=False).encode("utf-8"),
        f"PB_leave_{to_date(start).isoformat()}_{to_date(end).isoformat()}.csv",
        "text/csv",
        use_container_width=True,
    )
    st.dataframe(assignments, use_container_width=True, hide_index=True)


def page_my_schedule(user: dict) -> None:
    st.title("My Schedule")
    employee_id = user.get("employee_id")
    if not employee_id:
        st.warning("Your JobHub account is not linked to an employee record. Ask an administrator to link it.")
        return
    selected = st.date_input("Week commencing", value=week_start(date.today()))
    start = week_start(to_date(selected))
    end = start + timedelta(days=13)
    assignments = assignment_rows(start, end, int(employee_id))
    chart = timeline_chart(assignments, f"My schedule · {start.strftime('%d %b')} to {end.strftime('%d %b %Y')}")
    if chart:
        st.plotly_chart(chart, use_container_width=True)
        display = assignments[["schedule_date", "start_time", "finish_time", "job_no", "job_name", "address", "site_role", "notes"]]
        st.dataframe(display, use_container_width=True, hide_index=True)
    else:
        st.info("You have no assignments in this date range.")


def page_my_leave(user: dict) -> None:
    st.title("My Leave")
    employee_id = user.get("employee_id")
    if not employee_id:
        st.warning("Your JobHub account is not linked to an employee record.")
        return
    with st.form("employee_leave_request"):
        c1, c2, c3 = st.columns(3)
        start_date = c1.date_input("Start date", value=date.today())
        end_date = c2.date_input("End date", value=date.today())
        leave_type = c3.selectbox("Leave type", ["Annual Leave", "Personal Leave", "RDO", "Unpaid Leave", "Other"])
        reason = st.text_area("Reason / notes")
        submit = st.form_submit_button("Submit request", type="primary", use_container_width=True)
    if submit:
        if to_date(end_date) < to_date(start_date):
            pb_error("End date must be on or after start date.")
        else:
            execute(
                """
                INSERT INTO staff_leave_requests
                (employee_id,start_date,end_date,leave_type,status,reason,reviewed_by,reviewed_at,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(employee_id),
                    to_date(start_date).isoformat(),
                    to_date(end_date).isoformat(),
                    leave_type,
                    "Pending",
                    reason.strip(),
                    "",
                    None,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            pb_success("Leave request submitted.")
            pb_rerun()
    records = query_df(
        """
        SELECT start_date,end_date,leave_type,status,reason,reviewed_by,created_at
        FROM staff_leave_requests WHERE employee_id=? ORDER BY start_date DESC,id DESC
        """,
        (int(employee_id),),
    )
    st.dataframe(records, use_container_width=True, hide_index=True)


def main() -> None:
    try:
        valid, missing = validate_jobhub_schema()
    except Exception as exc:
        pb_error("Could not connect to the shared JobHub database.")
        st.exception(exc)
        st.stop()
    if not valid:
        pb_error("This database is not a JobHub database. Missing tables: " + ", ".join(missing))
        st.code(
            "For Render, set the scheduler's DATABASE_URL to the exact same PostgreSQL DATABASE_URL used by JobHub."
        )
        st.stop()
    try:
        init_linked_schema()
    except Exception as exc:
        pb_error("Connected to JobHub, but the linked scheduling tables could not be initialised.")
        st.exception(exc)
        st.stop()

    user = st.session_state.get("linked_user")
    if not user:
        login_screen()
    page = sidebar(user)
    role = str(user.get("role", "employee")).lower()
    if role in {"admin", "manager"}:
        if page == "Dashboard":
            page_dashboard()
        elif page == "Schedule Board":
            page_schedule(user)
        elif page == "Leave":
            page_leave(user)
        elif page == "Staff & Job Sync":
            page_sync()
        else:
            page_export()
    else:
        if page == "My Schedule":
            page_my_schedule(user)
        else:
            page_my_leave(user)

# -----------------------------------------------------------------------------
# Embedded JobHub grouped views
# -----------------------------------------------------------------------------

def _scheduler_date_range(key_prefix: str, default_days: int = 13) -> tuple[date, date]:
    c1, c2 = st.columns(2)
    start = c1.date_input("From", value=week_start(date.today()), key=f"{key_prefix}_from")
    end = c2.date_input(
        "To",
        value=week_start(date.today()) + timedelta(days=default_days),
        key=f"{key_prefix}_to",
    )
    start_date = to_date(start)
    end_date = to_date(end)
    if end_date < start_date:
        pb_error("The end date must be on or after the start date.")
        return start_date, start_date
    return start_date, end_date


def page_jobs_to_crew() -> None:
    st.title("Jobs → Crew")
    st.caption("See every job, the assigned crew, dates, roles and planned hours.")

    start, end = _scheduler_date_range("jobs_to_crew")
    assignments = assignment_rows(start, end)
    jobs = schedulable_jobs()

    if assignments.empty:
        st.info("No staff assignments were found in this date range.")
        if not jobs.empty:
            unscheduled = jobs[["job_no", "job_name", "builder", "address", "leading_hand"]].copy()
            unscheduled.columns = ["Job No", "Job", "Builder / Client", "Address", "Leading Hand"]
            st.subheader("Open jobs with no displayed assignments")
            st.dataframe(unscheduled, width="stretch", hide_index=True)
        return

    summary = (
        assignments.groupby(
            ["job_id", "job_no", "job_name", "builder", "address"],
            dropna=False,
        )
        .agg(
            Crew=("staff", lambda values: ", ".join(sorted(set(str(v) for v in values if str(v).strip())))),
            crew_count=("employee_id", "nunique"),
            planned_hours=("hours", "sum"),
            first_date=("schedule_date", "min"),
            last_date=("schedule_date", "max"),
        )
        .reset_index()
    )
    summary["planned_hours"] = summary["planned_hours"].round(2)

    display = summary[
        ["job_no", "job_name", "builder", "address", "Crew",
         "crew_count", "planned_hours", "first_date", "last_date"]
    ].copy()
    display.columns = [
        "Job No", "Job", "Builder / Client", "Address", "Assigned Crew",
        "Crew Count", "Planned Hours", "First Date", "Last Date"
    ]
    st.dataframe(display, width="stretch", hide_index=True)

    st.subheader("Job-by-job detail")
    for _, job_row in summary.sort_values(["job_no", "job_name"]).iterrows():
        job_assignments = assignments[assignments["job_id"] == job_row["job_id"]].copy()
        heading = (
            f"{job_row['job_no']} · {job_row['job_name']} — "
            f"{int(job_row['crew_count'])} staff / {float(job_row['planned_hours']):,.1f} hrs"
        )
        with st.expander(heading, expanded=False):
            detail = job_assignments[
                ["schedule_date", "staff", "position", "site_role",
                 "start_time", "finish_time", "hours", "notes"]
            ].copy()
            detail.columns = ["Date", "Staff", "Position", "Role", "Start", "Finish", "Hours", "Notes"]
            st.dataframe(detail, width="stretch", hide_index=True)

    assigned_job_ids = set(assignments["job_id"].astype(int).tolist())
    unscheduled = jobs[~jobs["id"].astype(int).isin(assigned_job_ids)].copy()
    if not unscheduled.empty:
        st.subheader("Open jobs without crew in this range")
        unscheduled = unscheduled[["job_no", "job_name", "builder", "address", "leading_hand"]]
        unscheduled.columns = ["Job No", "Job", "Builder / Client", "Address", "Leading Hand"]
        st.dataframe(unscheduled, width="stretch", hide_index=True)


def page_staff_to_jobs() -> None:
    st.title("Staff → Jobs")
    st.caption("See what each staff member is booked on and their remaining capacity.")

    start, end = _scheduler_date_range("staff_to_jobs")
    assignments = assignment_rows(start, end)
    staff = active_staff()

    if assignments.empty:
        st.info("No staff assignments were found in this date range.")
        if not staff.empty:
            available = staff[["name", "position", "phone", "target_daily_hours"]].copy()
            available.columns = ["Staff", "Position", "Phone", "Target Daily Hours"]
            st.dataframe(available, width="stretch", hide_index=True)
        return

    workday_count = sum(1 for day in daterange(start, end) if day.weekday() < 5)

    summary = (
        assignments.groupby(
            ["employee_id", "staff", "position", "target_daily_hours"],
            dropna=False,
        )
        .agg(
            Jobs=("job_no", lambda values: ", ".join(sorted(set(str(v) for v in values if str(v).strip())))),
            job_count=("job_id", "nunique"),
            allocated_hours=("hours", "sum"),
            first_date=("schedule_date", "min"),
            last_date=("schedule_date", "max"),
        )
        .reset_index()
    )
    summary["target_hours"] = summary["target_daily_hours"].astype(float) * max(workday_count, 1)
    summary["remaining_capacity"] = (summary["target_hours"] - summary["allocated_hours"]).round(2)
    summary["allocated_hours"] = summary["allocated_hours"].round(2)

    display = summary[
        ["staff", "position", "Jobs", "job_count", "allocated_hours",
         "target_hours", "remaining_capacity", "first_date", "last_date"]
    ].copy()
    display.columns = [
        "Staff", "Position", "Assigned Jobs", "Job Count", "Allocated Hours",
        "Target Hours", "Remaining Capacity", "First Date", "Last Date"
    ]
    st.dataframe(display, width="stretch", hide_index=True)

    fig, capacity = workload_chart(assignments, staff, start, end)
    st.plotly_chart(fig, width="stretch")

    st.subheader("Staff-by-staff detail")
    for _, staff_row in summary.sort_values("staff").iterrows():
        staff_assignments = assignments[
            assignments["employee_id"] == staff_row["employee_id"]
        ].copy()
        heading = (
            f"{staff_row['staff']} — {float(staff_row['allocated_hours']):,.1f} hrs allocated / "
            f"{float(staff_row['remaining_capacity']):,.1f} hrs remaining"
        )
        with st.expander(heading, expanded=False):
            detail = staff_assignments[
                ["schedule_date", "job_no", "job_name", "site_role",
                 "start_time", "finish_time", "hours", "notes"]
            ].copy()
            detail.columns = ["Date", "Job No", "Job", "Role", "Start", "Finish", "Hours", "Notes"]
            st.dataframe(detail, width="stretch", hide_index=True)

    assigned_staff_ids = set(assignments["employee_id"].astype(int).tolist())
    unassigned = staff[~staff["id"].astype(int).isin(assigned_staff_ids)].copy()
    if not unassigned.empty:
        st.subheader("Unassigned staff in this range")
        unassigned = unassigned[["name", "position", "phone", "target_daily_hours"]]
        unassigned.columns = ["Staff", "Position", "Phone", "Target Daily Hours"]
        st.dataframe(unassigned, width="stretch", hide_index=True)


def render_jobhub_staff_scheduler(user: dict | None = None) -> None:
    """Render the full native scheduler inside JobHub."""
    pb_replay_pending()
    apply_embedded_scheduler_style()
    user = user or {}
    role = str(user.get("role", "employee") or "employee").lower()

    try:
        valid, missing = validate_jobhub_schema()
    except Exception as exc:
        pb_error("Could not connect the visual scheduler to the JobHub database.")
        st.exception(exc)
        return

    if not valid:
        pb_error("The scheduler could not find the required JobHub tables: " + ", ".join(missing))
        return

    try:
        init_linked_schema()
    except Exception as exc:
        pb_error("The scheduling tables could not be initialised.")
        st.exception(exc)
        return

    moved_assignments = sync_linked_job_dates()
    if moved_assignments:
        pb_success(
            f"{moved_assignments} linked schedule assignment(s) moved automatically "
            "to match updated job dates."
        )

    if role in {"admin", "manager"}:
        pages = [
            "Dashboard",
            "Schedule Board",
            "Crew Suggestions",
            "Jobs → Crew",
            "Staff → Jobs",
            "Leave",
            "Staff & Job Sync",
            "Export",
        ]
        selected_page = st.radio(
            "Scheduler View",
            pages,
            horizontal=True,
            key="pb_jobhub_visual_scheduler_page",
        )

        if selected_page == "Dashboard":
            page_dashboard()
        elif selected_page == "Schedule Board":
            page_schedule(user)
        elif selected_page == "Crew Suggestions":
            page_crew_suggestions(user)
        elif selected_page == "Jobs → Crew":
            page_jobs_to_crew()
        elif selected_page == "Staff → Jobs":
            page_staff_to_jobs()
        elif selected_page == "Leave":
            page_leave(user)
        elif selected_page == "Staff & Job Sync":
            page_sync()
        else:
            page_export()
    else:
        selected_page = st.radio(
            "Scheduler View",
            ["My Schedule", "My Leave"],
            horizontal=True,
            key="pb_jobhub_employee_scheduler_page",
        )
        if selected_page == "My Schedule":
            page_my_schedule(user)
        else:
            page_my_leave(user)



# PB_JOBHUB_SCHEDULER_AUDIT_CLEANUP_20260727

# PB_JOBHUB_SCHEDULER_FEEDBACK_V2_20260727
