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
from jobhub_time import jobhub_now, jobhub_today

try:
    import psycopg2
    from psycopg2.pool import ThreadedConnectionPool
except ImportError:  # pragma: no cover - only used for local SQLite installs
    psycopg2 = None
    ThreadedConnectionPool = None


APP_NAME = "Premier Brushworks Staff Scheduler"
APP_VERSION = "2.5-crews-stages-clash-review"
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


def execute_many(sql: str, rows: Iterable[Iterable]) -> None:
    """Execute a batch in one transaction and one pooled connection."""
    prepared = [tuple(row) for row in rows]
    if not prepared:
        return
    with db_conn() as conn:
        cur = conn.cursor()
        cur.executemany(sql_text(sql), prepared)


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


@st.cache_resource(show_spinner=False)
def init_linked_schema() -> None:
    if USE_POSTGRES:
        execute(
            """
            CREATE TABLE IF NOT EXISTS staff_schedule (
                id SERIAL PRIMARY KEY,
                job_id INTEGER,
                job_stage_id INTEGER,
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
                target_daily_hours REAL DEFAULT 8.0,
                schedule_colour TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                updated_at TEXT,
                FOREIGN KEY(employee_id) REFERENCES employees(id)
            )
            """
        )
        execute(
            """
            CREATE TABLE IF NOT EXISTS scheduler_crews (
                id SERIAL PRIMARY KEY,
                crew_name TEXT NOT NULL UNIQUE,
                lead_employee_id INTEGER NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT,
                FOREIGN KEY(lead_employee_id) REFERENCES employees(id)
            )
            """
        )
        execute(
            """
            CREATE TABLE IF NOT EXISTS scheduler_crew_members (
                crew_id INTEGER NOT NULL,
                employee_id INTEGER NOT NULL,
                is_lead INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (crew_id, employee_id),
                FOREIGN KEY(crew_id) REFERENCES scheduler_crews(id) ON DELETE CASCADE,
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
                job_stage_id INTEGER,
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
                target_daily_hours REAL DEFAULT 8.0,
                schedule_colour TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                updated_at TEXT,
                FOREIGN KEY(employee_id) REFERENCES employees(id)
            )
            """
        )
        execute(
            """
            CREATE TABLE IF NOT EXISTS scheduler_crews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                crew_name TEXT NOT NULL UNIQUE,
                lead_employee_id INTEGER NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT,
                FOREIGN KEY(lead_employee_id) REFERENCES employees(id)
            )
            """
        )
        execute(
            """
            CREATE TABLE IF NOT EXISTS scheduler_crew_members (
                crew_id INTEGER NOT NULL,
                employee_id INTEGER NOT NULL,
                is_lead INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (crew_id, employee_id),
                FOREIGN KEY(crew_id) REFERENCES scheduler_crews(id) ON DELETE CASCADE,
                FOREIGN KEY(employee_id) REFERENCES employees(id)
            )
            """
        )

    for column, definition in [
        ("job_stage_id", "INTEGER"),
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
    execute("CREATE INDEX IF NOT EXISTS idx_staff_schedule_job_stage ON staff_schedule(job_id, job_stage_id)")
    execute("CREATE INDEX IF NOT EXISTS idx_scheduler_crew_lead ON scheduler_crews(lead_employee_id)")
    execute("CREATE INDEX IF NOT EXISTS idx_scheduler_crew_member ON scheduler_crew_members(employee_id)")
    execute(
        "CREATE INDEX IF NOT EXISTS idx_staff_schedule_linked_job "
        "ON staff_schedule(linked_to_job_dates, job_id)"
    )
    execute("CREATE INDEX IF NOT EXISTS idx_staff_leave_dates ON staff_leave_requests(start_date, end_date)")
    execute(
        """
        UPDATE scheduler_employee_settings
        SET target_daily_hours = 8.0
        WHERE ABS(COALESCE(target_daily_hours, 0) - 7.6) < 0.001
        """
    )


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
            submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
        if submitted:
            user = authenticate(username, password)
            if user:
                st.session_state["linked_user"] = user
                pb_rerun()
            else:
                pb_error("Incorrect JobHub username or password.")
        if JOBHUB_URL:
            st.link_button("Open JobHub", JOBHUB_URL, width="stretch")
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
        return default or jobhub_today()
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
    start_dt = datetime.combine(jobhub_today(), time_value(start_text))
    finish_dt = datetime.combine(jobhub_today(), time_value(finish_text))
    if finish_dt <= start_dt:
        return 0.0
    return round((finish_dt - start_dt).total_seconds() / 3600, 2)


def active_staff() -> pd.DataFrame:
    return query_df(
        """
        SELECT e.id, e.name, COALESCE(e.role,'Painter') AS position,
               COALESCE(e.phone,'') AS phone, COALESCE(e.status,'Active') AS status,
               COALESCE(s.target_daily_hours,8.0) AS target_daily_hours,
               COALESCE(s.notes,'') AS scheduler_notes
        FROM employees e
        LEFT JOIN scheduler_employee_settings s ON s.employee_id=e.id
        WHERE LOWER(COALESCE(e.status,'active')) NOT IN ('inactive','archived')
        ORDER BY e.name
        """
    )


def saved_crews(active_only: bool = True) -> pd.DataFrame:
    """Return saved lead/member groups used by the tile board and bulk allocator."""
    where_clause = "WHERE COALESCE(c.active,1)=1" if active_only else ""
    crews = query_df(
        f"""
        SELECT c.id, c.crew_name, c.lead_employee_id, lead.name AS lead_name,
               COALESCE(c.active,1) AS active, COALESCE(c.notes,'') AS notes
        FROM scheduler_crews c
        JOIN employees lead ON lead.id=c.lead_employee_id
        {where_clause}
        ORDER BY c.crew_name
        """
    )
    if crews.empty:
        return crews
    member_labels = []
    member_ids = []
    for crew_id in crews["id"].astype(int):
        members = query_df(
            """
            SELECT e.id, e.name, COALESCE(cm.is_lead,0) AS is_lead
            FROM scheduler_crew_members cm
            JOIN employees e ON e.id=cm.employee_id
            WHERE cm.crew_id=?
            ORDER BY cm.is_lead DESC, e.name
            """,
            (crew_id,),
        )
        member_labels.append(", ".join(members["name"].astype(str)) if not members.empty else "")
        member_ids.append(members["id"].astype(int).tolist() if not members.empty else [])
    crews["member_names"] = member_labels
    crews["member_ids"] = member_ids
    return crews


def crew_for_lead(employee_id: int) -> dict | None:
    crews = saved_crews(active_only=True)
    match = crews[crews["lead_employee_id"].astype(int) == int(employee_id)] if not crews.empty else crews
    return match.iloc[0].to_dict() if not match.empty else None


def save_scheduler_crew(
    crew_id: int | None,
    crew_name: str,
    lead_employee_id: int,
    member_employee_ids: Iterable[int],
    notes: str = "",
) -> int:
    """Create or update a saved crew and its members in one transaction."""
    member_ids = list(dict.fromkeys([int(lead_employee_id), *[int(value) for value in member_employee_ids]]))
    now = jobhub_now().isoformat(timespec="seconds")
    with db_conn() as conn:
        cur = conn.cursor()
        if crew_id is None:
            insert_sql = """
                INSERT INTO scheduler_crews
                (crew_name,lead_employee_id,active,notes,created_at,updated_at)
                VALUES (?,?,1,?,?,?)
            """
            if USE_POSTGRES:
                insert_sql += " RETURNING id"
            cur.execute(sql_text(insert_sql), (crew_name.strip(), int(lead_employee_id), notes.strip(), now, now))
            crew_id = int(cur.fetchone()[0]) if USE_POSTGRES else int(cur.lastrowid)
        else:
            crew_id = int(crew_id)
            cur.execute(
                sql_text(
                    """
                    UPDATE scheduler_crews
                    SET crew_name=?,lead_employee_id=?,active=1,notes=?,updated_at=?
                    WHERE id=?
                    """
                ),
                (crew_name.strip(), int(lead_employee_id), notes.strip(), now, crew_id),
            )
            cur.execute(sql_text("DELETE FROM scheduler_crew_members WHERE crew_id=?"), (crew_id,))
        cur.executemany(
            sql_text(
                """
                INSERT INTO scheduler_crew_members (crew_id,employee_id,is_lead)
                VALUES (?,?,?)
                """
            ),
            [
                (crew_id, employee_id, 1 if employee_id == int(lead_employee_id) else 0)
                for employee_id in member_ids
            ],
        )
    return int(crew_id)


def delete_scheduler_crew(crew_id: int) -> None:
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql_text("DELETE FROM scheduler_crew_members WHERE crew_id=?"), (int(crew_id),))
        cur.execute(sql_text("DELETE FROM scheduler_crews WHERE id=?"), (int(crew_id),))


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


def schedulable_job_stage_choices(jobs: pd.DataFrame) -> dict[str, dict]:
    """Return whole-job and active-stage choices for the scheduler."""
    choices: dict[str, dict] = {}
    stages_available = table_exists("job_stages")
    for _, job in jobs.iterrows():
        base_label = f"{job['job_no']} · {job['job_name']}"
        choices[f"{base_label} — Whole Job"] = {
            "job_id": int(job["id"]),
            "job_stage_id": None,
            "job_no": str(job["job_no"]),
            "job_name": str(job["job_name"]),
            "stage_name": "Whole Job",
        }
        stages = query_df(
            """
            SELECT id, stage_name
            FROM job_stages
            WHERE job_id=?
            ORDER BY sequence_order, id
            """,
            (int(job["id"]),),
        ) if stages_available else pd.DataFrame()
        for _, stage in stages.iterrows():
            stage_name = str(stage["stage_name"] or "").strip()
            choices[f"{base_label} — {stage_name}"] = {
                "job_id": int(job["id"]),
                "job_stage_id": int(stage["id"]),
                "job_no": str(job["job_no"]),
                "job_name": str(job["job_name"]),
                "stage_name": stage_name,
            }
    return choices


