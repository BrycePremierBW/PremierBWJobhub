from __future__ import annotations

import hashlib
import html
import os
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
try:
    import psycopg2  # type: ignore
    from psycopg2.pool import ThreadedConnectionPool  # type: ignore
except ImportError:  # Local SQLite mode can run before requirements are installed.
    psycopg2 = None
    ThreadedConnectionPool = None  # type: ignore
import streamlit as st

from pb_jobhub_visual_scheduler import render_jobhub_staff_scheduler


# ============================================================
# PREMIER BRUSHWORKS JOBHUB — COMPLETE REPLACEMENT
# ============================================================
# This app intentionally keeps the same core JobHub table names:
# jobs, builders_clients, employees, staff_schedule, app_users,
# timesheet_entries, products, material_entries, equipment_entries,
# and job_variations. Existing PostgreSQL/Supabase data is therefore
# reused when the same DATABASE_URL is retained.

APP_VERSION = "5.0.0"
DATA_DIR = os.getenv("DATA_DIR", str(Path(__file__).resolve().parent / "data"))
DB_PATH = os.path.join(DATA_DIR, "jobhub.db")
os.makedirs(DATA_DIR, exist_ok=True)

st.set_page_config(
    page_title="Premier Brushworks JobHub",
    page_icon="🎨",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# VISUAL THEME
# ============================================================

def apply_theme() -> None:
    st.markdown(
        """
        <style>
        :root {
            --pb-bg:#f5f3ef;
            --pb-card:#ffffff;
            --pb-text:#242321;
            --pb-muted:#706d68;
            --pb-border:#ded9d1;
            --pb-sidebar:#181817;
            --pb-accent:#9a8067;
            --pb-success:#47735b;
            --pb-warning:#a06f2f;
            --pb-danger:#9b4c48;
        }
        html, body, [class*="css"] { font-family: "Segoe UI", Arial, sans-serif; }
        .stApp, [data-testid="stAppViewContainer"] { background:var(--pb-bg)!important; color:var(--pb-text); }
        [data-testid="stHeader"] { background:transparent; }
        .block-container { max-width:1600px; padding-top:1rem; padding-bottom:3rem; }
        section[data-testid="stSidebar"] { background:var(--pb-sidebar); border-right:1px solid #34322f; }
        section[data-testid="stSidebar"] label,
        section[data-testid="stSidebar"] p,
        section[data-testid="stSidebar"] span,
        section[data-testid="stSidebar"] h1,
        section[data-testid="stSidebar"] h2,
        section[data-testid="stSidebar"] h3 { color:#f5f2ec!important; }
        section[data-testid="stSidebar"] div[data-baseweb="select"] > div { background:#fff!important; }
        section[data-testid="stSidebar"] div[data-baseweb="select"] * { color:#111!important; -webkit-text-fill-color:#111!important; }
        .pb-brand { padding:.8rem .2rem 1rem; border-bottom:1px solid #353432; margin-bottom:.8rem; }
        .pb-brand-mark { display:inline-flex; width:42px; height:42px; align-items:center; justify-content:center;
            border-radius:11px; background:#f5f2ec; color:#181817; font-weight:850; margin-bottom:.55rem; }
        .pb-brand-title { color:#fff; font-size:1.05rem; font-weight:750; line-height:1.15; }
        .pb-brand-sub { color:#aaa69f; font-size:.75rem; margin-top:.25rem; }
        .pb-hero { background:#fff; border:1px solid var(--pb-border); border-radius:15px; padding:1.15rem 1.3rem;
            margin:.2rem 0 1rem; box-shadow:0 6px 22px rgba(29,27,24,.06); }
        .pb-eyebrow { color:var(--pb-accent); font-size:.72rem; font-weight:750; letter-spacing:.12em; text-transform:uppercase; }
        .pb-title { color:var(--pb-text); font-size:1.75rem; font-weight:780; letter-spacing:-.035em; margin-top:.15rem; }
        .pb-subtitle { color:var(--pb-muted); margin-top:.35rem; line-height:1.45; }
        .pb-job-card { background:#fff; border:1px solid var(--pb-border); border-radius:13px; padding:1rem; margin-bottom:.7rem;
            box-shadow:0 4px 16px rgba(29,27,24,.04); }
        div[data-testid="stMetric"], div[data-testid="stVerticalBlockBorderWrapper"] {
            background:#fff; border:1px solid var(--pb-border); border-radius:12px; box-shadow:0 5px 18px rgba(29,27,24,.05); }
        div[data-testid="stMetric"] { padding:.9rem 1rem; }
        .stButton > button, .stDownloadButton > button { border-radius:9px!important; font-weight:650!important; }
        [data-testid="stDataFrame"] { border:1px solid var(--pb-border); border-radius:10px; overflow:hidden; }
        .pb-pill { display:inline-block; padding:.25rem .55rem; border-radius:999px; background:#eee8e1; color:#554a40; font-size:.75rem; font-weight:700; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def page_header(title: str, subtitle: str, eyebrow: str = "Premier Brushworks") -> None:
    st.markdown(
        f"""
        <div class="pb-hero">
          <div class="pb-eyebrow">{html.escape(eyebrow)}</div>
          <div class="pb-title">{html.escape(title)}</div>
          <div class="pb-subtitle">{html.escape(subtitle)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


apply_theme()


# ============================================================
# DATABASE — SQLITE LOCALLY / POSTGRESQL ON RENDER
# ============================================================

def get_database_url() -> str:
    try:
        value = st.secrets.get("DATABASE_URL", "")
        if value:
            return str(value)
    except Exception:
        pass
    return os.getenv("DATABASE_URL", "")


DATABASE_URL = get_database_url()
USE_POSTGRES = bool(DATABASE_URL)


@st.cache_resource
def postgres_pool():
    if not USE_POSTGRES:
        return None
    if ThreadedConnectionPool is None:
        raise RuntimeError("DATABASE_URL is set but psycopg2-binary is not installed. Run pip install -r requirements.txt.")
    return ThreadedConnectionPool(minconn=1, maxconn=15, dsn=DATABASE_URL, sslmode="require")


def adapt_sql(sql: str) -> str:
    if not USE_POSTGRES:
        return sql
    value = sql.strip()
    value = re.sub(r"AS '([^']+)'", r'AS "\1"', value)
    value = value.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    if re.search(r"INSERT\s+OR\s+IGNORE\s+INTO", value, re.I):
        value = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", value, flags=re.I)
        value = value.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    value = value.replace("?", "%s")
    value = re.sub(r"%(?!s)", "%%", value)
    return value


class PgCursor:
    def __init__(self, cursor: Any):
        self._cursor = cursor

    def execute(self, sql: str, params: tuple = ()):
        return self._cursor.execute(adapt_sql(sql), params)

    def executemany(self, sql: str, rows: list[tuple]):
        return self._cursor.executemany(adapt_sql(sql), rows)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def description(self):
        return self._cursor.description

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def __getattr__(self, name: str):
        return getattr(self._cursor, name)


class PgConnection:
    def __init__(self, connection: Any, pool: Any):
        self._connection = connection
        self._pool = pool
        self._closed = False

    def cursor(self) -> PgCursor:
        return PgCursor(self._connection.cursor())

    def commit(self):
        return self._connection.commit()

    def rollback(self):
        return self._connection.rollback()

    def close(self):
        if not self._closed:
            self._closed = True
            self._pool.putconn(self._connection)


def connect():
    if USE_POSTGRES:
        pool = postgres_pool()
        if pool is None:
            raise RuntimeError("PostgreSQL pool is unavailable.")
        return PgConnection(pool.getconn(), pool)
    connection = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def execute(sql: str, params: tuple = ()) -> int:
    connection = connect()
    try:
        cursor = connection.cursor()
        cursor.execute(sql, params)
        connection.commit()
        return int(cursor.rowcount or 0)
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass
        raise
    finally:
        connection.close()


def df_query(sql: str, params: tuple = ()) -> pd.DataFrame:
    connection = connect()
    try:
        cursor = connection.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        columns = [item[0] for item in cursor.description] if cursor.description else []
        return pd.DataFrame(rows, columns=columns)
    finally:
        connection.close()


def ensure_column(cursor: Any, table: str, column: str, definition: str) -> None:
    if USE_POSTGRES:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
        return
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {str(row[1]) for row in cursor.fetchall()}
    if column not in existing:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    connection = connect()
    cursor = connection.cursor()
    try:
        statements = [
            """CREATE TABLE IF NOT EXISTS builders_clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, name TEXT UNIQUE,
                contact_name TEXT, phone TEXT, email TEXT, address TEXT, qbcc TEXT,
                abn TEXT, terms TEXT, notes TEXT)""",
            """CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_no TEXT UNIQUE, job_name TEXT,
                builder_client_id INTEGER, site_address TEXT, status TEXT, leading_hand TEXT,
                start_date TEXT, end_date TEXT, contract_value REAL DEFAULT 0, notes TEXT,
                FOREIGN KEY(builder_client_id) REFERENCES builders_clients(id))""",
            """CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, role TEXT, phone TEXT,
                base_hourly_rate REAL DEFAULT 0, rate_plus_10 REAL DEFAULT 0,
                status TEXT DEFAULT 'Active', notes TEXT)""",
            """CREATE TABLE IF NOT EXISTS staff_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL,
                employee_id INTEGER NOT NULL, schedule_date TEXT NOT NULL,
                start_time TEXT DEFAULT '07:00', finish_time TEXT DEFAULT '15:00',
                site_role TEXT, notes TEXT, created_at TEXT,
                period_type TEXT, period_start TEXT, period_end TEXT,
                planned_hours REAL DEFAULT 7.6, created_by TEXT,
                source_app TEXT DEFAULT 'JobHub', updated_at TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id),
                FOREIGN KEY(employee_id) REFERENCES employees(id))""",
            """CREATE TABLE IF NOT EXISTS staff_leave_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id INTEGER NOT NULL,
                start_date TEXT NOT NULL, end_date TEXT NOT NULL,
                leave_type TEXT DEFAULT 'Annual Leave', status TEXT DEFAULT 'Pending',
                reason TEXT, reviewed_by TEXT, reviewed_at TEXT, created_at TEXT,
                FOREIGN KEY(employee_id) REFERENCES employees(id))""",
            """CREATE TABLE IF NOT EXISTS timesheet_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL,
                employee_id INTEGER NOT NULL, work_date TEXT, start_time TEXT,
                finish_time TEXT, break_minutes REAL DEFAULT 0, total_hours REAL DEFAULT 0,
                work_type TEXT, submitted_by TEXT, submitted_at TEXT,
                status TEXT DEFAULT 'Submitted', notes TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id),
                FOREIGN KEY(employee_id) REFERENCES employees(id))""",
            """CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT, product_code TEXT UNIQUE,
                product_name TEXT, supplier TEXT, unit TEXT, price_ex_gst REAL DEFAULT 0,
                notes TEXT)""",
            """CREATE TABLE IF NOT EXISTS material_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER,
                product_id INTEGER, qty_required REAL DEFAULT 0, qty_received REAL DEFAULT 0,
                date_ordered TEXT, supplier TEXT, notes TEXT,
                custom_product_code TEXT, custom_product_name TEXT,
                custom_supplier TEXT, custom_unit TEXT, custom_unit_price REAL DEFAULT 0,
                custom_colour TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id),
                FOREIGN KEY(product_id) REFERENCES products(id))""",
            """CREATE TABLE IF NOT EXISTS equipment_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT, equipment_item TEXT,
                category TEXT, serial_no TEXT, job_id INTEGER, date_out TEXT,
                date_in TEXT, condition_out TEXT, condition_in TEXT,
                assigned_to TEXT, notes TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id))""",
            """CREATE TABLE IF NOT EXISTS job_variations (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL,
                variation_no TEXT, description TEXT, reason TEXT,
                amount_ex_gst REAL DEFAULT 0, status TEXT DEFAULT 'Draft',
                sent_date TEXT, approved_date TEXT, approved_by TEXT,
                notes TEXT, created_at TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id))""",
            """CREATE TABLE IF NOT EXISTS app_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE,
                password_hash TEXT NOT NULL, role TEXT NOT NULL,
                employee_id INTEGER, active INTEGER DEFAULT 1, notes TEXT,
                FOREIGN KEY(employee_id) REFERENCES employees(id))""",
            """CREATE TABLE IF NOT EXISTS app_settings (
                setting_key TEXT PRIMARY KEY, setting_value TEXT)""",
            """CREATE TABLE IF NOT EXISTS scheduler_import_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, source_name TEXT,
                jobs_added INTEGER DEFAULT 0, employees_added INTEGER DEFAULT 0,
                assignments_added INTEGER DEFAULT 0, leave_added INTEGER DEFAULT 0,
                imported_by TEXT, imported_at TEXT, notes TEXT)""",
        ]
        for statement in statements:
            cursor.execute(statement)

        # Upgrade older JobHub tables without deleting or replacing data.
        for column, definition in {
            "period_type": "TEXT", "period_start": "TEXT", "period_end": "TEXT",
            "planned_hours": "REAL DEFAULT 7.6", "created_by": "TEXT",
            "source_app": "TEXT DEFAULT 'JobHub'", "updated_at": "TEXT",
        }.items():
            ensure_column(cursor, "staff_schedule", column, definition)

        for column, definition in {
            "custom_product_code": "TEXT", "custom_product_name": "TEXT",
            "custom_supplier": "TEXT", "custom_unit": "TEXT",
            "custom_unit_price": "REAL DEFAULT 0", "custom_colour": "TEXT",
        }.items():
            ensure_column(cursor, "material_entries", column, definition)

        connection.commit()
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass
        raise
    finally:
        connection.close()


init_db()


# ============================================================
# AUTHENTICATION
# ============================================================

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def check_password(password: str, stored_hash: str) -> bool:
    return hash_password(password) == str(stored_hash or "")


def seed_admin() -> None:
    users = df_query("SELECT COUNT(*) AS count FROM app_users")
    if int(users.iloc[0]["count"] or 0) == 0:
        execute(
            """INSERT INTO app_users
               (username,password_hash,role,employee_id,active,notes)
               VALUES (?,?,?,?,?,?)""",
            ("admin", hash_password("admin123"), "admin", None, 1,
             "Initial administrator. Change this password immediately."),
        )


def current_user() -> dict[str, Any] | None:
    return st.session_state.get("jobhub_user")


def current_username() -> str:
    user = current_user() or {}
    return str(user.get("username") or "JobHub user")


def is_manager() -> bool:
    return str((current_user() or {}).get("role") or "").lower() in {"admin", "manager"}


def require_login() -> None:
    seed_admin()
    if current_user():
        return
    page_header("JobHub login", "Sign in with your existing JobHub account.", "Secure access")
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
    if submitted:
        row = df_query(
            """SELECT u.id,u.username,u.password_hash,u.role,u.employee_id,u.active,
                      COALESCE(e.name,'') AS employee_name
               FROM app_users u LEFT JOIN employees e ON e.id=u.employee_id
               WHERE LOWER(u.username)=LOWER(?)""",
            (username.strip(),),
        )
        if row.empty or not check_password(password, row.iloc[0]["password_hash"]):
            st.error("Invalid username or password.")
        elif int(row.iloc[0]["active"] or 0) != 1:
            st.error("This account is inactive.")
        else:
            item = row.iloc[0]
            st.session_state["jobhub_user"] = {
                "id": int(item["id"]), "username": str(item["username"]),
                "role": str(item["role"]),
                "employee_id": None if pd.isna(item["employee_id"]) else int(item["employee_id"]),
                "employee_name": str(item["employee_name"] or ""),
            }
            st.rerun()
    st.info("First-time fallback login: admin / admin123. Change it in User Access after signing in.")
    st.stop()


require_login()


# ============================================================
# HELPERS
# ============================================================

def rerun() -> None:
    st.rerun()


def iso(value: Any) -> str:
    if value is None or value == "" or pd.isna(value):
        return ""
    if isinstance(value, date):
        return value.isoformat()
    try:
        return pd.to_datetime(value).date().isoformat()
    except Exception:
        return str(value)


def as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def active_jobs() -> pd.DataFrame:
    return df_query(
        """SELECT j.id AS job_id,j.job_no,j.job_name,j.site_address,j.status,
                  j.leading_hand,j.start_date,j.end_date,j.contract_value,j.notes,
                  COALESCE(b.name,'') AS builder
           FROM jobs j LEFT JOIN builders_clients b ON b.id=j.builder_client_id
           WHERE LOWER(COALESCE(j.status,'')) NOT IN ('completed','complete','archived','closed','cancelled')
           ORDER BY j.job_no"""
    )


def all_jobs() -> pd.DataFrame:
    return df_query(
        """SELECT j.id AS job_id,j.job_no,j.job_name,j.site_address,j.status,
                  j.leading_hand,j.start_date,j.end_date,j.contract_value,j.notes,
                  COALESCE(b.name,'') AS builder
           FROM jobs j LEFT JOIN builders_clients b ON b.id=j.builder_client_id
           ORDER BY j.job_no"""
    )


def active_staff() -> pd.DataFrame:
    return df_query(
        """SELECT id AS employee_id,name,role,phone,base_hourly_rate,rate_plus_10,status,notes
           FROM employees WHERE LOWER(COALESCE(status,'Active'))='active' ORDER BY name"""
    )


def get_or_create_builder(name: str) -> int | None:
    clean = name.strip()
    if not clean:
        return None
    existing = df_query("SELECT id FROM builders_clients WHERE LOWER(name)=LOWER(?)", (clean,))
    if not existing.empty:
        return int(existing.iloc[0]["id"])
    execute(
        "INSERT INTO builders_clients (type,name,contact_name,phone,email,address,notes) VALUES (?,?,?,?,?,?,?)",
        ("Builder / Client", clean, "", "", "", "", "Created from Job register"),
    )
    created = df_query("SELECT id FROM builders_clients WHERE LOWER(name)=LOWER(?)", (clean,))
    return int(created.iloc[0]["id"]) if not created.empty else None


# ============================================================
# PAGES
# ============================================================

def dashboard_page() -> None:
    page_header("Operations dashboard", "Jobs, staffing and current scheduling pressure at a glance.", "Today")
    jobs = active_jobs()
    staff = active_staff()
    monday = date.today() - timedelta(days=date.today().weekday())
    sunday = monday + timedelta(days=6)
    schedule = df_query(
        """SELECT s.id,s.job_id,s.employee_id,s.schedule_date,s.period_start,s.period_end,
                  COALESCE(s.planned_hours,7.6) AS planned_hours,e.name AS employee,
                  j.job_no,j.job_name
           FROM staff_schedule s JOIN employees e ON e.id=s.employee_id
           JOIN jobs j ON j.id=s.job_id
           WHERE COALESCE(NULLIF(s.period_end,''),s.schedule_date)>=?
             AND COALESCE(NULLIF(s.period_start,''),s.schedule_date)<=?""",
        (monday.isoformat(), sunday.isoformat()),
    )
    scheduled_job_ids = set(schedule["job_id"].astype(int)) if not schedule.empty else set()
    unscheduled = jobs[~jobs["job_id"].isin(scheduled_job_ids)] if not jobs.empty else jobs

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Active jobs", len(jobs))
    c2.metric("Active staff", len(staff))
    c3.metric("This week's allocations", len(schedule))
    c4.metric("Jobs without crew", len(unscheduled))

    left, right = st.columns([1.25, 1])
    with left:
        st.subheader("This week — who is going where")
        if schedule.empty:
            st.info("No staff are scheduled this week.")
        else:
            view = schedule[["employee", "job_no", "job_name", "period_start", "period_end", "planned_hours"]].copy()
            view.columns = ["Staff", "Job No", "Job", "From", "To", "Hours"]
            st.dataframe(view, width="stretch", hide_index=True)
    with right:
        st.subheader("Active jobs without crew")
        if unscheduled.empty:
            st.success("Every active job has crew assigned this week.")
        else:
            st.dataframe(
                unscheduled[["job_no", "job_name", "site_address", "status"]].rename(
                    columns={"job_no": "Job No", "job_name": "Job", "site_address": "Site", "status": "Status"}
                ),
                width="stretch",
                hide_index=True,
            )

    if not schedule.empty:
        workload = schedule.groupby("employee", as_index=False)["planned_hours"].sum().set_index("employee")
        st.subheader("Allocated hours this week")
        st.bar_chart(workload)


def jobs_page() -> None:
    page_header("Jobs", "Create, update and review all Premier Brushworks jobs.", "Job register")
    register_tab, add_tab, details_tab = st.tabs(["Job register", "Add or edit job", "Job details"])

    with register_tab:
        jobs = all_jobs()
        search, status = st.columns([2, 1])
        search_text = search.text_input("Search jobs", placeholder="Job number, name, builder or address")
        statuses = ["All"] + sorted({str(v) for v in jobs["status"].dropna().tolist() if str(v).strip()}) if not jobs.empty else ["All"]
        chosen_status = status.selectbox("Status", statuses)
        filtered = jobs.copy()
        if search_text.strip() and not filtered.empty:
            mask = filtered.astype(str).apply(lambda col: col.str.contains(search_text.strip(), case=False, na=False)).any(axis=1)
            filtered = filtered[mask]
        if chosen_status != "All" and not filtered.empty:
            filtered = filtered[filtered["status"] == chosen_status]
        display_cols = ["job_no", "job_name", "builder", "site_address", "status", "leading_hand", "start_date", "end_date", "contract_value"]
        labels = {"job_no":"Job No","job_name":"Job","builder":"Builder / Client","site_address":"Site Address","status":"Status",
                  "leading_hand":"Leading Hand","start_date":"Start","end_date":"Finish","contract_value":"Contract Value"}
        st.dataframe(filtered[display_cols].rename(columns=labels), width="stretch", hide_index=True)
        st.download_button("Download jobs CSV", filtered.to_csv(index=False).encode("utf-8"), "PB_JobHub_jobs.csv", "text/csv")

    with add_tab:
        if not is_manager():
            st.warning("Only managers and administrators can change the job register.")
        else:
            jobs = all_jobs()
            labels = ["Create new job"] + [f"{row.job_no} — {row.job_name}" for row in jobs.itertuples()]
            selected = st.selectbox("Action", labels)
            record = None
            if selected != "Create new job":
                job_no = selected.split(" — ", 1)[0]
                record = jobs[jobs["job_no"] == job_no].iloc[0]
            with st.form("job_form"):
                c1, c2 = st.columns(2)
                job_no = c1.text_input("Job number", value="" if record is None else str(record["job_no"]))
                job_name = c2.text_input("Job name", value="" if record is None else str(record["job_name"]))
                c3, c4 = st.columns(2)
                builder = c3.text_input("Builder / client", value="" if record is None else str(record["builder"]))
                address = c4.text_input("Site address", value="" if record is None else str(record["site_address"]))
                c5, c6, c7 = st.columns(3)
                status_options = ["Quoted", "Booked", "Active", "On Hold", "Completed", "Archived", "Cancelled"]
                current_status = "Active" if record is None else str(record["status"] or "Active")
                status_index = status_options.index(current_status) if current_status in status_options else 2
                job_status = c5.selectbox("Status", status_options, index=status_index)
                leading_hand = c6.text_input("Leading hand", value="" if record is None else str(record["leading_hand"] or ""))
                contract_value = c7.number_input("Contract value ex GST", min_value=0.0, value=0.0 if record is None else as_float(record["contract_value"]), step=1000.0)
                c8, c9 = st.columns(2)
                start_value = date.today() if record is None or not str(record["start_date"] or "").strip() else pd.to_datetime(record["start_date"]).date()
                end_value = start_value if record is None or not str(record["end_date"] or "").strip() else pd.to_datetime(record["end_date"]).date()
                start_date = c8.date_input("Start date", value=start_value)
                end_date = c9.date_input("End date", value=end_value)
                notes = st.text_area("Notes", value="" if record is None else str(record["notes"] or ""))
                save = st.form_submit_button("Save job", type="primary")
            if save:
                if not job_no.strip() or not job_name.strip():
                    st.error("Job number and job name are required.")
                elif end_date < start_date:
                    st.error("End date cannot be before start date.")
                else:
                    builder_id = get_or_create_builder(builder)
                    if record is None:
                        try:
                            execute(
                                """INSERT INTO jobs
                                   (job_no,job_name,builder_client_id,site_address,status,leading_hand,start_date,end_date,contract_value,notes)
                                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                                (job_no.strip(), job_name.strip(), builder_id, address.strip(), job_status,
                                 leading_hand.strip(), start_date.isoformat(), end_date.isoformat(), contract_value, notes),
                            )
                            st.success("Job created.")
                            rerun()
                        except Exception as exc:
                            st.error(f"Could not create job: {exc}")
                    else:
                        execute(
                            """UPDATE jobs SET job_no=?,job_name=?,builder_client_id=?,site_address=?,status=?,
                               leading_hand=?,start_date=?,end_date=?,contract_value=?,notes=? WHERE id=?""",
                            (job_no.strip(), job_name.strip(), builder_id, address.strip(), job_status,
                             leading_hand.strip(), start_date.isoformat(), end_date.isoformat(), contract_value, notes,
                             int(record["job_id"])),
                        )
                        st.success("Job updated.")
                        rerun()

    with details_tab:
        jobs = all_jobs()
        if jobs.empty:
            st.info("No jobs are available yet.")
        else:
            options = {f"{row.job_no} — {row.job_name}": int(row.job_id) for row in jobs.itertuples()}
            chosen = st.selectbox("Job", list(options), key="job_details_choice")
            job_id = options[chosen]
            job = jobs[jobs["job_id"] == job_id].iloc[0]
            st.markdown(
                f"<div class='pb-job-card'><strong>{html.escape(str(job['job_no']))} — {html.escape(str(job['job_name']))}</strong><br>"
                f"{html.escape(str(job['site_address'] or ''))}<br><span class='pb-pill'>{html.escape(str(job['status'] or ''))}</span></div>",
                unsafe_allow_html=True,
            )
            schedule_tab, times_tab, materials_tab, variations_tab = st.tabs(["Crew schedule", "Timesheets", "Materials", "Variations"])
            with schedule_tab:
                rows = df_query(
                    """SELECT e.name AS Staff,s.schedule_date AS Date,s.period_start AS From_Date,s.period_end AS To_Date,
                              s.start_time AS Start,s.finish_time AS Finish,s.site_role AS Role,
                              s.planned_hours AS Hours,s.source_app AS Entered_In,s.created_by AS Entered_By,s.notes AS Notes
                       FROM staff_schedule s JOIN employees e ON e.id=s.employee_id
                       WHERE s.job_id=? ORDER BY COALESCE(NULLIF(s.period_start,''),s.schedule_date),e.name""",
                    (job_id,),
                )
                st.dataframe(rows, width="stretch", hide_index=True)
            with times_tab:
                rows = df_query(
                    """SELECT e.name AS Staff,t.work_date AS Date,t.start_time AS Start,t.finish_time AS Finish,
                              t.break_minutes AS Break_Minutes,t.total_hours AS Hours,t.status AS Status,t.notes AS Notes
                       FROM timesheet_entries t JOIN employees e ON e.id=t.employee_id
                       WHERE t.job_id=? ORDER BY t.work_date DESC""",
                    (job_id,),
                )
                st.dataframe(rows, width="stretch", hide_index=True)
            with materials_tab:
                rows = df_query(
                    """SELECT COALESCE(p.product_name,m.custom_product_name,'') AS Product,
                              COALESCE(p.product_code,m.custom_product_code,'') AS Code,
                              m.custom_colour AS Colour,m.qty_required AS Required,m.qty_received AS Received,
                              COALESCE(m.supplier,m.custom_supplier,'') AS Supplier,m.date_ordered AS Ordered,m.notes AS Notes
                       FROM material_entries m LEFT JOIN products p ON p.id=m.product_id
                       WHERE m.job_id=? ORDER BY m.id DESC""",
                    (job_id,),
                )
                st.dataframe(rows, width="stretch", hide_index=True)
            with variations_tab:
                rows = df_query(
                    """SELECT variation_no AS Variation,description AS Description,reason AS Reason,
                              amount_ex_gst AS Amount_Ex_GST,status AS Status,approved_by AS Approved_By,
                              approved_date AS Approved_Date,notes AS Notes
                       FROM job_variations WHERE job_id=? ORDER BY id DESC""",
                    (job_id,),
                )
                st.dataframe(rows, width="stretch", hide_index=True)


def staff_page() -> None:
    page_header("Staff", "Manage employees and leave requests used by the visual scheduler.", "People")
    register_tab, edit_tab, leave_tab = st.tabs(["Staff register", "Add or edit staff", "Leave"])
    with register_tab:
        staff = df_query("SELECT id,name,role,phone,base_hourly_rate,rate_plus_10,status,notes FROM employees ORDER BY name")
        display = staff.copy()
        if not is_manager() and not display.empty:
            display = display[["name", "role", "phone", "status"]]
        st.dataframe(display, width="stretch", hide_index=True)
    with edit_tab:
        if not is_manager():
            st.warning("Only managers and administrators can edit staff.")
        else:
            staff = df_query("SELECT * FROM employees ORDER BY name")
            options = ["Create new staff member"] + [str(name) for name in staff["name"].tolist()]
            chosen = st.selectbox("Action", options)
            record = None if chosen == options[0] else staff[staff["name"] == chosen].iloc[0]
            with st.form("staff_form"):
                c1, c2, c3 = st.columns(3)
                name = c1.text_input("Name", value="" if record is None else str(record["name"]))
                role = c2.text_input("Role", value="Painter" if record is None else str(record["role"] or ""))
                phone = c3.text_input("Phone", value="" if record is None else str(record["phone"] or ""))
                c4, c5, c6 = st.columns(3)
                base_rate = c4.number_input("Base hourly rate", min_value=0.0, value=0.0 if record is None else as_float(record["base_hourly_rate"]), step=1.0)
                plus_10 = c5.number_input("Rate + 10%", min_value=0.0, value=0.0 if record is None else as_float(record["rate_plus_10"]), step=1.0)
                current_status = "Active" if record is None else str(record["status"] or "Active")
                status = c6.selectbox("Status", ["Active", "Inactive"], index=0 if current_status == "Active" else 1)
                notes = st.text_area("Notes", value="" if record is None else str(record["notes"] or ""))
                save = st.form_submit_button("Save staff member", type="primary")
            if save:
                if not name.strip():
                    st.error("Name is required.")
                elif record is None:
                    try:
                        execute(
                            "INSERT INTO employees (name,role,phone,base_hourly_rate,rate_plus_10,status,notes) VALUES (?,?,?,?,?,?,?)",
                            (name.strip(), role.strip(), phone.strip(), base_rate, plus_10, status, notes),
                        )
                        st.success("Staff member created.")
                        rerun()
                    except Exception as exc:
                        st.error(f"Could not create staff member: {exc}")
                else:
                    execute(
                        "UPDATE employees SET name=?,role=?,phone=?,base_hourly_rate=?,rate_plus_10=?,status=?,notes=? WHERE id=?",
                        (name.strip(), role.strip(), phone.strip(), base_rate, plus_10, status, notes, int(record["id"])),
                    )
                    st.success("Staff member updated.")
                    rerun()
    with leave_tab:
        staff = active_staff()
        if staff.empty:
            st.info("No active staff are available.")
        else:
            user = current_user() or {}
            employee_map = {str(row["name"]): int(row["employee_id"]) for _, row in staff.iterrows()}
            default_name = str(user.get("employee_name") or "")
            if is_manager():
                selected_employee = st.selectbox("Staff member", list(employee_map), index=list(employee_map).index(default_name) if default_name in employee_map else 0)
            else:
                if not user.get("employee_id"):
                    st.warning("Your login is not linked to an employee record.")
                    return
                selected_employee = default_name
                st.write(f"Leave request for **{selected_employee}**")
            with st.form("leave_request_form"):
                c1, c2, c3 = st.columns(3)
                start = c1.date_input("From", value=date.today())
                end = c2.date_input("To", value=date.today())
                leave_type = c3.selectbox("Leave type", ["Annual Leave", "Personal Leave", "RDO", "Unpaid Leave", "Other"])
                reason = st.text_area("Reason / notes")
                submit = st.form_submit_button("Submit leave request")
            if submit:
                if end < start:
                    st.error("End date cannot be before start date.")
                else:
                    execute(
                        """INSERT INTO staff_leave_requests
                           (employee_id,start_date,end_date,leave_type,status,reason,reviewed_by,reviewed_at,created_at)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (employee_map[selected_employee], start.isoformat(), end.isoformat(), leave_type,
                         "Approved" if is_manager() else "Pending", reason,
                         current_username() if is_manager() else "", datetime.now().isoformat(sep=" ") if is_manager() else None,
                         datetime.now().isoformat(sep=" ")),
                    )
                    st.success("Leave request saved.")
                    rerun()
            rows = df_query(
                """SELECT l.id,e.name AS Staff,l.start_date AS From_Date,l.end_date AS To_Date,
                          l.leave_type AS Type,l.status AS Status,l.reason AS Reason,
                          l.reviewed_by AS Reviewed_By,l.created_at AS Created
                   FROM staff_leave_requests l JOIN employees e ON e.id=l.employee_id
                   ORDER BY l.start_date DESC"""
            )
            if not is_manager() and user.get("employee_id") and not rows.empty:
                rows = rows[rows["Staff"] == default_name]
            st.dataframe(rows, width="stretch", hide_index=True)
            if is_manager() and not rows.empty:
                pending = rows[rows["Status"] == "Pending"]
                if not pending.empty:
                    id_map = {f"#{int(row.id)} — {row.Staff} — {row.From_Date} to {row.To_Date}": int(row.id) for row in pending.itertuples()}
                    selected = st.selectbox("Pending request", list(id_map), key="pending_leave_choice")
                    c1, c2 = st.columns(2)
                    if c1.button("Approve leave", type="primary"):
                        execute("UPDATE staff_leave_requests SET status='Approved',reviewed_by=?,reviewed_at=? WHERE id=?",
                                (current_username(), datetime.now().isoformat(sep=" "), id_map[selected]))
                        rerun()
                    if c2.button("Reject leave"):
                        execute("UPDATE staff_leave_requests SET status='Rejected',reviewed_by=?,reviewed_at=? WHERE id=?",
                                (current_username(), datetime.now().isoformat(sep=" "), id_map[selected]))
                        rerun()


def my_schedule_page() -> None:
    page_header("My schedule", "Your assigned jobs and approved leave.", "Staff portal")
    user = current_user() or {}
    employee_id = user.get("employee_id")
    if not employee_id:
        st.warning("This login is not linked to an employee record. Ask a manager to link it in User Access.")
        return
    start = st.date_input("From", value=date.today() - timedelta(days=date.today().weekday()))
    end = st.date_input("To", value=start + timedelta(days=13))
    rows = df_query(
        """SELECT s.schedule_date AS Date,s.period_start AS From_Date,s.period_end AS To_Date,
                  s.start_time AS Start,s.finish_time AS Finish,j.job_no AS Job_No,
                  j.job_name AS Job,j.site_address AS Site,s.site_role AS Role,s.notes AS Notes
           FROM staff_schedule s JOIN jobs j ON j.id=s.job_id
           WHERE s.employee_id=?
             AND COALESCE(NULLIF(s.period_end,''),s.schedule_date)>=?
             AND COALESCE(NULLIF(s.period_start,''),s.schedule_date)<=?
           ORDER BY COALESCE(NULLIF(s.period_start,''),s.schedule_date),s.start_time""",
        (employee_id, start.isoformat(), end.isoformat()),
    )
    st.dataframe(rows, width="stretch", hide_index=True)
    leave = df_query(
        """SELECT start_date AS From_Date,end_date AS To_Date,leave_type AS Type,status AS Status,reason AS Reason
           FROM staff_leave_requests WHERE employee_id=? AND end_date>=? AND start_date<=? ORDER BY start_date""",
        (employee_id, start.isoformat(), end.isoformat()),
    )
    st.subheader("Leave")
    st.dataframe(leave, width="stretch", hide_index=True)


def timesheets_page() -> None:
    page_header("Timesheets", "Enter and review labour against JobHub jobs.", "Labour")
    add_tab, review_tab = st.tabs(["Add timesheet", "Review"])
    jobs = active_jobs()
    staff = active_staff()
    user = current_user() or {}
    with add_tab:
        if jobs.empty or staff.empty:
            st.info("Create an active job and active staff member first.")
        else:
            job_map = {f"{r.job_no} — {r.job_name}": int(r.job_id) for r in jobs.itertuples()}
            staff_map = {str(r.name): int(r.employee_id) for r in staff.itertuples()}
            with st.form("timesheet_form"):
                c1, c2 = st.columns(2)
                selected_job = c1.selectbox("Job", list(job_map))
                if is_manager():
                    selected_staff = c2.selectbox("Staff", list(staff_map))
                else:
                    selected_staff = str(user.get("employee_name") or "")
                    c2.write(f"Staff: **{selected_staff}**")
                c3, c4, c5, c6 = st.columns(4)
                work_date = c3.date_input("Work date", value=date.today())
                start_time = c4.text_input("Start", value="07:00")
                finish_time = c5.text_input("Finish", value="15:00")
                break_minutes = c6.number_input("Break minutes", min_value=0, value=30, step=5)
                work_type = st.selectbox("Work type", ["Painting", "Preparation", "Supervision", "Travel", "Other"])
                notes = st.text_area("Notes")
                save = st.form_submit_button("Submit timesheet", type="primary")
            if save:
                if selected_staff not in staff_map:
                    st.error("Your account is not linked to an active staff member.")
                else:
                    try:
                        start_dt = datetime.strptime(start_time[:5], "%H:%M")
                        finish_dt = datetime.strptime(finish_time[:5], "%H:%M")
                        hours = max(0.0, (finish_dt - start_dt).total_seconds() / 3600 - float(break_minutes) / 60)
                    except Exception:
                        st.error("Start and finish must use HH:MM format.")
                        hours = -1
                    if hours >= 0:
                        execute(
                            """INSERT INTO timesheet_entries
                               (job_id,employee_id,work_date,start_time,finish_time,break_minutes,total_hours,
                                work_type,submitted_by,submitted_at,status,notes)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (job_map[selected_job], staff_map[selected_staff], work_date.isoformat(), start_time[:5], finish_time[:5],
                             float(break_minutes), round(hours, 2), work_type, current_username(),
                             datetime.now().isoformat(sep=" "), "Submitted", notes),
                        )
                        st.success(f"Timesheet submitted: {hours:.2f} hours.")
                        rerun()
    with review_tab:
        rows = df_query(
            """SELECT t.id,e.name AS Staff,j.job_no AS Job_No,j.job_name AS Job,t.work_date AS Date,
                      t.start_time AS Start,t.finish_time AS Finish,t.break_minutes AS Break_Minutes,
                      t.total_hours AS Hours,t.work_type AS Type,t.status AS Status,t.submitted_by AS Submitted_By,t.notes AS Notes
               FROM timesheet_entries t JOIN employees e ON e.id=t.employee_id JOIN jobs j ON j.id=t.job_id
               ORDER BY t.work_date DESC,t.id DESC"""
        )
        if not is_manager() and user.get("employee_id") and not rows.empty:
            rows = rows[rows["Staff"] == str(user.get("employee_name") or "")]
        st.dataframe(rows, width="stretch", hide_index=True)
        st.download_button("Download timesheets CSV", rows.to_csv(index=False).encode("utf-8"), "PB_JobHub_timesheets.csv", "text/csv")


def materials_page() -> None:
    page_header("Materials", "Record paint and material requirements against jobs.", "Procurement")
    jobs = active_jobs()
    add_tab, review_tab = st.tabs(["Add material", "Review materials"])
    with add_tab:
        if not is_manager():
            st.warning("Only managers and administrators can add material orders.")
        elif jobs.empty:
            st.info("Create an active job first.")
        else:
            job_map = {f"{r.job_no} — {r.job_name}": int(r.job_id) for r in jobs.itertuples()}
            with st.form("material_form"):
                selected_job = st.selectbox("Job", list(job_map))
                c1, c2, c3 = st.columns(3)
                product = c1.text_input("Product")
                code = c2.text_input("Product code")
                colour = c3.text_input("Colour")
                c4, c5, c6, c7 = st.columns(4)
                required = c4.number_input("Qty required", min_value=0.0, value=0.0, step=1.0)
                received = c5.number_input("Qty received", min_value=0.0, value=0.0, step=1.0)
                supplier = c6.text_input("Supplier")
                ordered = c7.date_input("Date ordered", value=date.today())
                notes = st.text_area("Notes")
                save = st.form_submit_button("Save material entry", type="primary")
            if save:
                execute(
                    """INSERT INTO material_entries
                       (job_id,product_id,qty_required,qty_received,date_ordered,supplier,notes,
                        custom_product_code,custom_product_name,custom_supplier,custom_colour)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (job_map[selected_job], None, required, received, ordered.isoformat(), supplier, notes,
                     code, product, supplier, colour),
                )
                st.success("Material entry saved.")
                rerun()
    with review_tab:
        rows = df_query(
            """SELECT m.id,j.job_no AS Job_No,j.job_name AS Job,
                      COALESCE(p.product_name,m.custom_product_name,'') AS Product,
                      COALESCE(p.product_code,m.custom_product_code,'') AS Code,
                      m.custom_colour AS Colour,m.qty_required AS Required,m.qty_received AS Received,
                      COALESCE(m.supplier,m.custom_supplier,'') AS Supplier,m.date_ordered AS Ordered,m.notes AS Notes
               FROM material_entries m JOIN jobs j ON j.id=m.job_id LEFT JOIN products p ON p.id=m.product_id
               ORDER BY m.id DESC"""
        )
        st.dataframe(rows, width="stretch", hide_index=True)
        st.download_button("Download materials CSV", rows.to_csv(index=False).encode("utf-8"), "PB_JobHub_materials.csv", "text/csv")


def variations_page() -> None:
    page_header("Variations", "Track scope changes, approvals and values against jobs.", "Commercial")
    jobs = all_jobs()
    add_tab, review_tab = st.tabs(["Add variation", "Review variations"])
    with add_tab:
        if not is_manager():
            st.warning("Only managers and administrators can add variations.")
        elif jobs.empty:
            st.info("Create a job first.")
        else:
            job_map = {f"{r.job_no} — {r.job_name}": int(r.job_id) for r in jobs.itertuples()}
            with st.form("variation_form"):
                selected_job = st.selectbox("Job", list(job_map))
                c1, c2, c3 = st.columns(3)
                variation_no = c1.text_input("Variation number")
                amount = c2.number_input("Amount ex GST", min_value=0.0, value=0.0, step=100.0)
                status = c3.selectbox("Status", ["Draft", "Sent", "Approved", "Rejected", "Invoiced"])
                description = st.text_area("Description")
                reason = st.text_area("Reason")
                approved_by = st.text_input("Approved by")
                notes = st.text_area("Notes")
                save = st.form_submit_button("Save variation", type="primary")
            if save:
                execute(
                    """INSERT INTO job_variations
                       (job_id,variation_no,description,reason,amount_ex_gst,status,sent_date,approved_date,approved_by,notes,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (job_map[selected_job], variation_no, description, reason, amount, status,
                     date.today().isoformat() if status in {"Sent", "Approved", "Invoiced"} else "",
                     date.today().isoformat() if status == "Approved" else "", approved_by, notes,
                     datetime.now().isoformat(sep=" ")),
                )
                st.success("Variation saved.")
                rerun()
    with review_tab:
        rows = df_query(
            """SELECT v.id,j.job_no AS Job_No,j.job_name AS Job,v.variation_no AS Variation,
                      v.description AS Description,v.amount_ex_gst AS Amount_Ex_GST,v.status AS Status,
                      v.approved_by AS Approved_By,v.approved_date AS Approved_Date,v.notes AS Notes
               FROM job_variations v JOIN jobs j ON j.id=v.job_id ORDER BY v.id DESC"""
        )
        st.dataframe(rows, width="stretch", hide_index=True)
        st.download_button("Download variations CSV", rows.to_csv(index=False).encode("utf-8"), "PB_JobHub_variations.csv", "text/csv")


def reports_page() -> None:
    page_header("Reports and exports", "Download current JobHub data for backup, payroll and planning.", "Reporting")
    datasets = {
        "Jobs": all_jobs(),
        "Staff": df_query("SELECT * FROM employees ORDER BY name"),
        "Schedule": df_query(
            """SELECT s.*,e.name AS employee,j.job_no,j.job_name FROM staff_schedule s
               JOIN employees e ON e.id=s.employee_id JOIN jobs j ON j.id=s.job_id ORDER BY s.schedule_date DESC"""
        ),
        "Timesheets": df_query(
            """SELECT t.*,e.name AS employee,j.job_no,j.job_name FROM timesheet_entries t
               JOIN employees e ON e.id=t.employee_id JOIN jobs j ON j.id=t.job_id ORDER BY t.work_date DESC"""
        ),
        "Materials": df_query("SELECT * FROM material_entries ORDER BY id DESC"),
        "Variations": df_query("SELECT * FROM job_variations ORDER BY id DESC"),
    }
    for name, frame in datasets.items():
        with st.expander(f"{name} — {len(frame)} row(s)"):
            st.dataframe(frame, width="stretch", hide_index=True)
            st.download_button(
                f"Download {name} CSV", frame.to_csv(index=False).encode("utf-8"),
                f"PB_JobHub_{name.lower()}_{date.today()}.csv", "text/csv", key=f"download_{name}",
            )
    if not USE_POSTGRES and os.path.exists(DB_PATH):
        st.download_button("Download complete local JobHub database", Path(DB_PATH).read_bytes(), "PB_JobHub_complete_backup.db", "application/octet-stream")
    else:
        st.info("Live PostgreSQL data remains in your existing Render/Supabase database. Use the CSV exports above or your database provider's backup tools.")


def users_page() -> None:
    page_header("User access", "Create accounts, link staff and reset passwords.", "Administration")
    if not is_manager():
        st.warning("Only managers and administrators can access this page.")
        return
    users = df_query(
        """SELECT u.id,u.username,u.role,u.employee_id,u.active,u.notes,COALESCE(e.name,'') AS employee
           FROM app_users u LEFT JOIN employees e ON e.id=u.employee_id ORDER BY u.username"""
    )
    st.dataframe(users[["username", "role", "employee", "active", "notes"]], width="stretch", hide_index=True)
    staff = df_query("SELECT id,name FROM employees ORDER BY name")
    staff_map = {"No staff link": None, **{str(r.name): int(r.id) for r in staff.itertuples()}}
    with st.form("create_user_form"):
        st.subheader("Create user")
        c1, c2, c3 = st.columns(3)
        username = c1.text_input("Username")
        password = c2.text_input("Temporary password", type="password")
        role = c3.selectbox("Role", ["employee", "manager", "admin"])
        employee = st.selectbox("Linked staff member", list(staff_map))
        notes = st.text_input("Notes")
        create = st.form_submit_button("Create user", type="primary")
    if create:
        employee_id = staff_map[employee]
        if not username.strip() or not password:
            st.error("Username and password are required.")
        elif employee_id is not None and not df_query("SELECT id FROM app_users WHERE employee_id=?", (employee_id,)).empty:
            st.error("That employee is already linked to another user account.")
        else:
            try:
                execute(
                    "INSERT INTO app_users (username,password_hash,role,employee_id,active,notes) VALUES (?,?,?,?,?,?)",
                    (username.strip(), hash_password(password), role, employee_id, 1, notes),
                )
                st.success("User created.")
                rerun()
            except Exception as exc:
                st.error(f"Could not create user: {exc}")
    if not users.empty:
        user_map = {f"{r.username} — {r.role}": int(r.id) for r in users.itertuples()}
        chosen = st.selectbox("Existing user", list(user_map), key="existing_user_choice")
        chosen_id = user_map[chosen]
        selected_row = users[users["id"] == chosen_id].iloc[0]
        with st.form("update_user_form"):
            c1, c2, c3 = st.columns(3)
            new_role = c1.selectbox("New role", ["employee", "manager", "admin"], index=["employee", "manager", "admin"].index(str(selected_row["role"])) if str(selected_row["role"]) in ["employee", "manager", "admin"] else 0)
            active = c2.checkbox("Active", value=int(selected_row["active"] or 0) == 1)
            new_password = c3.text_input("New password (leave blank to keep)", type="password")
            save = st.form_submit_button("Update user")
        if save:
            execute("UPDATE app_users SET role=?,active=? WHERE id=?", (new_role, 1 if active else 0, chosen_id))
            if new_password:
                execute("UPDATE app_users SET password_hash=? WHERE id=?", (hash_password(new_password), chosen_id))
            st.success("User updated.")
            rerun()


def system_page() -> None:
    page_header("System", "Deployment and database checks for this complete replacement build.", "Settings")
    st.write(f"**JobHub version:** {APP_VERSION}")
    st.write(f"**Database mode:** {'PostgreSQL / Supabase' if USE_POSTGRES else 'Local SQLite'}")
    st.write(f"**Local data path:** `{DB_PATH}`")
    checks = []
    for table in ["jobs", "employees", "staff_schedule", "app_users", "timesheet_entries", "material_entries", "job_variations"]:
        try:
            count = int(df_query(f"SELECT COUNT(*) AS count FROM {table}").iloc[0]["count"])
            checks.append({"Table": table, "Rows": count, "Status": "OK"})
        except Exception as exc:
            checks.append({"Table": table, "Rows": "", "Status": str(exc)})
    st.dataframe(pd.DataFrame(checks), width="stretch", hide_index=True)
    st.info("To keep your live records, retain the same DATABASE_URL when replacing the old code on Render.")


# ============================================================
# SIDEBAR AND ROUTING
# ============================================================

st.sidebar.markdown(
    """
    <div class="pb-brand">
      <div class="pb-brand-mark">PB</div>
      <div class="pb-brand-title">Premier Brushworks<br>JobHub</div>
      <div class="pb-brand-sub">Jobs and staff scheduling</div>
    </div>
    """,
    unsafe_allow_html=True,
)
user = current_user() or {}
st.sidebar.write(f"Signed in as **{user.get('username', '')}**")
st.sidebar.caption(f"Role: {user.get('role', '')} · v{APP_VERSION}")

if is_manager():
    menu_items = [
        "Dashboard", "Jobs", "Staff Scheduler", "Staff", "Timesheets",
        "Materials", "Variations", "Reports", "User Access", "System",
    ]
else:
    menu_items = ["My Schedule", "Jobs", "Timesheets", "Staff"]

menu = st.sidebar.radio("Navigation", menu_items, label_visibility="collapsed")
if st.sidebar.button("Log out"):
    st.session_state["jobhub_user"] = None
    st.rerun()

try:
    if menu == "Dashboard":
        dashboard_page()
    elif menu == "Jobs":
        jobs_page()
    elif menu == "Staff Scheduler":
        page_header("Staff Scheduler", "See every job, every staff member and every allocation in one visual board.", "Planning")
        render_jobhub_staff_scheduler(
            df_query=df_query,
            execute=execute,
            connect=connect,
            use_postgres=USE_POSTGRES,
            current_username=current_username,
            refresh=rerun,
        )
    elif menu == "Staff":
        staff_page()
    elif menu == "My Schedule":
        my_schedule_page()
    elif menu == "Timesheets":
        timesheets_page()
    elif menu == "Materials":
        materials_page()
    elif menu == "Variations":
        variations_page()
    elif menu == "Reports":
        reports_page()
    elif menu == "User Access":
        users_page()
    elif menu == "System":
        system_page()
except Exception as exc:
    st.error("JobHub encountered an error on this page.")
    st.exception(exc)