def assignment_rows(start: date, end: date, employee_id: int | None = None) -> pd.DataFrame:
    stage_select = (
        "s.job_stage_id, COALESCE(js.stage_name,'Whole Job') AS stage_name"
        if table_exists("job_stages")
        else "s.job_stage_id, 'Whole Job' AS stage_name"
    )
    stage_join = "LEFT JOIN job_stages js ON js.id=s.job_stage_id" if table_exists("job_stages") else ""
    sql = f"""
        SELECT s.id, s.employee_id, e.name AS staff, COALESCE(e.role,'Painter') AS position,
               COALESCE(es.target_daily_hours,8.0) AS target_daily_hours,
               s.job_id, {stage_select}, j.job_no, j.job_name,
               COALESCE(bc.name,'') AS builder,
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
        {stage_join}
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


def job_assignment_rows(job_id: int) -> pd.DataFrame:
    """Return every current schedule booking for one Job Folder."""
    bounds = query_df(
        """
        SELECT MIN(schedule_date) AS first_date, MAX(schedule_date) AS last_date
        FROM staff_schedule WHERE job_id=?
        """,
        (int(job_id),),
    )
    if bounds.empty or not str(bounds.iloc[0].get("first_date") or "").strip():
        return pd.DataFrame()
    rows = assignment_rows(
        to_date(bounds.iloc[0]["first_date"]),
        to_date(bounds.iloc[0]["last_date"]),
    )
    return rows[rows["job_id"].astype(int) == int(job_id)].reset_index(drop=True)


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


def overlapping_assignment_rows(
    employee_id: int,
    work_date: date,
    start_value: time,
    finish_value: time,
    exclude_id: int | None = None,
) -> pd.DataFrame:
    stages_available = table_exists("job_stages")
    stage_select = "COALESCE(js.stage_name,'Whole Job')" if stages_available else "'Whole Job'"
    stage_join = "LEFT JOIN job_stages js ON js.id=s.job_stage_id" if stages_available else ""
    sql = f"""
        SELECT s.id, s.employee_id, e.name AS staff, s.job_id, s.job_stage_id,
               j.job_no, j.job_name, {stage_select} AS stage_name,
               s.schedule_date, COALESCE(s.start_time,'07:00') AS start_time,
               COALESCE(s.finish_time,'15:00') AS finish_time,
               COALESCE(s.site_role,'Site Work') AS site_role,
               COALESCE(s.notes,'') AS notes
        FROM staff_schedule s
        JOIN employees e ON e.id=s.employee_id
        JOIN jobs j ON j.id=s.job_id
        {stage_join}
        WHERE s.employee_id=? AND s.schedule_date=?
          AND NOT (COALESCE(finish_time,'15:00') <= ? OR COALESCE(start_time,'07:00') >= ?)
    """
    params: list = [
        employee_id,
        work_date.isoformat(),
        start_value.strftime("%H:%M"),
        finish_value.strftime("%H:%M"),
    ]
    if exclude_id is not None:
        sql += " AND s.id<>?"
        params.append(int(exclude_id))
    sql += " ORDER BY s.start_time, s.id"
    return query_df(sql, params)


def overlapping_assignment(
    employee_id: int,
    work_date: date,
    start_value: time,
    finish_value: time,
    exclude_id: int | None = None,
) -> bool:
    return not overlapping_assignment_rows(
        employee_id,
        work_date,
        start_value,
        finish_value,
        exclude_id,
    ).empty


def job_date_linkage(job_id: int, work_date: date, linked_to_job_dates: bool) -> tuple[date | None, int | None]:
    if not linked_to_job_dates:
        return None, None
    job_row = query_df("SELECT start_date FROM jobs WHERE id=?", (int(job_id),))
    if job_row.empty or not str(job_row.iloc[0].get("start_date") or "").strip():
        return None, None
    try:
        job_start = to_date(job_row.iloc[0]["start_date"])
        return job_start, (work_date - job_start).days
    except Exception:
        return None, None


def add_assignment(
    employee_id: int,
    job_id: int,
    job_stage_id: int | None,
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
    job_start, day_offset = job_date_linkage(job_id, work_date, linked_to_job_dates)
    execute(
        """
        INSERT INTO staff_schedule
        (job_id,job_stage_id,employee_id,schedule_date,start_time,finish_time,site_role,notes,created_at,
         period_type,period_start,period_end,planned_hours,created_by,
         linked_to_job_dates,job_day_offset,last_job_start_date)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            job_id,
            job_stage_id,
            employee_id,
            work_date.isoformat(),
            start_value.strftime("%H:%M"),
            finish_value.strftime("%H:%M"),
            site_role,
            notes.strip(),
            jobhub_now().isoformat(timespec="seconds"),
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


def replace_conflicting_assignments(
    expected_conflict_ids: Iterable[int],
    employee_id: int,
    job_id: int,
    job_stage_id: int | None,
    work_date: date,
    start_value: time,
    finish_value: time,
    planned_hours: float,
    site_role: str,
    notes: str,
    created_by: str,
    linked_to_job_dates: bool = True,
) -> tuple[bool, str]:
    """Atomically replace only the clashes the user reviewed on screen."""
    if finish_value <= start_value:
        return False, "Finish time must be after start time."
    if has_approved_leave(employee_id, work_date):
        return False, "Staff member is on approved leave."
    expected_ids = {int(value) for value in expected_conflict_ids}
    current = overlapping_assignment_rows(employee_id, work_date, start_value, finish_value)
    current_ids = set(current["id"].astype(int).tolist()) if not current.empty else set()
    if current_ids != expected_ids:
        return False, "The schedule changed while this clash was open. Review the current bookings again."
    if not current_ids:
        return add_assignment(
            employee_id, job_id, job_stage_id, work_date, start_value, finish_value,
            planned_hours, site_role, notes, created_by, linked_to_job_dates,
        )

    job_start, day_offset = job_date_linkage(job_id, work_date, linked_to_job_dates)
    placeholders = ",".join("?" for _ in current_ids)
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            sql_text(f"DELETE FROM staff_schedule WHERE employee_id=? AND id IN ({placeholders})"),
            (int(employee_id), *sorted(current_ids)),
        )
        cur.execute(
            sql_text(
                """
                INSERT INTO staff_schedule
                (job_id,job_stage_id,employee_id,schedule_date,start_time,finish_time,site_role,notes,created_at,
                 period_type,period_start,period_end,planned_hours,created_by,
                 linked_to_job_dates,job_day_offset,last_job_start_date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """
            ),
            (
                int(job_id), int(job_stage_id) if job_stage_id is not None else None,
                int(employee_id), work_date.isoformat(), start_value.strftime("%H:%M"),
                finish_value.strftime("%H:%M"), site_role, notes.strip(),
                jobhub_now().isoformat(timespec="seconds"), "Day", work_date.isoformat(),
                work_date.isoformat(), float(planned_hours), created_by,
                1 if linked_to_job_dates and job_start is not None else 0,
                day_offset, job_start.isoformat() if job_start else None,
            ),
        )
    return True, f"Replaced {len(current_ids)} conflicting booking{'s' if len(current_ids) != 1 else ''}."


def replace_conflicts_for_assignment_edit(
    expected_conflict_ids: Iterable[int],
    assignment_id: int,
    employee_id: int,
    job_id: int,
    job_stage_id: int | None,
    work_date: date,
    start_value: time,
    finish_value: time,
    planned_hours: float,
    site_role: str,
    notes: str,
    linked_to_job_dates: bool,
) -> tuple[bool, str]:
    """Delete reviewed clashes and update the selected booking in one transaction."""
    if finish_value <= start_value:
        return False, "Finish time must be after start time."
    if has_approved_leave(employee_id, work_date):
        return False, "Staff member is on approved leave."
    expected_ids = {int(value) for value in expected_conflict_ids}
    current = overlapping_assignment_rows(
        employee_id, work_date, start_value, finish_value, int(assignment_id),
    )
    current_ids = set(current["id"].astype(int).tolist()) if not current.empty else set()
    if current_ids != expected_ids:
        return False, "The schedule changed while this clash was open. Review the current bookings again."
    job_start, day_offset = job_date_linkage(job_id, work_date, linked_to_job_dates)
    placeholders = ",".join("?" for _ in current_ids)
    with db_conn() as conn:
        cur = conn.cursor()
        if current_ids:
            cur.execute(
                sql_text(f"DELETE FROM staff_schedule WHERE employee_id=? AND id IN ({placeholders})"),
                (int(employee_id), *sorted(current_ids)),
            )
        cur.execute(
            sql_text(
                """
                UPDATE staff_schedule
                SET employee_id=?,job_id=?,job_stage_id=?,schedule_date=?,start_time=?,finish_time=?,
                    planned_hours=?,site_role=?,notes=?,period_start=?,period_end=?,
                    linked_to_job_dates=?,job_day_offset=?,last_job_start_date=?
                WHERE id=?
                """
            ),
            (
                int(employee_id), int(job_id),
                int(job_stage_id) if job_stage_id is not None else None,
                work_date.isoformat(), start_value.strftime("%H:%M"), finish_value.strftime("%H:%M"),
                float(planned_hours), site_role, notes.strip(), work_date.isoformat(),
                work_date.isoformat(), 1 if linked_to_job_dates and job_start is not None else 0,
                day_offset, job_start.isoformat() if job_start else None, int(assignment_id),
            ),
        )
    return True, f"Kept the edited booking and removed {len(current_ids)} reviewed clash{'es' if len(current_ids) != 1 else ''}."


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
    updates = []
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
        updates.append(
            (
                new_date.isoformat(), new_date.isoformat(), new_date.isoformat(),
                current_start.isoformat(), int(row["id"]),
            )
        )
    execute_many(
        """
        UPDATE staff_schedule
        SET schedule_date=?,period_start=?,period_end=?,last_job_start_date=?
        WHERE id=?
        """,
        updates,
    )
    return len(updates)


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
    # `staff` comes from active_staff() (excludes inactive/archived employees),
    # while assignments can still reference employees deactivated after being
    # booked. Filter those out so grid.loc never raises a KeyError.
    if "staff" in assignments.columns and not staff.empty:
        assignments = assignments[assignments["staff"].isin(staff["name"].tolist())]
    for _, row in assignments.iterrows():
        day = to_date(row["schedule_date"])
        column = day.strftime("%a\n%d %b")
        text = (
            f"{row['job_no']} · {row['job_name']}\n"
            f"{row['stage_name']} · {row['hours']:.1f}h"
        )
        current = grid.loc[row["staff"], column]
        grid.loc[row["staff"], column] = f"{current}\n{text}".strip()
    return grid


def job_crew_grid(assignments: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Visual matrix with jobs as rows and scheduled crew inside each day."""
    days = list(daterange(start, end))
    columns = [day.strftime("%a %d %b") for day in days]
    if assignments.empty:
        return pd.DataFrame(columns=["Job"] + columns)
    jobs = (
        assignments[["job_id", "job_no", "job_name", "stage_name"]]
        .drop_duplicates()
        .sort_values(["job_no", "job_name", "stage_name"])
    )
    rows = []
    for _, job in jobs.iterrows():
        row = {"Job": f"{job['job_no']} · {job['job_name']} — {job['stage_name']}"}
        for day in days:
            booked = assignments[
                (assignments["job_id"].astype(int) == int(job["job_id"]))
                & (assignments["stage_name"].astype(str) == str(job["stage_name"]))
                & (assignments["schedule_date"].astype(str) == day.isoformat())
            ]
            row[day.strftime("%a %d %b")] = "\n".join(
                f"{item['staff']} · {float(item['hours']):.1f}h"
                for _, item in booked.iterrows()
            )
        rows.append(row)
    return pd.DataFrame(rows, columns=["Job"] + columns)


def staff_job_grid(assignments: pd.DataFrame, start: date, end: date, staff: pd.DataFrame) -> pd.DataFrame:
    """Visual matrix with employees as rows and their jobs inside each day."""
    days = list(daterange(start, end))
    columns = [day.strftime("%a %d %b") for day in days]
    rows = []
    for _, employee in staff.iterrows():
        row = {"Employee": employee["name"]}
        for day in days:
            booked = assignments[
                (assignments["employee_id"].astype(int) == int(employee["id"]))
                & (assignments["schedule_date"].astype(str) == day.isoformat())
            ] if not assignments.empty else assignments
            row[day.strftime("%a %d %b")] = "\n".join(
                f"{item['job_no']} · {item['job_name']}\n{item['stage_name']} · {float(item['hours']):.1f}h"
                for _, item in booked.iterrows()
            )
        rows.append(row)
    return pd.DataFrame(rows, columns=["Employee"] + columns)


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


def render_tile_clash_choices(pending_key: str, cell_key: str) -> bool:
    """Show both sides of every clash and apply the user's per-person choice."""
    pending = list(st.session_state.get(pending_key, []) or [])
    if not pending:
        return False

    st.error("One or more people already have a booking at this time.")
    st.caption("Review both bookings for each person, then choose which one to keep.")
    decisions: list[tuple[dict, str]] = []
    for index, item in enumerate(pending):
        start_value = time_value(item["start_time"])
        finish_value = time_value(item["finish_time"], time(15, 0))
        existing = overlapping_assignment_rows(
            int(item["employee_id"]),
            to_date(item["work_date"]),
            start_value,
            finish_value,
        )
        with st.container(border=True):
            st.markdown(f"**{item['staff_name']} · {to_date(item['work_date']).strftime('%A %d %B')}**")
            comparison_rows = []
            for _, booking in existing.iterrows():
                comparison_rows.append(
                    {
                        "Booking": "Existing",
                        "Job / Stage": f"{booking['job_no']} · {booking['job_name']} — {booking['stage_name']}",
                        "Time": f"{booking['start_time']}–{booking['finish_time']}",
                        "Role": booking["site_role"],
                    }
                )
            comparison_rows.append(
                {
                    "Booking": "New selection",
                    "Job / Stage": item["job_label"],
                    "Time": f"{item['start_time']}–{item['finish_time']}",
                    "Role": item["site_role"],
                }
            )
            st.dataframe(pd.DataFrame(comparison_rows), width="stretch", hide_index=True)
            choice = st.radio(
                f"Which booking should {item['staff_name']} keep?",
                ["Keep existing booking", "Replace existing with new selection"],
                key=f"{pending_key}_decision_{index}_{item['employee_id']}",
            )
            decisions.append((item, choice))

    apply_col, cancel_col = st.columns(2)
    if apply_col.button("Apply clash choices", type="primary", width="stretch", key=f"{pending_key}_apply"):
        messages = []
        errors = []
        for item, choice in decisions:
            if choice == "Keep existing booking":
                messages.append(f"Kept {item['staff_name']}'s existing booking.")
                continue
            ok, message = replace_conflicting_assignments(
                item["expected_conflict_ids"],
                int(item["employee_id"]),
                int(item["job_id"]),
                int(item["job_stage_id"]) if item.get("job_stage_id") is not None else None,
                to_date(item["work_date"]),
                time_value(item["start_time"]),
                time_value(item["finish_time"], time(15, 0)),
                float(item["planned_hours"]),
                str(item["site_role"]),
                str(item.get("notes", "")),
                str(item.get("created_by", "")),
                bool(item.get("linked_to_job_dates", True)),
            )
            (messages if ok else errors).append(f"{item['staff_name']}: {message}")
        st.session_state.pop(pending_key, None)
        st.session_state["scheduler_dismissed_cell"] = cell_key
        if errors:
            st.session_state["scheduler_clash_result"] = {"messages": messages, "errors": errors}
        else:
            st.session_state["scheduler_clash_result"] = {"messages": messages, "errors": []}
        pb_rerun()

    if cancel_col.button("Cancel new booking", width="stretch", key=f"{pending_key}_cancel"):
        st.session_state.pop(pending_key, None)
        st.session_state["scheduler_dismissed_cell"] = cell_key
        pb_rerun()
    return True


def render_edit_clash_choices(pending_key: str) -> bool:
    """Resolve a clash caused by moving or extending an existing booking."""
    item = st.session_state.get(pending_key)
    if not item:
        return False
    existing = overlapping_assignment_rows(
        int(item["employee_id"]), to_date(item["work_date"]),
        time_value(item["start_time"]), time_value(item["finish_time"], time(15, 0)),
        int(item["assignment_id"]),
    )
    st.error("These edited times overlap another booking.")
    comparison_rows = [
        {
            "Booking": "Existing",
            "Job / Stage": f"{row['job_no']} · {row['job_name']} — {row['stage_name']}",
            "Time": f"{row['start_time']}–{row['finish_time']}",
            "Role": row["site_role"],
        }
        for _, row in existing.iterrows()
    ]
    comparison_rows.append({
        "Booking": "Edited selection",
        "Job / Stage": item["job_label"],
        "Time": f"{item['start_time']}–{item['finish_time']}",
        "Role": item["site_role"],
    })
    st.dataframe(pd.DataFrame(comparison_rows), width="stretch", hide_index=True)
    choice = st.radio(
        "Which one should be kept?",
        ["Keep existing booking and cancel this edit", "Keep edited booking and remove the clash"],
        key=f"{pending_key}_choice",
    )
    keep_col, cancel_col = st.columns(2)
    if keep_col.button("Apply choice", type="primary", width="stretch", key=f"{pending_key}_apply"):
        if choice == "Keep existing booking and cancel this edit":
            result = (True, "Kept the existing booking; the edited booking was not changed.")
        else:
            result = replace_conflicts_for_assignment_edit(
                item["expected_conflict_ids"], int(item["assignment_id"]),
                int(item["employee_id"]), int(item["job_id"]),
                int(item["job_stage_id"]) if item.get("job_stage_id") is not None else None,
                to_date(item["work_date"]), time_value(item["start_time"]),
                time_value(item["finish_time"], time(15, 0)), float(item["planned_hours"]),
                str(item["site_role"]), str(item.get("notes", "")),
                bool(item.get("linked_to_job_dates", True)),
            )
        st.session_state.pop(pending_key, None)
        st.session_state["scheduler_clash_result"] = {
            "messages": [result[1]] if result[0] else [],
            "errors": [] if result[0] else [result[1]],
        }
        pb_rerun()
    if cancel_col.button("Close without changes", width="stretch", key=f"{pending_key}_cancel"):
        st.session_state.pop(pending_key, None)
        pb_rerun()
    return True


@st.dialog("Choose a job", dismissible=False)
def schedule_tile_dialog(
    employee_id: int,
    staff_name: str,
    work_day: date,
    existing: pd.DataFrame,
    jobs: pd.DataFrame,
    job_stage_choices: dict[str, dict],
    user: dict,
    cell_key: str,
) -> None:
    """Open the minimal employee/day job picker requested for the tile board."""
    st.markdown(f"**{staff_name} · {work_day.strftime('%A %d %B')}**")
    pending_key = f"scheduler_tile_clashes_{cell_key}"
    if render_tile_clash_choices(pending_key, cell_key):
        return
    if not existing.empty:
        st.caption("Already scheduled")
        for _, booking in existing.iterrows():
            details, remove = st.columns([4, 1])
            details.write(
                f"{booking['job_no']} · {booking['job_name']} — {booking['stage_name']} "
                f"({booking['start_time']}–{booking['finish_time']})"
            )
            if remove.button(
                "Delete",
                key=f"tile_popup_delete_{int(booking['id'])}",
                width="stretch",
            ):
                execute("DELETE FROM staff_schedule WHERE id=?", (int(booking["id"]),))
                st.session_state["scheduler_dismissed_cell"] = cell_key
                pb_success(f"Deleted {staff_name}'s booking for {work_day.strftime('%d %b')}.")
                pb_rerun()

    selected_job_stage = st.selectbox(
        "Select job / stage",
        list(job_stage_choices.keys()),
        key=f"tile_popup_job_{employee_id}_{work_day.isoformat()}",
    )
    lead_crew = crew_for_lead(employee_id)
    include_saved_crew = False
    if lead_crew:
        member_ids = [int(value) for value in lead_crew.get("member_ids", [])]
        available_members = active_staff()
        crew_members = available_members[available_members["id"].astype(int).isin(member_ids)]
        other_names = crew_members[crew_members["id"].astype(int) != int(employee_id)]["name"].astype(str).tolist()
        if other_names:
            include_saved_crew = st.checkbox(
                f"Also include {lead_crew['crew_name']}: {', '.join(other_names)}",
                value=False,
                key=f"tile_popup_include_crew_{employee_id}_{work_day.isoformat()}",
                help=f"Schedule {staff_name} alone, or add every available member of this saved crew.",
            )
    st.caption("Standard shift: 7:00 am–3:00 pm, zero break, 8.0 hours.")
    add_col, close_col = st.columns(2)
    if add_col.button(
        "Add to job",
        key=f"tile_popup_add_{employee_id}_{work_day.isoformat()}",
        type="primary",
        width="stretch",
    ):
        selection = job_stage_choices[selected_job_stage]
        available_staff = active_staff()
        target_ids = [int(employee_id)]
        if include_saved_crew and lead_crew:
            target_ids = [
                int(value)
                for value in lead_crew.get("member_ids", [])
                if int(value) in set(available_staff["id"].astype(int).tolist())
            ]
            if int(employee_id) not in target_ids:
                target_ids.insert(0, int(employee_id))
        added = []
        blocked = []
        clashes = []
        for target_id in target_ids:
            target_name = str(available_staff.loc[available_staff["id"].astype(int) == target_id, "name"].iloc[0])
            if has_approved_leave(target_id, work_day):
                blocked.append(f"{target_name}: approved leave")
                continue
            conflicts = overlapping_assignment_rows(target_id, work_day, time(7, 0), time(15, 0))
            if not conflicts.empty:
                clashes.append(
                    {
                        "employee_id": target_id,
                        "staff_name": target_name,
                        "expected_conflict_ids": conflicts["id"].astype(int).tolist(),
                        "job_id": int(selection["job_id"]),
                        "job_stage_id": selection["job_stage_id"],
                        "job_label": selected_job_stage,
                        "work_date": work_day.isoformat(),
                        "start_time": "07:00",
                        "finish_time": "15:00",
                        "planned_hours": 8.0,
                        "site_role": "Site Work",
                        "notes": "",
                        "created_by": str(user.get("username", "")),
                        "linked_to_job_dates": True,
                    }
                )
                continue
            ok, message = add_assignment(
                target_id, int(selection["job_id"]), selection["job_stage_id"],
                work_day, time(7, 0), time(15, 0), 8.0, "Site Work", "",
                str(user.get("username", "")), True,
            )
            (added if ok else blocked).append(target_name if ok else f"{target_name}: {message}")
        if clashes:
            st.session_state[pending_key] = clashes
            st.session_state["scheduler_clash_result"] = {
                "messages": [f"Added: {', '.join(added)}"] if added else [],
                "errors": [f"Not added: {', '.join(blocked)}"] if blocked else [],
            }
            pb_rerun()
        elif added:
            st.session_state["scheduler_dismissed_cell"] = cell_key
            if blocked:
                st.session_state["scheduler_clash_result"] = {
                    "messages": [f"Added: {', '.join(added)}"],
                    "errors": [f"Not added: {', '.join(blocked)}"],
                }
            pb_rerun()
        elif blocked:
            pb_error(" | ".join(blocked))
    if close_col.button(
        "Close",
        key=f"tile_popup_close_{employee_id}_{work_day.isoformat()}",
        width="stretch",
    ):
        st.session_state["scheduler_dismissed_cell"] = cell_key
        pb_rerun()


def clickable_schedule_board(
    assignments: pd.DataFrame,
    start: date,
    end: date,
    staff: pd.DataFrame,
    jobs: pd.DataFrame,
    job_stage_choices: dict[str, dict],
    role_options: list[str],
    user: dict,
) -> None:
    """Render a cell-selectable board whose tiles open a job picker popup."""
    days = list(daterange(start, end))
    day_lookup = {day.strftime("%a %d %b"): day for day in days}
    grid = schedule_grid(assignments, start, end, staff)
    grid.columns = [to_date(day).strftime("%a %d %b") for day in days]
    board = grid.reset_index().rename(columns={"index": "Employee"})

    st.caption("Click any employee/day tile, then choose the job in the popup.")
    event = st.dataframe(
        board,
        width="stretch",
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
    existing = assignments[
        (assignments["employee_id"].astype(int) == employee_id)
        & (assignments["schedule_date"].astype(str) == work_day.isoformat())
    ]
    cell_key = f"{employee_id}:{work_day.isoformat()}"
    if st.session_state.get("scheduler_dismissed_cell") != cell_key:
        schedule_tile_dialog(
            employee_id,
            staff_name,
            work_day,
            existing,
            jobs,
            job_stage_choices,
            user,
            cell_key,
        )


def timeline_chart(assignments: pd.DataFrame, title: str):
    if assignments.empty:
        return None
    data = assignments.copy()
    data["Start"] = pd.to_datetime(data["schedule_date"] + " " + data["start_time"])
    data["Finish"] = pd.to_datetime(data["schedule_date"] + " " + data["finish_time"])
    data["Job"] = (
        data["job_no"].astype(str)
        + " · "
        + data["job_name"].astype(str)
        + " — "
        + data["stage_name"].astype(str)
    )
    data["Details"] = data["site_role"].astype(str) + " · " + data["hours"].map(lambda x: f"{x:.1f}h")
    fig = px.timeline(
        data,
        x_start="Start",
        x_end="Finish",
        y="staff",
        color="Job",
        hover_data=["job_no", "job_name", "stage_name", "builder", "address", "Details", "notes"],
        custom_data=["id"],
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


def selected_timeline_assignment_id(event) -> int | None:
    """Extract the JobHub assignment id from a selected Plotly timeline box."""
    if event is None:
        return None
    selection = getattr(event, "selection", None)
    if selection is None and isinstance(event, dict):
        selection = event.get("selection")
    if selection is None:
        return None
    points = getattr(selection, "points", None)
    if points is None and isinstance(selection, dict):
        points = selection.get("points")
    if not points:
        return None
    point = points[0]
    custom_data = point.get("customdata") if isinstance(point, dict) else getattr(point, "customdata", None)
    if not custom_data:
        return None
    try:
        return int(custom_data[0])
    except (TypeError, ValueError, IndexError):
        return None


def timeline_booking_editor(
    assignment_id: int,
    assignments: pd.DataFrame,
    staff: pd.DataFrame,
    jobs: pd.DataFrame,
    job_stage_choices: dict[str, dict],
    role_options: list[str],
) -> None:
    """Quick edit/delete panel opened by clicking a timeline booking box."""
    selected_rows = assignments[assignments["id"].astype(int) == int(assignment_id)]
    if selected_rows.empty:
        st.warning("That booking is no longer in the displayed date range.")
        return
    row = selected_rows.iloc[0]
    pending_key = f"timeline_edit_clash_{assignment_id}"
    if render_edit_clash_choices(pending_key):
        return
    staff_names = staff["name"].tolist()
    job_labels = list(job_stage_choices.keys())
    current_job = f"{row['job_no']} · {row['job_name']} — {row['stage_name']}"

    st.markdown(
        f"#### Selected booking · {row['staff']} · "
        f"{to_date(row['schedule_date']).strftime('%A %d %B')}"
    )
    with st.form(f"timeline_edit_{assignment_id}"):
        e1, e2, e3 = st.columns(3)
        edit_staff = e1.selectbox(
            "Employee",
            staff_names,
            index=staff_names.index(row["staff"]),
            key=f"timeline_staff_{assignment_id}",
        )
        edit_date = e1.date_input(
            "Date",
            value=to_date(row["schedule_date"]),
            key=f"timeline_date_{assignment_id}",
        )
        edit_job = e2.selectbox(
            "Job / stage",
            job_labels,
            index=job_labels.index(current_job) if current_job in job_labels else 0,
            key=f"timeline_job_{assignment_id}",
        )
        edit_role = e2.selectbox(
            "Role / type",
            role_options,
            index=role_options.index(row["site_role"]) if row["site_role"] in role_options else 0,
            key=f"timeline_role_{assignment_id}",
        )
        edit_start = e3.time_input(
            "Start",
            value=time_value(row["start_time"]),
            key=f"timeline_start_{assignment_id}",
        )
        edit_finish = e3.time_input(
            "Finish",
            value=time_value(row["finish_time"], time(15, 0)),
            key=f"timeline_finish_{assignment_id}",
        )
        edit_hours = e3.number_input(
            "Hours",
            min_value=0.25,
            max_value=24.0,
            value=float(row["hours"]),
            step=0.25,
            key=f"timeline_hours_{assignment_id}",
        )
        edit_linked = st.checkbox(
            "Keep linked to job start date",
            value=bool(int(row.get("linked_to_job_dates") or 0)),
            key=f"timeline_linked_{assignment_id}",
        )
        edit_notes = st.text_input(
            "Notes",
            value=str(row["notes"] or ""),
            key=f"timeline_notes_{assignment_id}",
        )
        save = st.form_submit_button(
            "Save selected booking",
            type="primary",
            width="stretch",
        )
    if save:
        employee_id = int(staff.loc[staff["name"] == edit_staff, "id"].iloc[0])
        selected_job_stage = job_stage_choices[edit_job]
        job_id = int(selected_job_stage["job_id"])
        job_stage_id = selected_job_stage["job_stage_id"]
        if edit_finish <= edit_start:
            pb_error("Finish time must be after start time.")
        elif has_approved_leave(employee_id, to_date(edit_date)):
            pb_error("This staff member is on approved leave.")
        elif overlapping_assignment(employee_id, to_date(edit_date), edit_start, edit_finish, assignment_id):
            conflicts = overlapping_assignment_rows(
                employee_id, to_date(edit_date), edit_start, edit_finish, assignment_id,
            )
            st.session_state[pending_key] = {
                "assignment_id": int(assignment_id), "employee_id": employee_id,
                "staff_name": edit_staff, "expected_conflict_ids": conflicts["id"].astype(int).tolist(),
                "job_id": job_id, "job_stage_id": job_stage_id, "job_label": edit_job,
                "work_date": to_date(edit_date).isoformat(),
                "start_time": edit_start.strftime("%H:%M"),
                "finish_time": edit_finish.strftime("%H:%M"),
                "planned_hours": float(edit_hours), "site_role": edit_role,
                "notes": edit_notes, "linked_to_job_dates": bool(edit_linked),
            }
            pb_rerun()
        else:
            job_start_value = jobs.loc[jobs["id"] == job_id, "start_date"]
            job_start = (
                to_date(job_start_value.iloc[0])
                if edit_linked and not job_start_value.empty and str(job_start_value.iloc[0] or "").strip()
                else None
            )
            execute(
                """
                UPDATE staff_schedule
                SET employee_id=?,job_id=?,job_stage_id=?,schedule_date=?,start_time=?,finish_time=?,planned_hours=?,
                    site_role=?,notes=?,period_start=?,period_end=?,linked_to_job_dates=?,
                    job_day_offset=?,last_job_start_date=?
                WHERE id=?
                """,
                (
                    employee_id,
                    job_id,
                    job_stage_id,
                    to_date(edit_date).isoformat(),
                    edit_start.strftime("%H:%M"),
                    edit_finish.strftime("%H:%M"),
                    float(edit_hours),
                    edit_role,
                    edit_notes.strip(),
                    to_date(edit_date).isoformat(),
                    to_date(edit_date).isoformat(),
                    1 if job_start else 0,
                    (to_date(edit_date) - job_start).days if job_start else None,
                    job_start.isoformat() if job_start else None,
                    int(assignment_id),
                ),
            )
            pb_success("Selected booking updated.")
            pb_rerun()

    if st.button(
        "Delete selected booking",
        key=f"timeline_delete_{assignment_id}",
        width="stretch",
    ):
        execute("DELETE FROM staff_schedule WHERE id=?", (int(assignment_id),))
        pb_success("Selected booking deleted.")
        pb_rerun()


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
        st.sidebar.link_button("Open JobHub", JOBHUB_URL, width="stretch")
    if st.sidebar.button("Sign out", width="stretch"):
        st.session_state.pop("linked_user", None)
        pb_rerun()
    st.sidebar.caption(f"Version {APP_VERSION}")
    st.sidebar.caption("Database: shared PostgreSQL" if USE_POSTGRES else f"Database: {SQLITE_PATH}")
    return page


def page_dashboard() -> None:
    st.title("Staff Scheduler Dashboard")
    selected = st.date_input("Week commencing", value=week_start(jobhub_today()))
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
        st.plotly_chart(chart, width="stretch")
    else:
        st.info("No JobHub schedule entries have been entered for this week.")

    left, right = st.columns([1.15, 1])
    with left:
        st.subheader("Weekly schedule grid")
        st.dataframe(schedule_grid(assignments, start, end, staff), width="stretch", height=max(280, 80 + len(staff) * 38))
    with right:
        fig, capacity = workload_chart(assignments, staff, start, end)
        st.plotly_chart(fig, width="stretch")
        available = capacity[capacity["Allocated hours"] < capacity["Target hours"] - 0.01]
        if not available.empty:
            st.caption(
                "Available capacity: "
                + ", ".join(
                    f"{row['name']} {row['Target hours'] - row['Allocated hours']:.1f}h"
                    for _, row in available.iterrows()
                )
            )


def render_crew_management(staff: pd.DataFrame) -> None:
    """Create saved lead/member groups such as Ian with River and Salty."""
    st.markdown("### Saved crews")
    st.caption(
        "Set one employee as the lead and save the regular members. Clicking that lead's "
        "tile will then ask whether to schedule the lead alone or the whole crew."
    )
    crews = saved_crews(active_only=False)
    staff_names = staff["name"].astype(str).tolist()
    if not crews.empty:
        display = crews[["crew_name", "lead_name", "member_names", "active", "notes"]].rename(
            columns={
                "crew_name": "Crew", "lead_name": "Lead", "member_names": "Members",
                "active": "Active", "notes": "Notes",
            }
        )
        st.dataframe(display, width="stretch", hide_index=True)

    mode_options = ["Create new crew"] + (
        [f"#{int(row['id'])} · {row['crew_name']}" for _, row in crews.iterrows()]
        if not crews.empty else []
    )
    mode = st.selectbox("Crew record", mode_options, key="scheduler_crew_record")
    selected_crew = None
    crew_id = None
    if mode != "Create new crew":
        crew_id = int(mode.split(" · ", 1)[0].replace("#", ""))
        selected_crew = crews[crews["id"].astype(int) == crew_id].iloc[0]

    current_lead = str(selected_crew["lead_name"]) if selected_crew is not None else staff_names[0]
    if current_lead not in staff_names:
        st.warning("This crew's lead is inactive. Choose an active lead before saving.")
        current_lead = staff_names[0]
    current_members = (
        [
            name for name in str(selected_crew["member_names"] or "").split(", ")
            if name in staff_names and name != current_lead
        ]
        if selected_crew is not None else []
    )
    with st.form(f"scheduler_crew_form_{crew_id or 'new'}"):
        crew_name = st.text_input(
            "Crew name",
            value=str(selected_crew["crew_name"]) if selected_crew is not None else "",
            placeholder="Ian's crew",
        )
        lead_name = st.selectbox("Crew lead", staff_names, index=staff_names.index(current_lead))
        member_names = st.multiselect(
            "Other crew members",
            [name for name in staff_names if name != lead_name],
            default=[name for name in current_members if name != lead_name],
            help="The lead is included automatically.",
        )
        crew_notes = st.text_area(
            "Notes",
            value=str(selected_crew["notes"] or "") if selected_crew is not None else "",
        )
        save_crew = st.form_submit_button("Save crew", type="primary", width="stretch")
    if save_crew:
        lead_id = int(staff.loc[staff["name"] == lead_name, "id"].iloc[0])
        member_ids = staff[staff["name"].isin(member_names)]["id"].astype(int).tolist()
        duplicate_name = scalar(
            "SELECT id FROM scheduler_crews WHERE LOWER(TRIM(crew_name))=LOWER(TRIM(?)) AND id<>?",
            (crew_name.strip(), int(crew_id or 0)), None,
        )
        duplicate_lead = scalar(
            "SELECT id FROM scheduler_crews WHERE lead_employee_id=? AND id<>?",
            (lead_id, int(crew_id or 0)), None,
        )
        if not crew_name.strip():
            pb_error("Crew name is required.")
        elif duplicate_name:
            pb_error("A saved crew already uses that name.")
        elif duplicate_lead:
            pb_error(f"{lead_name} is already the lead of another saved crew.")
        else:
            save_scheduler_crew(crew_id, crew_name, lead_id, member_ids, crew_notes)
            pb_success(f"Saved {crew_name.strip()} with {lead_name} as lead.")
            pb_rerun()

    if selected_crew is not None:
        delete_confirmed = st.checkbox(
            f"I understand this removes the saved group {selected_crew['crew_name']} (not its employees).",
            key=f"scheduler_delete_crew_confirm_{crew_id}",
        )
        if st.button(
            "Delete saved crew", disabled=not delete_confirmed, width="stretch",
            key=f"scheduler_delete_crew_{crew_id}",
        ):
            delete_scheduler_crew(int(crew_id))
            pb_success(f"Deleted saved crew {selected_crew['crew_name']}.")
            pb_rerun()


def page_schedule(user: dict) -> None:
    st.title("Schedule Board")
    staff = active_staff()
    jobs = schedulable_jobs()
    if staff.empty or jobs.empty:
        st.warning("Add active employees and open jobs in JobHub before scheduling.")
        return

    c1, c2 = st.columns(2)
    selected = c1.date_input("Board week", value=week_start(jobhub_today()))
    display_days = c2.selectbox("Board range", [7, 14, 21, 28], index=1, format_func=lambda x: f"{x} days")
    start = week_start(to_date(selected))
    end = start + timedelta(days=display_days - 1)
    result = st.session_state.pop("scheduler_clash_result", None)
    if result:
        if result.get("messages"):
            pb_success(" | ".join(result["messages"]))
        if result.get("errors"):
            pb_error(" | ".join(result["errors"]))
    tabs = st.tabs(["Clickable tile board", "Add assignment", "Allocate crew", "Saved crews", "Edit / delete"])
    staff_names = staff["name"].tolist()
    job_stage_choices = schedulable_job_stage_choices(jobs)
    job_labels = list(job_stage_choices.keys())
    role_options = ["Site Work", "Leading Hand", "Supervision", "Quote / Measure", "Office / Planning", "Training", "Touch-ups", "Other"]

    with tabs[0]:
        copy_pending_key = "scheduler_copy_week_clashes"
        render_tile_clash_choices(copy_pending_key, "copy-week")
        assignments = assignment_rows(start, end)
        leaves = leave_rows(start, end)
        alerts = conflict_report(assignments, leaves)
        if alerts:
            st.warning("Warnings: " + " | ".join(alerts[:4]) + (" …" if len(alerts) > 4 else ""))
        st.markdown("### Staff × Day tile board")
        st.caption(
            "Every day tile is selectable. Click an employee's tile to add a job, "
            "view that day's bookings, or delete a booking."
        )
        clickable_schedule_board(
            assignments,
            start,
            end,
            staff,
            jobs,
            job_stage_choices,
            role_options,
            user,
        )
        with st.expander("View coloured schedule timeline"):
            chart = timeline_chart(assignments, f"JobHub schedule · {start.strftime('%d %b')} to {end.strftime('%d %b %Y')}")
            if chart:
                st.caption("Click any coloured booking box to open and edit or delete it.")
                timeline_event = st.plotly_chart(
                    chart,
                    width="stretch",
                    on_select="rerun",
                    selection_mode="points",
                    key="clickable_schedule_timeline",
                )
                selected_assignment_id = selected_timeline_assignment_id(timeline_event)
                if selected_assignment_id is not None:
                    timeline_booking_editor(
                        selected_assignment_id,
                        assignments,
                        staff,
                        jobs,
                        job_stage_choices,
                        role_options,
                    )
            else:
                st.info("Nothing is scheduled in this date range.")
        b1, b2 = st.columns(2)
        with b1:
            if st.button("Copy first week to next week", width="stretch"):
                source = assignment_rows(start, start + timedelta(days=6))
                added = skipped = 0
                copy_clashes: list[dict] = []
                for _, row in source.iterrows():
                    copy_date = to_date(row["schedule_date"]) + timedelta(days=7)
                    copy_start = time_value(row["start_time"])
                    copy_finish = time_value(row["finish_time"], time(15, 0))
                    conflicts = overlapping_assignment_rows(
                        int(row["employee_id"]), copy_date, copy_start, copy_finish,
                    )
                    if not conflicts.empty:
                        copy_clashes.append({
                            "employee_id": int(row["employee_id"]),
                            "staff_name": str(row["staff"]),
                            "expected_conflict_ids": conflicts["id"].astype(int).tolist(),
                            "job_id": int(row["job_id"]),
                            "job_stage_id": int(row["job_stage_id"]) if pd.notna(row["job_stage_id"]) else None,
                            "job_label": f"{row['job_no']} · {row['job_name']} — {row['stage_name']}",
                            "work_date": copy_date.isoformat(),
                            "start_time": copy_start.strftime("%H:%M"),
                            "finish_time": copy_finish.strftime("%H:%M"),
                            "planned_hours": float(row["hours"]),
                            "site_role": str(row["site_role"]),
                            "notes": str(row["notes"] or ""),
                            "created_by": str(user.get("username", "")),
                            "linked_to_job_dates": True,
                        })
                        continue
                    ok, _ = add_assignment(
                        int(row["employee_id"]),
                        int(row["job_id"]),
                        int(row["job_stage_id"]) if pd.notna(row["job_stage_id"]) else None,
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
                if copy_clashes:
                    st.session_state[copy_pending_key] = copy_clashes
                    st.session_state["scheduler_clash_result"] = {
                        "messages": [f"Copied {added} non-conflicting assignment(s)."] if added else [],
                        "errors": [f"Skipped {skipped} leave or validation issue(s)."] if skipped else [],
                    }
                    pb_rerun()
                pb_success(f"Copied {added} assignment(s); skipped {skipped} leave or validation issue(s).")
                pb_rerun()
        with b2:
            csv = assignments.to_csv(index=False).encode("utf-8")
            st.download_button(
                "Download displayed schedule CSV",
                csv,
                f"PB_JobHub_schedule_{start.isoformat()}_{end.isoformat()}.csv",
                "text/csv",
                width="stretch",
            )

    with tabs[1]:
        manual_pending_key = "scheduler_manual_assignment_clashes"
        render_tile_clash_choices(manual_pending_key, "manual-assignment")
        with st.form("add_linked_assignment"):
            a1, a2, a3 = st.columns(3)
            staff_name = a1.selectbox("Staff member", staff_names)
            work_day = a1.date_input("Date", value=start)
            job_label = a2.selectbox("Job / stage", job_labels)
            site_role = a2.selectbox("Role / type", role_options)
            start_time = a3.time_input("Start", value=time(7, 0))
            finish_time = a3.time_input("Finish", value=time(15, 0))
            hours = a3.number_input("Allocated hours", min_value=0.25, max_value=24.0, value=8.0, step=0.25)
            linked_dates = st.checkbox(
                "Keep this assignment linked to the job start date",
                value=True,
                help="If the job start date changes, this assignment moves by the same number of days.",
            )
            notes = st.text_area("Notes", placeholder="Access, supervisor, equipment or special instructions")
            save = st.form_submit_button("Add to JobHub schedule", type="primary", width="stretch")
        if save:
            employee_id = int(staff.loc[staff["name"] == staff_name, "id"].iloc[0])
            selected_job_stage = job_stage_choices[job_label]
            conflicts = overlapping_assignment_rows(
                employee_id, to_date(work_day), start_time, finish_time,
            )
            if not conflicts.empty:
                st.session_state[manual_pending_key] = [{
                    "employee_id": employee_id,
                    "staff_name": staff_name,
                    "expected_conflict_ids": conflicts["id"].astype(int).tolist(),
                    "job_id": int(selected_job_stage["job_id"]),
                    "job_stage_id": selected_job_stage["job_stage_id"],
                    "job_label": job_label,
                    "work_date": to_date(work_day).isoformat(),
                    "start_time": start_time.strftime("%H:%M"),
                    "finish_time": finish_time.strftime("%H:%M"),
                    "planned_hours": float(hours),
                    "site_role": site_role,
                    "notes": notes,
                    "created_by": str(user.get("username", "")),
                    "linked_to_job_dates": bool(linked_dates),
                }]
                pb_rerun()
            else:
                ok, message = add_assignment(
                    employee_id, int(selected_job_stage["job_id"]),
                    selected_job_stage["job_stage_id"], to_date(work_day), start_time,
                    finish_time, hours, site_role, notes, str(user.get("username", "")),
                    linked_dates,
                )
                (pb_success if ok else pb_error)(message)
                if ok:
                    pb_rerun()

    with tabs[2]:
        bulk_pending_key = "scheduler_bulk_assignment_clashes"
        render_tile_clash_choices(bulk_pending_key, "bulk-assignment")
        with st.form("bulk_linked_assignment"):
            crews = saved_crews(active_only=True)
            crew_source_options = ["Choose names manually"] + (
                [f"#{int(row['id'])} · {row['crew_name']}" for _, row in crews.iterrows()]
                if not crews.empty else []
            )
            crew_source = st.selectbox("Saved crew", crew_source_options)
            default_crew = []
            source_key = "manual"
            if crew_source != "Choose names manually":
                source_id = int(crew_source.split(" · ", 1)[0].replace("#", ""))
                source_key = str(source_id)
                source_row = crews[crews["id"].astype(int) == source_id].iloc[0]
                default_crew = [
                    name for name in str(source_row["member_names"] or "").split(", ")
                    if name in staff_names
                ]
            crew = st.multiselect(
                "Crew members",
                staff_names,
                default=default_crew,
                key=f"bulk_crew_members_{source_key}",
            )
            job_label = st.selectbox("Job / stage", job_labels, key="bulk_job")
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
            hours = c3.number_input("Hours per person / day", min_value=0.25, max_value=24.0, value=8.0, step=0.25)
            linked_dates = st.checkbox(
                "Keep crew dates linked to the job start date",
                value=True,
                key="bulk_linked_dates",
            )
            notes = st.text_area("Crew notes")
            save_bulk = st.form_submit_button("Allocate crew", type="primary", width="stretch")
        if save_bulk:
            if not crew:
                pb_error("Select at least one employee.")
            elif to_date(range_end) < to_date(range_start):
                pb_error("End date must be on or after start date.")
            else:
                day_numbers = {name: number for number, name in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])}
                selected_day_numbers = {day_numbers[name] for name in weekdays}
                selected_job_stage = job_stage_choices[job_label]
                job_id = int(selected_job_stage["job_id"])
                job_stage_id = selected_job_stage["job_stage_id"]
                added = 0
                skipped: list[str] = []
                clash_items: list[dict] = []
                for work_day in daterange(to_date(range_start), to_date(range_end)):
                    if work_day.weekday() not in selected_day_numbers:
                        continue
                    for staff_name in crew:
                        employee_id = int(staff.loc[staff["name"] == staff_name, "id"].iloc[0])
                        conflicts = overlapping_assignment_rows(
                            employee_id, work_day, start_time, finish_time,
                        )
                        if not conflicts.empty:
                            clash_items.append({
                                "employee_id": employee_id,
                                "staff_name": staff_name,
                                "expected_conflict_ids": conflicts["id"].astype(int).tolist(),
                                "job_id": job_id,
                                "job_stage_id": job_stage_id,
                                "job_label": job_label,
                                "work_date": work_day.isoformat(),
                                "start_time": start_time.strftime("%H:%M"),
                                "finish_time": finish_time.strftime("%H:%M"),
                                "planned_hours": float(hours),
                                "site_role": site_role,
                                "notes": notes,
                                "created_by": str(user.get("username", "")),
                                "linked_to_job_dates": bool(linked_dates),
                            })
                            continue
                        ok, message = add_assignment(
                            employee_id,
                            job_id,
                            job_stage_id,
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
                if clash_items:
                    st.session_state[bulk_pending_key] = clash_items
                    st.session_state["scheduler_clash_result"] = {
                        "messages": [f"Added {added} non-conflicting schedule entries."] if added else [],
                        "errors": skipped,
                    }
                    pb_rerun()
                pb_success(f"Added {added} JobHub schedule entries.")
                if skipped:
                    st.warning("Skipped:\n\n" + "\n\n".join(f"• {item}" for item in skipped[:15]))
                if added:
                    pb_rerun()

    with tabs[3]:
        render_crew_management(staff)

    with tabs[4]:
        assignments = assignment_rows(start, end)
        if assignments.empty:
            st.info("There are no assignments in this range.")
        else:
            labels = assignments.apply(
                lambda row: (
                    f"#{row['id']} · {to_date(row['schedule_date']).strftime('%a %d %b')} · "
                    f"{row['staff']} · {row['job_no']} {row['job_name']} — {row['stage_name']}"
                ),
                axis=1,
            ).tolist()
            selected_label = st.selectbox("Assignment", labels)
            assignment_id = int(selected_label.split(" · ", 1)[0].replace("#", ""))
            row = assignments[assignments["id"] == assignment_id].iloc[0]
            edit_pending_key = f"schedule_tab_edit_clash_{assignment_id}"
            if render_edit_clash_choices(edit_pending_key):
                return
            with st.form("edit_linked_assignment"):
                e1, e2, e3 = st.columns(3)
                edit_staff = e1.selectbox("Staff member", staff_names, index=staff_names.index(row["staff"]))
                edit_date = e1.date_input("Date", value=to_date(row["schedule_date"]))
                current_job = f"{row['job_no']} · {row['job_name']} — {row['stage_name']}"
                edit_job = e2.selectbox(
                    "Job / stage",
                    job_labels,
                    index=job_labels.index(current_job) if current_job in job_labels else 0,
                )
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
                update = st.form_submit_button("Save changes", type="primary", width="stretch")
            if update:
                employee_id = int(staff.loc[staff["name"] == edit_staff, "id"].iloc[0])
                selected_job_stage = job_stage_choices[edit_job]
                job_id = int(selected_job_stage["job_id"])
                job_stage_id = selected_job_stage["job_stage_id"]
                if edit_finish <= edit_start:
                    pb_error("Finish time must be after start time.")
                elif has_approved_leave(employee_id, to_date(edit_date)):
                    pb_error("This staff member is on approved leave.")
                elif overlapping_assignment(employee_id, to_date(edit_date), edit_start, edit_finish, assignment_id):
                    conflicts = overlapping_assignment_rows(
                        employee_id, to_date(edit_date), edit_start, edit_finish, assignment_id,
                    )
                    st.session_state[edit_pending_key] = {
                        "assignment_id": assignment_id, "employee_id": employee_id,
                        "staff_name": edit_staff,
                        "expected_conflict_ids": conflicts["id"].astype(int).tolist(),
                        "job_id": job_id, "job_stage_id": job_stage_id,
                        "job_label": edit_job, "work_date": to_date(edit_date).isoformat(),
                        "start_time": edit_start.strftime("%H:%M"),
                        "finish_time": edit_finish.strftime("%H:%M"),
                        "planned_hours": float(edit_hours), "site_role": edit_role,
                        "notes": edit_notes, "linked_to_job_dates": bool(edit_linked),
                    }
                    pb_rerun()
                else:
                    execute(
                        """
                        UPDATE staff_schedule
                        SET employee_id=?,job_id=?,job_stage_id=?,schedule_date=?,start_time=?,finish_time=?,planned_hours=?,
                            site_role=?,notes=?,period_start=?,period_end=?,linked_to_job_dates=?,
                            job_day_offset=?,last_job_start_date=?
                        WHERE id=?
                        """,
                        (
                            employee_id,
                            job_id,
                            job_stage_id,
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
            if st.button("Delete selected assignment", width="stretch"):
                execute("DELETE FROM staff_schedule WHERE id=?", (assignment_id,))
                pb_success("JobHub schedule entry deleted.")
                pb_rerun()


def page_leave(user: dict) -> None:
    st.title("Leave Requests")
    staff = active_staff()
    if staff.empty:
        st.info("Add staff members before requesting leave.")
        return
    tab_add, tab_review, tab_register = st.tabs(["Add request", "Approve / reject", "Leave register"])
    with tab_add:
        with st.form("manager_leave_request"):
            employee_name = st.selectbox("Employee", staff["name"].tolist())
            c1, c2, c3 = st.columns(3)
            start_date = c1.date_input("Start date", value=jobhub_today())
            end_date = c2.date_input("End date", value=jobhub_today())
            leave_type = c3.selectbox("Leave type", ["Annual Leave", "Personal Leave", "RDO", "Unpaid Leave", "Other"])
            reason = st.text_area("Reason / notes")
            status = st.selectbox("Initial status", ["Pending", "Approved"], index=0)
            save = st.form_submit_button("Save leave request", type="primary", width="stretch")
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
                        jobhub_now().isoformat(timespec="seconds") if status == "Approved" else None,
                        jobhub_now().isoformat(timespec="seconds"),
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
            if c1.button("Approve", type="primary", width="stretch"):
                execute(
                    "UPDATE staff_leave_requests SET status='Approved',reviewed_by=?,reviewed_at=? WHERE id=?",
                    (str(user.get("username", "")), jobhub_now().isoformat(timespec="seconds"), request_id),
                )
                pb_success("Leave approved.")
                pb_rerun()
            if c2.button("Reject", width="stretch"):
                execute(
                    "UPDATE staff_leave_requests SET status='Rejected',reviewed_by=?,reviewed_at=? WHERE id=?",
                    (str(user.get("username", "")), jobhub_now().isoformat(timespec="seconds"), request_id),
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
        st.dataframe(records, width="stretch", hide_index=True)


def page_crew_suggestions(user: dict) -> None:
    st.title("Crew Suggestions")
    result = st.session_state.pop("scheduler_clash_result", None)
    if result:
        if result.get("messages"):
            pb_success(" | ".join(result["messages"]))
        if result.get("errors"):
            pb_error(" | ".join(result["errors"]))
    suggestion_pending_key = "scheduler_suggestion_clashes"
    render_tile_clash_choices(suggestion_pending_key, "crew-suggestion")
    st.caption(
        "JobHub compares job dates, estimator labour hours, existing allocations, leave, "
        "staff roles and current progress. Suggestions never alter the schedule until approved."
    )
    start = to_date(st.date_input("Suggestion period starts", value=jobhub_today()))
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
        required_hours = remaining_by_estimate if estimated > 0 else max(8.0, allocated)
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
        column_config={
            "Progress %": st.column_config.ProgressColumn(
                "Progress",
                min_value=0,
                max_value=100,
                format="%.0f%%",
            ),
            "Estimator hours": st.column_config.NumberColumn(format="%.1f h"),
            "Already scheduled": st.column_config.NumberColumn(format="%.1f h"),
            "Suggested remaining hours": st.column_config.NumberColumn(format="%.1f h"),
        },
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
        hours = c3.number_input("Hours per person/day", min_value=0.25, value=8.0, step=0.25)
        approve = st.form_submit_button("Approve and add suggested crew", type="primary")
    if approve:
        if not crew:
            pb_error("Select at least one crew member.")
            return
        added = skipped = 0
        suggestion_clashes: list[dict] = []
        for work_day in daterange(to_date(schedule_start), to_date(schedule_end)):
            if work_day.weekday() >= 5:
                continue
            for name in crew:
                employee_id = int(staff.loc[staff["name"] == name, "id"].iloc[0])
                conflicts = overlapping_assignment_rows(
                    employee_id, work_day, time(7, 0), time(15, 0),
                )
                if not conflicts.empty:
                    job_record = jobs[jobs["id"].astype(int) == int(job_id)].iloc[0]
                    suggestion_clashes.append({
                        "employee_id": employee_id, "staff_name": name,
                        "expected_conflict_ids": conflicts["id"].astype(int).tolist(),
                        "job_id": job_id, "job_stage_id": None,
                        "job_label": f"{job_record['job_no']} · {job_record['job_name']} — Whole Job",
                        "work_date": work_day.isoformat(), "start_time": "07:00",
                        "finish_time": "15:00", "planned_hours": float(hours),
                        "site_role": "Site Work", "notes": "JobHub approved crew suggestion",
                        "created_by": str(user.get("username", "")),
                        "linked_to_job_dates": True,
                    })
                    continue
                ok, _ = add_assignment(
                    employee_id, job_id, None, work_day, time(7, 0), time(15, 0),
                    float(hours), "Site Work", "JobHub approved crew suggestion",
                    str(user.get("username", "")), True,
                )
                added += int(ok)
                skipped += int(not ok)
        if suggestion_clashes:
            st.session_state[suggestion_pending_key] = suggestion_clashes
            st.session_state["scheduler_clash_result"] = {
                "messages": [f"Added {added} non-conflicting suggested entries."] if added else [],
                "errors": [f"Skipped {skipped} leave or validation issue(s)."] if skipped else [],
            }
            pb_rerun()
        pb_success(
            f"Suggestion approved: {added} schedule entries added; "
            f"{skipped} leave or validation issue(s) skipped."
        )
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
        st.dataframe(staff, width="stretch", hide_index=True)
    with tab2:
        st.caption("Create and edit jobs in JobHub. They appear here automatically.")
        st.dataframe(jobs, width="stretch", hide_index=True)
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
                save = st.form_submit_button("Save scheduler settings", type="primary", width="stretch")
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
                        (employee_id, target, notes.strip(), jobhub_now().isoformat(timespec="seconds")),
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
                        (employee_id, target, notes.strip(), jobhub_now().isoformat(timespec="seconds")),
                    )
                pb_success("Target hours saved.")
                pb_rerun()


def page_export() -> None:
    st.title("Export Shared Scheduling Data")
    start = st.date_input("Start date", value=week_start(jobhub_today()))
    end = st.date_input("End date", value=week_start(jobhub_today()) + timedelta(days=27))
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
        width="stretch",
    )
    c2.download_button(
        "Download leave CSV",
        leaves.to_csv(index=False).encode("utf-8"),
        f"PB_leave_{to_date(start).isoformat()}_{to_date(end).isoformat()}.csv",
        "text/csv",
        width="stretch",
    )
    st.dataframe(assignments, width="stretch", hide_index=True)


def page_my_schedule(user: dict) -> None:
    st.title("My Schedule")
    employee_id = user.get("employee_id")
    if not employee_id:
        st.warning("Your JobHub account is not linked to an employee record. Ask an administrator to link it.")
        return
    selected = st.date_input("Week commencing", value=week_start(jobhub_today()))
    start = week_start(to_date(selected))
    end = start + timedelta(days=13)
    assignments = assignment_rows(start, end, int(employee_id))
    chart = timeline_chart(assignments, f"My schedule · {start.strftime('%d %b')} to {end.strftime('%d %b %Y')}")
    if chart:
        st.plotly_chart(chart, width="stretch")
        display = assignments[["schedule_date", "start_time", "finish_time", "job_no", "job_name", "address", "site_role", "notes"]]
        st.dataframe(display, width="stretch", hide_index=True)
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
        start_date = c1.date_input("Start date", value=jobhub_today())
        end_date = c2.date_input("End date", value=jobhub_today())
        leave_type = c3.selectbox("Leave type", ["Annual Leave", "Personal Leave", "RDO", "Unpaid Leave", "Other"])
        reason = st.text_area("Reason / notes")
        submit = st.form_submit_button("Submit request", type="primary", width="stretch")
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
                    jobhub_now().isoformat(timespec="seconds"),
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
    st.dataframe(records, width="stretch", hide_index=True)


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
    start = c1.date_input("From", value=week_start(jobhub_today()), key=f"{key_prefix}_from")
    end = c2.date_input(
        "To",
        value=week_start(jobhub_today()) + timedelta(days=default_days),
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
            ["job_id", "job_no", "job_name", "stage_name", "builder", "address"],
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
        ["job_no", "job_name", "stage_name", "builder", "address", "Crew",
         "crew_count", "planned_hours", "first_date", "last_date"]
    ].copy()
    display.columns = [
        "Job No", "Job", "Stage", "Builder / Client", "Address", "Assigned Crew",
        "Crew Count", "Planned Hours", "First Date", "Last Date"
    ]
    st.subheader("Visual jobs → crew table")
    st.caption("Jobs are listed down the left. Each day shows the employees assigned to that job.")
    st.dataframe(
        job_crew_grid(assignments, start, end),
        width="stretch",
        hide_index=True,
        height=max(300, 80 + len(summary) * 48),
        key="visual_jobs_to_crew_grid",
    )

    st.subheader("Job allocation summary")
    st.dataframe(display, width="stretch", hide_index=True)

    st.subheader("Job-by-job detail")
    for _, job_row in summary.sort_values(["job_no", "job_name"]).iterrows():
        job_assignments = assignments[
            (assignments["job_id"] == job_row["job_id"])
            & (assignments["stage_name"] == job_row["stage_name"])
        ].copy()
        heading = (
            f"{job_row['job_no']} · {job_row['job_name']} — {job_row['stage_name']} — "
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
    st.subheader("Visual staff → jobs table")
    st.caption("Employees are listed down the left. Each day shows their job and planned hours.")
    st.dataframe(
        staff_job_grid(assignments, start, end, staff),
        width="stretch",
        hide_index=True,
        height=max(300, 80 + len(staff) * 48),
        key="visual_staff_to_jobs_grid",
    )

    st.subheader("Staff allocation summary")
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


def render_job_folder_schedule_editor(job_id: int, user: dict | None = None) -> None:
    """Render a clash-safe add/edit/delete scheduler scoped to one Job Folder."""
    user = user or {}
    role = str(user.get("role", "employee") or "employee").lower()
    init_linked_schema()
    job = query_df(
        """
        SELECT id,job_no,job_name,start_date,end_date
        FROM jobs WHERE id=?
        """,
        (int(job_id),),
    )
    if job.empty:
        st.info("This job is no longer available for scheduling.")
        return
    assignments = job_assignment_rows(int(job_id))
    display = assignments[
        ["id", "schedule_date", "staff", "stage_name", "start_time", "finish_time", "hours", "site_role", "notes"]
    ].rename(columns={
        "schedule_date": "Date", "staff": "Employee", "stage_name": "Stage",
        "start_time": "Start", "finish_time": "Finish", "hours": "Hours",
        "site_role": "Role", "notes": "Notes",
    }) if not assignments.empty else pd.DataFrame()

    if role not in {"admin", "manager"}:
        if display.empty:
            st.info("No staff schedule entries saved for this job.")
        else:
            st.dataframe(display, width="stretch", hide_index=True, column_config={"id": None})
        return

    staff = active_staff()
    if staff.empty:
        st.info("Add an active employee before scheduling this job.")
        return
    stage_rows = query_df(
        "SELECT id,stage_name FROM job_stages WHERE job_id=? ORDER BY sequence_order,id",
        (int(job_id),),
    ) if table_exists("job_stages") else pd.DataFrame()
    stage_options: dict[str, int | None] = {"Whole Job": None}
    for _, stage in stage_rows.iterrows():
        stage_options[str(stage["stage_name"])] = int(stage["id"])
    role_options = [
        "Site Work", "Leading Hand", "Supervision", "Quote / Measure",
        "Office / Planning", "Training", "Touch-ups", "Other",
    ]
    result = st.session_state.pop("scheduler_clash_result", None)
    if result:
        if result.get("messages"):
            pb_success(" | ".join(result["messages"]))
        if result.get("errors"):
            pb_error(" | ".join(result["errors"]))

    add_tab, edit_tab = st.tabs(["Add staff", "Edit / remove bookings"])
    with add_tab:
        pending_key = f"job_folder_add_clashes_{job_id}"
        if not render_tile_clash_choices(pending_key, f"job-folder-{job_id}"):
            job_start = to_date(job.iloc[0].get("start_date"), jobhub_today())
            with st.form(f"job_folder_schedule_add_{job_id}"):
                a1, a2, a3 = st.columns(3)
                selected_staff = a1.multiselect("Employees", staff["name"].astype(str).tolist())
                work_day = a1.date_input("Date", value=job_start)
                selected_stage = a2.selectbox("Job stage", list(stage_options.keys()))
                site_role = a2.selectbox("Role / type", role_options)
                start_value = a3.time_input("Start", value=time(7, 0))
                finish_value = a3.time_input("Finish", value=time(15, 0))
                planned_hours = a3.number_input(
                    "Hours per employee", min_value=0.25, max_value=24.0,
                    value=8.0, step=0.25,
                )
                linked_dates = st.checkbox(
                    "Keep bookings linked to the job start date", value=True,
                )
                notes = st.text_area("Schedule notes")
                add_staff = st.form_submit_button(
                    "Add staff to this job", type="primary", width="stretch",
                )
            if add_staff:
                if not selected_staff:
                    pb_error("Select at least one employee.")
                elif finish_value <= start_value:
                    pb_error("Finish time must be after start time.")
                else:
                    added: list[str] = []
                    blocked: list[str] = []
                    clashes: list[dict] = []
                    for staff_name in selected_staff:
                        employee_id = int(staff.loc[staff["name"] == staff_name, "id"].iloc[0])
                        conflicts = overlapping_assignment_rows(
                            employee_id, to_date(work_day), start_value, finish_value,
                        )
                        if not conflicts.empty:
                            clashes.append({
                                "employee_id": employee_id,
                                "staff_name": staff_name,
                                "expected_conflict_ids": conflicts["id"].astype(int).tolist(),
                                "job_id": int(job_id),
                                "job_stage_id": stage_options[selected_stage],
                                "job_label": (
                                    f"{job.iloc[0]['job_no']} · {job.iloc[0]['job_name']} — {selected_stage}"
                                ),
                                "work_date": to_date(work_day).isoformat(),
                                "start_time": start_value.strftime("%H:%M"),
                                "finish_time": finish_value.strftime("%H:%M"),
                                "planned_hours": float(planned_hours),
                                "site_role": site_role,
                                "notes": notes,
                                "created_by": str(user.get("username", "")),
                                "linked_to_job_dates": bool(linked_dates),
                            })
                            continue
                        ok, message = add_assignment(
                            employee_id, int(job_id), stage_options[selected_stage],
                            to_date(work_day), start_value, finish_value,
                            float(planned_hours), site_role, notes,
                            str(user.get("username", "")), bool(linked_dates),
                        )
                        (added if ok else blocked).append(staff_name if ok else f"{staff_name}: {message}")
                    if clashes:
                        st.session_state[pending_key] = clashes
                        st.session_state["scheduler_clash_result"] = {
                            "messages": [f"Added: {', '.join(added)}"] if added else [],
                            "errors": [f"Not added: {', '.join(blocked)}"] if blocked else [],
                        }
                        pb_rerun()
                    elif added:
                        pb_success(f"Added {', '.join(added)} to this job.")
                        if blocked:
                            pb_error(" | ".join(blocked))
                        pb_rerun()
                    elif blocked:
                        pb_error(" | ".join(blocked))

    with edit_tab:
        if assignments.empty:
            st.info("No staff bookings have been saved for this job yet.")
        else:
            event = st.dataframe(
                display,
                width="stretch",
                hide_index=True,
                on_select="rerun",
                selection_mode="multi-row",
                key=f"job_folder_schedule_rows_{job_id}",
                column_config={"id": None},
            )
            selected_rows = list(getattr(getattr(event, "selection", None), "rows", []) or [])
            if not selected_rows:
                st.caption("Select one or more bookings above to edit or remove them.")
            elif len(selected_rows) == 1:
                row = assignments.iloc[selected_rows[0]]
                assignment_id = int(row["id"])
                edit_pending_key = f"job_folder_edit_clash_{assignment_id}"
                if not render_edit_clash_choices(edit_pending_key):
                    staff_names = staff["name"].astype(str).tolist()
                    current_stage = str(row["stage_name"] or "Whole Job")
                    with st.form(f"job_folder_schedule_edit_{assignment_id}"):
                        e1, e2, e3 = st.columns(3)
                        edit_staff = e1.selectbox(
                            "Employee", staff_names,
                            index=staff_names.index(str(row["staff"])) if str(row["staff"]) in staff_names else 0,
                        )
                        edit_date = e1.date_input("Date", value=to_date(row["schedule_date"]))
                        edit_stage = e2.selectbox(
                            "Job stage", list(stage_options.keys()),
                            index=list(stage_options.keys()).index(current_stage) if current_stage in stage_options else 0,
                        )
                        edit_role = e2.selectbox(
                            "Role / type", role_options,
                            index=role_options.index(str(row["site_role"])) if str(row["site_role"]) in role_options else 0,
                        )
                        edit_start = e3.time_input("Start", value=time_value(row["start_time"]))
                        edit_finish = e3.time_input(
                            "Finish", value=time_value(row["finish_time"], time(15, 0)),
                        )
                        edit_hours = e3.number_input(
                            "Hours", min_value=0.25, max_value=24.0,
                            value=float(row["hours"]), step=0.25,
                        )
                        edit_linked = st.checkbox(
                            "Keep linked to the job start date",
                            value=bool(int(row.get("linked_to_job_dates") or 0)),
                        )
                        edit_notes = st.text_area("Notes", value=str(row["notes"] or ""))
                        save_booking = st.form_submit_button(
                            "Save booking", type="primary", width="stretch",
                        )
                    if save_booking:
                        employee_id = int(staff.loc[staff["name"] == edit_staff, "id"].iloc[0])
                        conflicts = overlapping_assignment_rows(
                            employee_id, to_date(edit_date), edit_start, edit_finish, assignment_id,
                        )
                        if not conflicts.empty:
                            st.session_state[edit_pending_key] = {
                                "assignment_id": assignment_id,
                                "employee_id": employee_id,
                                "staff_name": edit_staff,
                                "expected_conflict_ids": conflicts["id"].astype(int).tolist(),
                                "job_id": int(job_id),
                                "job_stage_id": stage_options[edit_stage],
                                "job_label": (
                                    f"{job.iloc[0]['job_no']} · {job.iloc[0]['job_name']} — {edit_stage}"
                                ),
                                "work_date": to_date(edit_date).isoformat(),
                                "start_time": edit_start.strftime("%H:%M"),
                                "finish_time": edit_finish.strftime("%H:%M"),
                                "planned_hours": float(edit_hours),
                                "site_role": edit_role,
                                "notes": edit_notes,
                                "linked_to_job_dates": bool(edit_linked),
                            }
                            pb_rerun()
                        else:
                            ok, message = replace_conflicts_for_assignment_edit(
                                [], assignment_id, employee_id, int(job_id),
                                stage_options[edit_stage], to_date(edit_date), edit_start,
                                edit_finish, float(edit_hours), edit_role, edit_notes,
                                bool(edit_linked),
                            )
                            (pb_success if ok else pb_error)(
                                "Booking updated." if ok else message
                            )
                            if ok:
                                pb_rerun()
                    confirm_delete = st.checkbox(
                        f"Confirm removal of {row['staff']}'s booking",
                        key=f"job_folder_schedule_delete_confirm_{assignment_id}",
                    )
                    if st.button(
                        "Remove selected booking",
                        disabled=not confirm_delete,
                        key=f"job_folder_schedule_delete_{assignment_id}",
                        width="stretch",
                    ):
                        execute("DELETE FROM staff_schedule WHERE id=?", (assignment_id,))
                        pb_success("Schedule booking removed.")
                        pb_rerun()
            else:
                selected_assignments = assignments.iloc[selected_rows]
                selected_ids = selected_assignments["id"].astype(int).tolist()
                st.markdown(f"### {len(selected_ids)} bookings selected")
                st.dataframe(
                    display.iloc[selected_rows],
                    width="stretch",
                    hide_index=True,
                    column_config={"id": None},
                )
                bulk_delete_confirm = st.checkbox(
                    f"Confirm removal of all {len(selected_ids)} selected bookings",
                    key=f"job_folder_schedule_bulk_delete_confirm_{job_id}",
                )
                if st.button(
                    "Remove all selected bookings",
                    disabled=not bulk_delete_confirm,
                    key=f"job_folder_schedule_bulk_delete_{job_id}",
                    width="stretch",
                ):
                    placeholders = ",".join("?" for _ in selected_ids)
                    execute(
                        sql_text(f"DELETE FROM staff_schedule WHERE id IN ({placeholders})"),
                        selected_ids,
                    )
                    pb_success(f"Removed {len(selected_ids)} schedule bookings.")
                    pb_rerun()


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
