import sqlite3
import os
import tempfile
from urllib.parse import urlparse
import shutil
import base64
import hashlib
import re
import json
from pathlib import Path
from datetime import date, datetime, timedelta
from io import BytesIO

import pandas as pd
from PIL import Image
import requests
import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from pypdf import PdfReader
import streamlit as st


# =============================
# APP PATHS / PERSISTENT STORAGE
# =============================

DATA_DIR = os.getenv("DATA_DIR", "/var/data")

DB_PATH = os.path.join(DATA_DIR, "jobhub.db")
JOB_FILES_DIR = os.path.join(DATA_DIR, "job_files")
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
EXPORTS_DIR = os.path.join(DATA_DIR, "exports")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(JOB_FILES_DIR, exist_ok=True)
os.makedirs(PHOTOS_DIR, exist_ok=True)
os.makedirs(EXPORTS_DIR, exist_ok=True)


def get_job_folder(job_number):
    folder = os.path.join(JOB_FILES_DIR, str(job_number))
    os.makedirs(folder, exist_ok=True)
    return folder


st.set_page_config(page_title="Premier Brushworks JobHub", layout="wide")


def get_database_url():
    # Streamlit Cloud: add DATABASE_URL under App > Settings > Secrets.
    try:
        if "DATABASE_URL" in st.secrets:
            return st.secrets["DATABASE_URL"]
    except Exception:
        pass

    # Local/server fallback: environment variable.
    return os.environ.get("DATABASE_URL", "")


DATABASE_URL = get_database_url()
USE_POSTGRES = bool(DATABASE_URL)


# =============================
# TEMP STORAGE TEST - REMOVE LATER
# =============================

st.sidebar.markdown("### Storage Check")
st.sidebar.write("DATA_DIR:", DATA_DIR)
st.sidebar.write("DB_PATH:", DB_PATH)
st.sidebar.write("Using Postgres:", USE_POSTGRES)
st.sidebar.write("DATA_DIR exists:", os.path.exists(DATA_DIR))
st.sidebar.write("JOB_FILES_DIR exists:", os.path.exists(JOB_FILES_DIR))

test_file_path = os.path.join(DATA_DIR, "persistent_test.txt")

if st.sidebar.button("Test Persistent Disk"):
    with open(test_file_path, "a") as f:
        f.write(f"Test saved at {datetime.now()}\n")
    st.sidebar.success("Test file saved.")

if os.path.exists(test_file_path):
    with open(test_file_path, "r") as f:
        lines = f.readlines()
    st.sidebar.success(f"Persistent test file exists with {len(lines)} saved test line(s).")
else:
    st.sidebar.warning("No persistent test file found yet.")
Add this after this line:

USE_POSTGRES = bool(DATABASE_URL)

Add:

# =============================
# TEMP STORAGE TEST - REMOVE LATER
# =============================

st.sidebar.markdown("### Storage Check")
st.sidebar.write("DATA_DIR:", DATA_DIR)
st.sidebar.write("DB_PATH:", DB_PATH)
st.sidebar.write("Using Postgres:", USE_POSTGRES)
st.sidebar.write("DATA_DIR exists:", os.path.exists(DATA_DIR))
st.sidebar.write("JOB_FILES_DIR exists:", os.path.exists(JOB_FILES_DIR))

test_file_path = os.path.join(DATA_DIR, "persistent_test.txt")

if st.sidebar.button("Test Persistent Disk"):
    with open(test_file_path, "a") as f:
        f.write(f"Test saved at {datetime.now()}\n")
    st.sidebar.success("Test file saved.")

if os.path.exists(test_file_path):
    with open(test_file_path, "r") as f:
        lines = f.readlines()
    st.sidebar.success(f"Persistent test file exists with {len(lines)} saved test line(s).")
else:
    st.sidebar.warning("No persistent test file found yet.")
@st.cache_resource
def get_postgres_pool():
    """
    Reusable Supabase/PostgreSQL connection pool.
    This avoids opening a brand new database connection for every query.
    """
    if not DATABASE_URL:
        return None

    return ThreadedConnectionPool(
        minconn=1,
        maxconn=5,
        dsn=DATABASE_URL,
        sslmode="require",
    )



# =============================
# DATABASE
# =============================

def normalise_seed_rows(rows, expected_columns):
    fixed_rows = []
    for row in rows:
        row = list(row)
        if len(row) < expected_columns:
            row = row + [""] * (expected_columns - len(row))
        elif len(row) > expected_columns:
            row = row[:expected_columns]
        fixed_rows.append(tuple(row))
    return fixed_rows


def get_app_setting(key, default=""):
    try:
        conn = connect()
        cur = conn.cursor()
        cur.execute("SELECT setting_value FROM app_settings WHERE setting_key = ?", (key,))
        row = cur.fetchone()
        conn.close()
        if row:
            return row[0]
    except Exception:
        return default
    return default


def set_app_setting(key, value):
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO app_settings (setting_key, setting_value)
        VALUES (?, ?)
    """, (key, value))
    conn.commit()
    conn.close()


def starter_data_already_seeded():
    return get_app_setting("starter_data_seeded", "") == "yes"



def adapt_sql_for_postgres(sql):
    if not USE_POSTGRES:
        return sql

    original_sql = sql
    s = sql.strip()

    # PostgreSQL alias names with spaces need double quotes, not single quotes.
    s = re.sub(r"AS '([^']+)'", r'AS "\1"', s)

    # SQLite autoincrement syntax -> PostgreSQL serial syntax.
    s = s.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")

    # PostgreSQL ROUND(double precision, integer) is not valid; cast simple expressions to numeric.
    s = re.sub(
        r"ROUND\(([^()]+),\s*2\)",
        r"ROUND(CAST(\1 AS numeric), 2)",
        s
    )

    # Convert INSERT OR IGNORE to PostgreSQL ON CONFLICT DO NOTHING.
    if re.search(r"INSERT\s+OR\s+IGNORE\s+INTO", s, flags=re.IGNORECASE):
        s = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", s, flags=re.IGNORECASE)
        if "ON CONFLICT" not in s.upper():
            s = s.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    # Convert INSERT OR REPLACE to PostgreSQL upsert.
    if re.search(r"INSERT\s+OR\s+REPLACE\s+INTO", s, flags=re.IGNORECASE):
        m = re.match(
            r"INSERT\s+OR\s+REPLACE\s+INTO\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*?)\)\s*VALUES\s*\((.*?)\)\s*$",
            s,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if m:
            table = m.group(1)
            columns_text = m.group(2)
            values_text = m.group(3)

            columns = [c.strip() for c in columns_text.replace("\n", " ").split(",")]
            conflict_targets = {
                "app_settings": "setting_key",
                "jobs": "job_no",
                "builders_clients": "name",
                "employees": "name",
                "products": "product_code",
                "equipment_checklist_items": "item_name",
                "app_users": "username",
            }
            conflict_col = conflict_targets.get(table)

            if conflict_col:
                updates = [
                    f"{col} = EXCLUDED.{col}"
                    for col in columns
                    if col != conflict_col
                ]
                s = (
                    f"INSERT INTO {table} ({', '.join(columns)}) "
                    f"VALUES ({values_text}) "
                    f"ON CONFLICT ({conflict_col}) DO UPDATE SET {', '.join(updates)}"
                )
            else:
                s = re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO", "INSERT INTO", s, flags=re.IGNORECASE)
                s = s.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        else:
            s = re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO", "INSERT INTO", s, flags=re.IGNORECASE)

    # SQLite placeholders ? -> psycopg2 placeholders %s.
    s = s.replace("?", "%s")

    # Psycopg2 uses % for parameter formatting. Any literal % in SQL, such as
    # a column alias "Rate + 10%", must be escaped as %% or psycopg2 can crash
    # with "IndexError: tuple index out of range".
    s = re.sub(r"%(?!s)", "%%", s)

    return s


class PostgresCursorAdapter:
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, sql, params=()):
        return self.cursor.execute(adapt_sql_for_postgres(sql), params)

    def executemany(self, sql, rows):
        return self.cursor.executemany(adapt_sql_for_postgres(sql), rows)

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    @property
    def description(self):
        return self.cursor.description

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def __iter__(self):
        return iter(self.cursor)

    def __getattr__(self, name):
        return getattr(self.cursor, name)


class PostgresConnectionAdapter:
    def __init__(self, conn, pool=None):
        self.conn = conn
        self.pool = pool
        self._closed = False

    def cursor(self):
        return PostgresCursorAdapter(self.conn.cursor())

    def commit(self):
        return self.conn.commit()

    def rollback(self):
        return self.conn.rollback()

    def close(self):
        """
        In Supabase mode this returns the connection to the cached pool instead
        of closing it completely.
        """
        if self._closed:
            return

        self._closed = True

        if self.pool is not None:
            try:
                self.pool.putconn(self.conn)
            except Exception:
                try:
                    self.pool.putconn(self.conn, close=True)
                except Exception:
                    pass
        else:
            self.conn.close()

    def __getattr__(self, name):
        return getattr(self.conn, name)


def connect():
    if USE_POSTGRES:
        pool = get_postgres_pool()
        raw_conn = pool.getconn()
        return PostgresConnectionAdapter(raw_conn, pool)

    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    conn = connect()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS builders_clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT,
        name TEXT UNIQUE,
        contact_name TEXT,
        phone TEXT,
        email TEXT,
        address TEXT,
        qbcc TEXT,
        abn TEXT,
        terms TEXT,
        notes TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_no TEXT UNIQUE,
        job_name TEXT,
        builder_client_id INTEGER,
        site_address TEXT,
        status TEXT,
        leading_hand TEXT,
        start_date TEXT,
        end_date TEXT,
        contract_value REAL,
        notes TEXT,
        FOREIGN KEY(builder_client_id) REFERENCES builders_clients(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_code TEXT UNIQUE,
        product_name TEXT,
        supplier TEXT,
        unit TEXT,
        price_ex_gst REAL,
        notes TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS employees (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        role TEXT,
        phone TEXT,
        base_hourly_rate REAL,
        rate_plus_10 REAL,
        status TEXT,
        notes TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS material_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER,
        product_id INTEGER,
        qty_required REAL,
        qty_received REAL,
        date_ordered TEXT,
        supplier TEXT,
        notes TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id),
        FOREIGN KEY(product_id) REFERENCES products(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS wage_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER,
        employee_id INTEGER,
        work_date TEXT,
        hours REAL,
        notes TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id),
        FOREIGN KEY(employee_id) REFERENCES employees(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS equipment_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        equipment_item TEXT,
        category TEXT,
        serial_no TEXT,
        job_id INTEGER,
        date_out TEXT,
        date_in TEXT,
        condition_out TEXT,
        condition_in TEXT,
        assigned_to TEXT,
        notes TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS equipment_checklist_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT,
        item_name TEXT UNIQUE,
        default_qty REAL,
        notes TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS equipment_checklist_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        checklist_item_id INTEGER NOT NULL,
        qty_required REAL DEFAULT 0,
        qty_taken REAL DEFAULT 0,
        qty_returned REAL DEFAULT 0,
        is_required INTEGER DEFAULT 0,
        is_packed INTEGER DEFAULT 0,
        is_returned INTEGER DEFAULT 0,
        date_out TEXT,
        date_in TEXT,
        taken_by TEXT,
        returned_by TEXT,
        condition_out TEXT,
        condition_in TEXT,
        notes TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id),
        FOREIGN KEY(checklist_item_id) REFERENCES equipment_checklist_items(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS imported_material_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        product TEXT,
        colour TEXT,
        qty_required TEXT,
        qty_loaded TEXT,
        source_file TEXT,
        imported_at TEXT,
        notes TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id)
    )
    """)



    cur.execute("""
    CREATE TABLE IF NOT EXISTS timesheet_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        employee_id INTEGER NOT NULL,
        work_date TEXT,
        start_time TEXT,
        finish_time TEXT,
        break_minutes REAL DEFAULT 0,
        total_hours REAL DEFAULT 0,
        work_type TEXT,
        submitted_by TEXT,
        submitted_at TEXT,
        status TEXT DEFAULT 'Submitted',
        notes TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id),
        FOREIGN KEY(employee_id) REFERENCES employees(id)
    )
    """)




    cur.execute("""
    CREATE TABLE IF NOT EXISTS estimate_working_sheets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        estimate_no TEXT,
        estimate_date TEXT,
        revision TEXT,
        status TEXT,
        labour_hours REAL DEFAULT 0,
        labour_rate REAL DEFAULT 0,
        material_allowance REAL DEFAULT 0,
        access_equipment_allowance REAL DEFAULT 0,
        subcontractor_allowance REAL DEFAULT 0,
        sundries_allowance REAL DEFAULT 0,
        margin_percent REAL DEFAULT 0,
        contingency_percent REAL DEFAULT 0,
        gst_percent REAL DEFAULT 10,
        total_ex_gst REAL DEFAULT 0,
        gst_amount REAL DEFAULT 0,
        total_inc_gst REAL DEFAULT 0,
        created_at TEXT,
        updated_at TEXT,
        notes TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS estimate_line_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        estimate_id INTEGER NOT NULL,
        section TEXT,
        item_description TEXT,
        qty REAL DEFAULT 0,
        unit TEXT,
        unit_rate REAL DEFAULT 0,
        line_total REAL DEFAULT 0,
        notes TEXT,
        FOREIGN KEY(estimate_id) REFERENCES estimate_working_sheets(id)
    )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_timesheet_entries_job_id ON timesheet_entries(job_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_estimate_working_sheets_job_id ON estimate_working_sheets(job_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_estimate_line_items_estimate_id ON estimate_line_items(estimate_id)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS job_photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        photo_name TEXT,
        photo_type TEXT,
        photo_data TEXT,
        category TEXT,
        caption TEXT,
        uploaded_by TEXT,
        uploaded_at TEXT,
        notes TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS app_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password_hash TEXT,
        role TEXT,
        employee_id INTEGER,
        active INTEGER DEFAULT 1,
        notes TEXT,
        FOREIGN KEY(employee_id) REFERENCES employees(id)
    )
    """)


    cur.execute("""
    CREATE TABLE IF NOT EXISTS job_budgets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER UNIQUE,
        quoted_labour_hours REAL DEFAULT 0,
        quoted_labour_cost REAL DEFAULT 0,
        quoted_materials REAL DEFAULT 0,
        quoted_access_equipment REAL DEFAULT 0,
        quoted_subcontractors REAL DEFAULT 0,
        quoted_sundries REAL DEFAULT 0,
        target_gp_percent REAL DEFAULT 35,
        locked_at TEXT,
        locked_by TEXT,
        notes TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS job_variations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER,
        variation_no TEXT,
        description TEXT,
        reason TEXT,
        amount_ex_gst REAL DEFAULT 0,
        status TEXT DEFAULT 'Draft',
        sent_date TEXT,
        approved_date TEXT,
        approved_by TEXT,
        notes TEXT,
        created_at TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS invoice_claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER,
        claim_no TEXT,
        description TEXT,
        amount_ex_gst REAL DEFAULT 0,
        invoice_date TEXT,
        due_date TEXT,
        paid_date TEXT,
        status TEXT DEFAULT 'Draft',
        notes TEXT,
        created_at TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id)
    )
    """)

    cur.execute("""
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
        FOREIGN KEY(job_id) REFERENCES jobs(id),
        FOREIGN KEY(employee_id) REFERENCES employees(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS app_code_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        request TEXT,
        ai_response TEXT,
        patch_json TEXT,
        target_files TEXT,
        status TEXT,
        created_at TEXT,
        applied_at TEXT,
        result_message TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS app_learning_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic TEXT,
        url TEXT,
        active INTEGER DEFAULT 1,
        last_checked TEXT,
        last_summary TEXT,
        notes TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS app_builder_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic TEXT,
        note TEXT,
        source TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS app_settings (
        setting_key TEXT PRIMARY KEY,
        setting_value TEXT
    )
    """)

    conn.commit()
    conn.close()


def df_query(sql, params=()):
    """
    Query helper.
    In Supabase mode this uses the cached connection pool through connect().
    """
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description] if cur.description else []
        return pd.DataFrame(rows, columns=columns)
    finally:
        conn.close()


def execute(sql, params=()):
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def execute_many(sql, rows):
    conn = connect()
    try:
        cur = conn.cursor()
        cur.executemany(sql, rows)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def refresh():
    st.rerun()


def get_builder_options():
    df = df_query("SELECT id, name FROM builders_clients ORDER BY name")
    return {str(row["name"]): int(row["id"]) for _, row in df.iterrows()}


def get_employee_options(active_only=False):
    where = "WHERE status = 'Active'" if active_only else ""
    df = df_query(f"SELECT id, name FROM employees {where} ORDER BY name")
    return {str(row["name"]): int(row["id"]) for _, row in df.iterrows()}


def get_job_options():
    df = df_query("""
        SELECT id, job_no || ' - ' || COALESCE(job_name, '') AS label
        FROM jobs
        ORDER BY job_no
    """)
    return {str(row["label"]): int(row["id"]) for _, row in df.iterrows()}


def get_product_options():
    df = df_query("SELECT id, product_code FROM products ORDER BY product_code")
    return {str(row["product_code"]): int(row["id"]) for _, row in df.iterrows()}


def get_product_name_options():
    df = df_query("""
        SELECT id, product_name, product_code
        FROM products
        ORDER BY product_name
    """)
    return {f"{row['product_name']} ({row['product_code']})": int(row["id"]) for _, row in df.iterrows()}


def next_job_no():
    df = df_query("SELECT job_no FROM jobs WHERE job_no LIKE 'PB%' ORDER BY job_no DESC LIMIT 1")
    if df.empty:
        return "PB25001"

    last = str(df.iloc[0]["job_no"])
    digits = "".join(c for c in last if c.isdigit())
    prefix = "".join(c for c in last if not c.isdigit())

    if not digits:
        return "PB25001"

    return f"{prefix}{int(digits) + 1:05d}"


def has_related_records(table, field, record_id):
    df = df_query(f"SELECT COUNT(*) AS c FROM {table} WHERE {field} = ?", (record_id,))
    return int(df.iloc[0]["c"]) > 0


# =============================
# STARTER DATA
# =============================
def seed_data():
    conn = connect()
    cur = conn.cursor()

    # Seed starter/demo data only once.
    # This prevents deleted starter jobs, builders, employees, products, or equipment items
    # from reappearing every time the app starts.
    if starter_data_already_seeded():
        conn.close()
        return


    builders = [
        ("Builder","Ausmar Homes Pty Ltd","Compliance Team","07 5319 1500","compliance@ausmargroup.com.au","8 Flinders Lane, Maroochydore QLD 4558","1083000","55 087 236 208","30 Days","Annual Period Trade Contract"),
        ("Developer / Builder","OneLife Property Group","Bryce Curran","0421 069 817","brycecurran@hotmail.com","Sunshine Coast","","","30 Days","Multi-residential complexes"),
        ("Builder","Thompson Homes","","","","","","","30 Days","Existing JobHub builder"),
        ("Client / Developer","Palm Lakes","","","","Pelican Waters","","","30 Days","Palm Lakes Pelican Waters"),
        ("Interior Designer","Box Clever Interiors","Design Team","07 5309 5640","info@boxcleverinteriors.com.au","PO Box 208, Moffat Beach QLD 4551","","08 007 428 613","","Bannister project designer"),
        ("Interior Designer","Inka Interiors","Sheena Hanks","0438 308 672","info@inkainteriors.com.au","Basement Level, 811 Stanley St, Woolloongabba","","","","Cunningham project designer"),
        ("Painting Contractor","Emerald Painting Company Pty Ltd","Anthony Des Johnston","0410 949 719","des@emeraldpainting.com.au","20 Warenna Crescent, Glenvale QLD 4350","","85 169 333 957","","Industry contact"),
        ("Supplier","Dulux Australia","","07 5443 7255","","Cnr Amaroo St & Maroochydore Rd, Maroochydore QLD 4558","","67 000 049 427","","Supplier"),
        ("Builder","Greenrock Building","","","","","","","30 Days","Client history"),
        ("Builder","Rejuvenate Group","","","","","","","30 Days","School works"),
        ("Builder","Adlar Homes","","","","Maroochydore","","","30 Days","Client history"),
        ("Builder","Darren Hunt Homes","","","","","","","30 Days","Custom homes"),
        ("Builder","Watherston Building","","","","","","","30 Days","Custom homes"),
        ("Commercial Client","Stockland Aura","","","","Aura","","","","Commercial developments"),
        ("Commercial Builder","FDC Constructions","Simon Hawkins / Adam Pickering","","","","","","","Outreach"),
        ("Commercial Client","Comiskey Group","Paul / David / Rob & team","","","Sunshine Coast","","","","Hospitality venue"),
        ("Education Client","Nambour State College","","","","Nambour","","","","School works"),
        ("Education Client","Currimundi State School","","","","Currimundi","","","","School works"),
        ("Education Client","Currimundi Special School","","","","Currimindi","","","","School works"),
        ("Education Client","Gympie South State School","","","","Gympie","","","","School works"),
        ("Education Client","Good Shepherd Lutheran School","","","","","","","","School works"),
    ]

    builders = [tuple(list(row) + [""] * (10 - len(row)))[:10] for row in builders]

    builders = normalise_seed_rows(builders, 10)

    cur.executemany("""
        INSERT OR IGNORE INTO builders_clients
        (type, name, contact_name, phone, email, address, qbcc, abn, terms, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, builders)

    products = [
        ("PB-H00001","Coverplus Interior L/S White","Haymes","",168.00,""),
        ("PB-H00002","Elite Ceiling Toned White, 15L","Haymes","15L",90.00,""),
        ("PB-H00003","Elite Ceiling White, 15L","Haymes","15L",90.00,""),
        ("PB-H00004","Elite Interior Low Sheen White","Haymes","",118.00,""),
        ("PB-H00005","Elite Interior Matt White, 15L","Haymes","15L",125.00,""),
        ("PB-H00006","Elite Acrylic Sealer Undercoat","Haymes","",105.36,""),
        ("PB-H00007","Elite Quick Dry Primer Undercoat","Haymes","",123.55,""),
        ("PB-H00008","Expressions Low Sheen DKT, 4L","Haymes","4L",74.13,""),
        ("PB-H00009","Expressions Low Sheen EDT, 4L","Haymes","4L",74.13,""),
        ("PB-H00010","Expressions Low Sheen UDT, 4L","Haymes","4L",74.13,""),
        ("PB-H00011","Expressions Low Sheen White","Haymes","",107.48,""),
        ("PB-H00012","Expressions Low Sheen White","Haymes","",145.00,""),
        ("PB-H00013","Expressions Low Sheen White, 4L","Haymes","4L",67.26,""),
        ("PB-H00014","Solashield Low Sheen DKT, 10L","Haymes","10L",115.00,""),
        ("PB-H00015","Solashield Low Sheen DKT, 15L","Haymes","15L",160.00,""),
        ("PB-H00016","Solashield Low Sheen DKT, 4L","Haymes","4L",73.55,""),
        ("PB-H00017","Solashield Low Sheen EDT, 10L","Haymes","10L",115.00,""),
        ("PB-H00018","Solashield Low Sheen EDT, 15L","Haymes","15L",160.00,""),
        ("PB-H00019","Solashield Low Sheen EDT, 4L","Haymes","4L",73.55,""),
        ("PB-H00020","Solashield Low Sheen UDT, 10L","Haymes","10L",115.00,""),
        ("PB-H00021","Solashield Low Sheen UDT, 15L","Haymes","15L",160.00,""),
        ("PB-H00022","Solashield Low Sheen UDT, 4L","Haymes","4L",73.55,""),
        ("PB-H00023","Solashield Low Sheen White, 10L","Haymes","10L",107.42,""),
        ("PB-H00024","Solashield Low Sheen White, 15L","Haymes","15L",148.00,""),
        ("PB-H00025","Solashield Low Sheen White, 4L","Haymes","4L",67.40,""),
        ("PB-H00026","R/Tex Roll On Coarse, 15L","Haymes","15L",175.00,""),
        ("PB-H00027","Solashield Satin DKT, 15L","Haymes","15L",160.00,""),
        ("PB-H00028","Solashield Satin EDT, 15L","Haymes","15L",160.00,""),
        ("PB-H00029","Solashield Satin UDT, 15L","Haymes","15L",160.00,""),
        ("PB-H00030","Solashield Satin White, 10L","Haymes","10L",115.00,""),
        ("PB-H00031","Solashield Satin White, 15L","Haymes","15L",148.00,""),
        ("PB-H00032","Ultra Premium Primer Sealer","Haymes","",167.46,""),
        ("PB-H00033","Acrylic Sealer Undercoat","Haymes","",120.00,""),
        ("PB-H00034","Ultratrim High Gloss White","Haymes","",130.00,""),
        ("PB-H00035","Ultratrim Semi Gloss White","Haymes","",130.00,""),
        ("PB-H00036","Woodcare Aqualac Floor Satin","Haymes","",250.44,""),
    ]

    products = normalise_seed_rows(products, 6)

    cur.executemany("""
        INSERT OR IGNORE INTO products
        (product_code, product_name, supplier, unit, price_ex_gst, notes)
        VALUES (?, ?, ?, ?, ?, ?)
    """, products)

    employees = [
        ("Bryce","", "",60.00,66.00,"Active",""),
        ("Brodrick","", "",45.00,49.50,"Active",""),
        ("Sol","", "",50.00,55.00,"Active",""),
        ("Critter","", "",40.00,44.00,"Active",""),
        ("Greg","", "",46.00,50.60,"Active",""),
        ("Chris Nagy","", "",50.00,55.00,"Active",""),
        ("Isaac","", "",46.00,50.60,"Active",""),
        ("Rob Pullin","", "",45.00,49.50,"Active",""),
        ("Ian","", "",46.00,50.60,"Active",""),
        ("Tim","", "",45.00,49.50,"Active",""),
        ("Anth","", "",35.00,38.50,"Active",""),
        ("River","", "",32.50,35.75,"Active",""),
        ("Dipper","", "",45.00,49.50,"Active",""),
        ("Vlad 1","", "",45.00,49.50,"Active",""),
        ("Vlad 2","", "",45.00,49.50,"Active",""),
        ("Ryan","", "",45.00,49.50,"Active",""),
    ]

    cur.executemany("""
        INSERT OR IGNORE INTO employees
        (name, role, phone, base_hourly_rate, rate_plus_10, status, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, employees)

    equipment_items = [
        ("Access", "Extension ladders", 0, ""),
        ("Access", "Platform ladders", 0, ""),
        ("Access", "Step ladders 6ft", 0, ""),
        ("Access", "Step ladders 4ft", 0, ""),
        ("Access", "Trestles", 0, ""),
        ("Access", "Planks", 0, ""),
        ("Access", "Scaffold / mobile scaffold", 0, ""),
        ("Access", "Harness / height safety gear", 0, ""),
        ("Spray Equipment", "Graco airless sprayer", 0, ""),
        ("Spray Equipment", "Titan sprayer", 0, ""),
        ("Spray Equipment", "Spray gun", 0, ""),
        ("Spray Equipment", "Spray tips", 0, ""),
        ("Spray Equipment", "Tip guards", 0, ""),
        ("Spray Equipment", "Spray hose", 0, ""),
        ("Spray Equipment", "Whip hose", 0, ""),
        ("Sanding / Prep", "Mirka drywall sander", 0, ""),
        ("Sanding / Prep", "Mirka orbital sander", 0, ""),
        ("Sanding / Prep", "Dust extractor / vacuum", 0, ""),
        ("Sanding / Prep", "Hand sanders", 0, ""),
        ("Sanding / Prep", "Filler blades", 0, ""),
        ("Sanding / Prep", "Scrapers", 0, ""),
        ("Sanding / Prep", "Caulking guns", 0, ""),
        ("Painting Gear", "Brushes", 0, ""),
        ("Painting Gear", "Roller frames", 0, ""),
        ("Painting Gear", "Roller poles", 0, ""),
        ("Painting Gear", "Roller trays / buckets", 0, ""),
        ("Painting Gear", "Cut pots", 0, ""),
        ("Painting Gear", "Grids", 0, ""),
        ("Protection", "Canvas drop sheets", 0, ""),
        ("Protection", "Plastic drop sheets", 0, ""),
        ("Protection", "Masking machine", 0, ""),
        ("Protection", "Masking tape", 0, ""),
        ("Protection", "Masking paper", 0, ""),
        ("Protection", "Masking plastic", 0, ""),
        ("Power / Site Gear", "Extension leads", 0, ""),
        ("Power / Site Gear", "RCD safety switch", 0, ""),
        ("Power / Site Gear", "Battery chargers", 0, ""),
        ("Power / Site Gear", "Work lights", 0, ""),
        ("Power / Site Gear", "Fans", 0, ""),
        ("Power / Site Gear", "Cordless drill / driver", 0, ""),
        ("Wash Down", "Petrol pressure cleaner", 0, ""),
        ("Wash Down", "Hoses", 0, ""),
        ("Wash Down", "Wash brushes", 0, ""),
        ("Safety", "Safety glasses", 0, ""),
        ("Safety", "Respirators / P2 masks", 0, ""),
        ("Safety", "Gloves", 0, ""),
        ("Safety", "Hi-vis", 0, ""),
        ("Safety", "Barricades / exclusion zone gear", 0, ""),
        ("Safety", "First aid kit", 0, ""),
        ("Other", "Bins / rubbish bags", 0, ""),
        ("Other", "Cleaning gear", 0, ""),
    ]

    cur.executemany("""
        INSERT OR IGNORE INTO equipment_checklist_items
        (category, item_name, default_qty, notes)
        VALUES (?, ?, ?, ?)
    """, equipment_items)

    # Keep checklist starting quantities at zero by default, even for existing databases
    cur.execute("UPDATE equipment_checklist_items SET default_qty = 0 WHERE default_qty IS NULL OR default_qty != 0")

    # Starter/demo jobs are intentionally NOT auto-created.
    # This keeps the Job Register at 0 when all jobs are deleted.
    # Add real jobs manually from Jobs > Add Job.


    cur.execute("""
        INSERT OR REPLACE INTO app_settings (setting_key, setting_value)
        VALUES (?, ?)
    """, ("starter_data_seeded", "yes"))

    conn.commit()
    conn.close()



# =============================
# PDF CHECKLIST IMPORT HELPERS
# =============================
PDF_CHECKLIST_ITEMS = {
    "access": ("Access Equipment", [
        "4ft Step Ladder",
        "6ft Step Ladder",
        "8ft Step Ladder",
        "10ft Step Ladder",
        "3m Extension Ladder",
        "4.8m Extension Ladder",
        "6m Extension Ladder",
        "Door Stackers",
        "600mm Trestles",
        "900mm Trestles",
        "4m Planks",
        "5m Planks",
        "6m Planks",
    ]),
    "prep": ("Preparation Equipment", [
        "Mirka Dustless Sander",
        "Mirka Extractor",
        "Pole Sander",
        "Pressure Cleaner",
        "PowerShot",
        "Saw Stools",
        "Paper Machine",
        "Mixing Paddle",
        "Broom",
        "Dustpan",
        "Brush",
    ]),
    "painting": ("Painting Equipment", [
        "Graco Sprayguns",
        "Fine Finish Tips",
        "Standard Spray Tips",
        "Roller Frames 270mm",
        "Mini Roller Frames",
        "Roller Sleeves 270mm",
        "Mini Roller Sleeves",
        "Brushes",
        "Paint Trays",
        "Paint Pots",
    ]),
    "poles": ("Extension Poles", [
        "600mm Pole",
        "1200mm Pole",
        "1800mm Pole",
        "2400mm Pole",
        "Adjustable Pole",
    ]),
    "dewalt": ("DeWalt Electrical Tools", [
        "Impact Driver",
        "Hammer Drill",
        "Blower",
        "Sheet Sander",
        "Orbital Sander",
        "Grinder",
        "Work Light",
        "Bluetooth Speaker",
        "Battery Charger",
        "5Ah Battery",
        "Extension Leads",
        "RCD",
    ]),
    "cons": ("Consumables", [
        "Green Tape",
        "Yellow Tape",
        "Plastic Masking Film",
        "Black Plastic",
        "Canvas Drop Sheets",
        "Floor Protection Paper",
        "Gap Filler",
        "Plaster Filler",
        "Timber Filler",
        "Putty",
        "Bog",
        "Sugar Soap",
        "Mixing Sticks",
        "Sandpaper 80G",
        "Sandpaper 120G",
        "Sandpaper 180G",
        "Sandpaper 240G",
    ]),
}


def clean_pdf_value(value):
    if value is None:
        return ""
    text = str(value)
    if text in ["/Off", "Off", "None", "nan"]:
        return ""
    if text.startswith("/"):
        text = text[1:]
    return text.strip()


def pdf_field_value(fields, name):
    field = fields.get(name)
    if not field:
        return ""
    return clean_pdf_value(field.get("/V", ""))


def qty_to_float(value):
    text = clean_pdf_value(value)
    if not text:
        return 0.0
    match = re.search(r"[-+]?\d*\.?\d+", text)
    if not match:
        return 0.0
    try:
        return float(match.group(0))
    except ValueError:
        return 0.0


def is_pdf_tick(value):
    text = clean_pdf_value(value).lower()
    return bool(text and text not in ["off", "false", "0", "no"])


def parse_master_checklist_pdf(uploaded_file):
    reader = PdfReader(uploaded_file)
    fields = reader.get_fields() or {}

    job_info = {
        "job_number": pdf_field_value(fields, "p1_job_0"),
        "job_name": pdf_field_value(fields, "p1_job_1"),
        "site_address": pdf_field_value(fields, "p1_job_2"),
        "client_builder": pdf_field_value(fields, "p1_job_3"),
        "leading_hand": pdf_field_value(fields, "p1_team_0"),
        "crew_members": pdf_field_value(fields, "p1_team_1"),
        "team_extra": pdf_field_value(fields, "p1_team_extra"),
    }

    equipment_rows = []

    for prefix, (category, item_names) in PDF_CHECKLIST_ITEMS.items():
        for idx, item_name in enumerate(item_names):
            req = pdf_field_value(fields, f"{prefix}_{idx}_req")
            loaded = pdf_field_value(fields, f"{prefix}_{idx}_loaded")
            returned = pdf_field_value(fields, f"{prefix}_{idx}_returned")
            tick = pdf_field_value(fields, f"{prefix}_{idx}_tick")
            missing = pdf_field_value(fields, f"{prefix}_{idx}_missing")

            has_anything = any([req, loaded, returned, is_pdf_tick(tick), missing])
            if not has_anything:
                continue

            equipment_rows.append({
                "Category": category,
                "Equipment Item": item_name,
                "Qty Required Raw": req,
                "Qty Loaded Raw": loaded,
                "Qty Returned Raw": returned,
                "Qty Required": qty_to_float(req),
                "Qty Loaded": qty_to_float(loaded),
                "Qty Returned": qty_to_float(returned),
                "Ticked": "Yes" if is_pdf_tick(tick) else "",
                "Missing / Damaged": missing,
            })

    material_rows = []
    for idx in range(5):
        product = pdf_field_value(fields, f"paintreg_{idx}_product")
        colour = pdf_field_value(fields, f"paintreg_{idx}_colour")
        qty_req = pdf_field_value(fields, f"paintreg_{idx}_qty_req")
        qty_loaded = pdf_field_value(fields, f"paintreg_{idx}_qty_loaded")

        if any([product, colour, qty_req, qty_loaded]):
            material_rows.append({
                "Product": product,
                "Colour": colour,
                "Qty Required": qty_req,
                "Qty Loaded": qty_loaded,
            })

    return job_info, pd.DataFrame(equipment_rows), pd.DataFrame(material_rows)


def find_or_create_builder_client(cur, name):
    name = clean_pdf_value(name)
    if not name:
        return None
    cur.execute("SELECT id FROM builders_clients WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("""
        INSERT INTO builders_clients
        (type, name, contact_name, phone, email, address, qbcc, abn, terms, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ("Client / Builder", name, "", "", "", "", "", "", "", "Created from imported PDF checklist"))

    cur.execute("SELECT id FROM builders_clients WHERE name = ?", (name,))
    row = cur.fetchone()
    return row[0] if row else None


def find_or_create_checklist_item(cur, category, item_name):
    cur.execute("SELECT id FROM equipment_checklist_items WHERE item_name = ?", (item_name,))
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute("""
        INSERT INTO equipment_checklist_items
        (category, item_name, default_qty, notes)
        VALUES (?, ?, ?, ?)
    """, (category, item_name, 0, "Created from imported PDF checklist"))

    cur.execute("SELECT id FROM equipment_checklist_items WHERE item_name = ?", (item_name,))
    row = cur.fetchone()
    return row[0] if row else None


def import_master_checklist_to_job(job_id, job_info, equipment_df, materials_df, source_file, update_job=True, replace_imported_materials=True):
    conn = connect()
    cur = conn.cursor()

    imported_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if update_job:
        update_fields = []
        params = []

        if job_info.get("job_number"):
            update_fields.append("job_no = ?")
            params.append(job_info["job_number"])

        if job_info.get("job_name"):
            update_fields.append("job_name = ?")
            params.append(job_info["job_name"])

        if job_info.get("site_address"):
            update_fields.append("site_address = ?")
            params.append(job_info["site_address"])

        if job_info.get("leading_hand"):
            update_fields.append("leading_hand = ?")
            params.append(job_info["leading_hand"])

        if job_info.get("client_builder"):
            builder_id = find_or_create_builder_client(cur, job_info["client_builder"])
            if builder_id:
                update_fields.append("builder_client_id = ?")
                params.append(builder_id)

        crew_notes = []
        if job_info.get("crew_members"):
            crew_notes.append(f"Crew Members from checklist: {job_info['crew_members']}")
        if job_info.get("team_extra"):
            crew_notes.append(f"Team Notes from checklist: {job_info['team_extra']}")

        if crew_notes:
            cur.execute("SELECT notes FROM jobs WHERE id = ?", (job_id,))
            current_notes_row = cur.fetchone()
            current_notes = current_notes_row[0] if current_notes_row and current_notes_row[0] else ""
            new_notes = (current_notes + "\n" if current_notes else "") + "\n".join(crew_notes)
            update_fields.append("notes = ?")
            params.append(new_notes)

        if update_fields:
            params.append(job_id)
            cur.execute(f"UPDATE jobs SET {', '.join(update_fields)} WHERE id = ?", params)

    imported_equipment_count = 0

    for _, row in equipment_df.iterrows():
        category = str(row.get("Category", "")).strip()
        item_name = str(row.get("Equipment Item", "")).strip()
        if not item_name:
            continue

        item_id = find_or_create_checklist_item(cur, category, item_name)

        qty_required = float(row.get("Qty Required", 0) or 0)
        qty_loaded = float(row.get("Qty Loaded", 0) or 0)
        qty_returned = float(row.get("Qty Returned", 0) or 0)

        raw_req = str(row.get("Qty Required Raw", "") or "").strip()
        raw_loaded = str(row.get("Qty Loaded Raw", "") or "").strip()
        raw_returned = str(row.get("Qty Returned Raw", "") or "").strip()
        missing = str(row.get("Missing / Damaged", "") or "").strip()
        ticked = str(row.get("Ticked", "") or "").strip()

        notes_parts = []
        if raw_req and qty_required == 0:
            notes_parts.append(f"Original required qty: {raw_req}")
        if raw_loaded and qty_loaded == 0:
            notes_parts.append(f"Original loaded qty: {raw_loaded}")
        if raw_returned and qty_returned == 0:
            notes_parts.append(f"Original returned qty: {raw_returned}")
        if missing:
            notes_parts.append(f"Missing/damaged: {missing}")
        if ticked:
            notes_parts.append("Checklist ticked")
        notes_parts.append(f"Imported from {source_file} at {imported_at}")
        notes = " | ".join(notes_parts)

        is_required = 1 if (qty_required > 0 or raw_req) else 0
        is_packed = 1 if (qty_loaded > 0 or raw_loaded or ticked) else 0
        is_returned = 1 if (qty_returned > 0 or raw_returned) else 0

        cur.execute("""
            SELECT id FROM equipment_checklist_records
            WHERE job_id = ? AND checklist_item_id = ?
            ORDER BY id ASC
        """, (job_id, item_id))
        existing = cur.fetchall()

        if existing:
            keep_id = existing[0][0]
            cur.execute("""
                UPDATE equipment_checklist_records
                SET qty_required = ?, qty_taken = ?, qty_returned = ?,
                    is_required = ?, is_packed = ?, is_returned = ?,
                    date_out = ?, date_in = ?, taken_by = ?, returned_by = ?,
                    condition_out = ?, condition_in = ?, notes = ?
                WHERE id = ?
            """, (
                qty_required, qty_loaded, qty_returned,
                is_required, is_packed, is_returned,
                imported_at.split(" ")[0], "", "", "",
                "", missing, notes, keep_id
            ))

            for duplicate in existing[1:]:
                cur.execute("DELETE FROM equipment_checklist_records WHERE id = ?", (duplicate[0],))
        else:
            cur.execute("""
                INSERT INTO equipment_checklist_records
                (job_id, checklist_item_id, qty_required, qty_taken, qty_returned,
                 is_required, is_packed, is_returned, date_out, date_in, taken_by, returned_by,
                 condition_out, condition_in, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job_id, item_id, qty_required, qty_loaded, qty_returned,
                is_required, is_packed, is_returned,
                imported_at.split(" ")[0], "", "", "",
                "", missing, notes
            ))

        imported_equipment_count += 1

    imported_material_count = 0

    if replace_imported_materials:
        cur.execute("DELETE FROM imported_material_entries WHERE job_id = ?", (job_id,))

    for _, row in materials_df.iterrows():
        product = str(row.get("Product", "") or "").strip()
        colour = str(row.get("Colour", "") or "").strip()
        qty_required = str(row.get("Qty Required", "") or "").strip()
        qty_loaded = str(row.get("Qty Loaded", "") or "").strip()

        if not any([product, colour, qty_required, qty_loaded]):
            continue

        cur.execute("""
            INSERT INTO imported_material_entries
            (job_id, product, colour, qty_required, qty_loaded, source_file, imported_at, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (job_id, product, colour, qty_required, qty_loaded, source_file, imported_at, "Imported from PDF master checklist"))

        imported_material_count += 1

    conn.commit()
    conn.close()

    return imported_equipment_count, imported_material_count



def linked_job_counts(job_id):
    counts = {}

    for table in [
        "material_entries",
        "wage_entries",
        "timesheet_entries",
        "equipment_entries",
        "equipment_checklist_records",
        "imported_material_entries",
        "job_photos",
    ]:
        try:
            df = df_query(f"SELECT COUNT(*) AS c FROM {table} WHERE job_id = ?", (job_id,))
            counts[table] = int(df.iloc[0]["c"])
        except Exception:
            counts[table] = 0

    return counts


def permanently_delete_job_and_linked_data(job_id):
    conn = connect()
    cur = conn.cursor()

    for table in [
        "material_entries",
        "wage_entries",
        "equipment_entries",
        "equipment_checklist_records",
        "imported_material_entries",
        "job_photos",
    ]:
        try:
            cur.execute(f"DELETE FROM {table} WHERE job_id = ?", (job_id,))
        except Exception:
            pass

    cur.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    try:
        cur.execute("""
            INSERT OR REPLACE INTO app_settings (setting_key, setting_value)
            VALUES (?, ?)
        """, ("starter_data_seeded", "yes"))
    except Exception:
        pass

    conn.commit()
    conn.close()



# =============================
# LOGIN / ACCESS CONTROL
# =============================
def hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def check_password(password, password_hash):
    return hash_password(password) == password_hash


def username_from_employee_name(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def seed_app_users():
    conn = connect()
    cur = conn.cursor()

    def user_exists(username=None, employee_id=None):
        if username and employee_id:
            cur.execute("""
                SELECT id FROM app_users
                WHERE LOWER(TRIM(username)) = LOWER(TRIM(?)) OR employee_id = ?
                LIMIT 1
            """, (username, employee_id))
        elif username:
            cur.execute("""
                SELECT id FROM app_users
                WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))
                LIMIT 1
            """, (username,))
        elif employee_id:
            cur.execute("""
                SELECT id FROM app_users
                WHERE employee_id = ?
                LIMIT 1
            """, (employee_id,))
        else:
            return True
        return cur.fetchone() is not None

    # Default admin account
    if not user_exists(username="admin"):
        cur.execute("""
            INSERT INTO app_users
            (username, password_hash, role, employee_id, active, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("admin", hash_password("admin123"), "admin", None, 1, "Default admin account - change password immediately"))

    # Default manager account
    if not user_exists(username="manager"):
        cur.execute("""
            INSERT INTO app_users
            (username, password_hash, role, employee_id, active, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("manager", hash_password("manager123"), "manager", None, 1, "Default manager account - change password immediately"))

    # Create basic employee logins for active employees if missing.
    # Username example: "bryce", "robpullin"
    # Default password: changeme123
    cur.execute("SELECT id, name FROM employees WHERE status = 'Active'")
    for employee_id, employee_name in cur.fetchall():
        username = username_from_employee_name(employee_name)
        if not username:
            continue

        # Do not create another account if either the username OR employee link already exists.
        if user_exists(username=username, employee_id=employee_id):
            continue

        cur.execute("""
            INSERT INTO app_users
            (username, password_hash, role, employee_id, active, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (username, hash_password("changeme123"), "employee", employee_id, 1, "Auto-created employee account"))

    conn.commit()
    conn.close()


def get_current_user():
    return st.session_state.get("user")


def current_role():
    user = get_current_user()
    if not user:
        return ""
    return user.get("role", "")


def is_admin():
    return current_role() == "admin"


def is_manager_or_admin():
    return current_role() in ["admin", "manager"]


def require_login():
    seed_app_users()

    if "user" not in st.session_state:
        st.session_state["user"] = None

    if st.session_state["user"]:
        return True

    st.title("Premier Brushworks JobHub")
    st.subheader("Login")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")

        if submitted:
            user_df = df_query("""
                SELECT u.id, u.username, u.password_hash, u.role, u.employee_id, u.active,
                       e.name AS employee_name
                FROM app_users u
                LEFT JOIN employees e ON e.id = u.employee_id
                WHERE u.username = ?
            """, (username.strip(),))

            if user_df.empty:
                st.error("Invalid username or password.")
            else:
                row = user_df.iloc[0]
                if int(row["active"] or 0) != 1:
                    st.error("This user account is inactive.")
                elif not check_password(password, row["password_hash"]):
                    st.error("Invalid username or password.")
                else:
                    st.session_state["user"] = {
                        "id": int(row["id"]),
                        "username": str(row["username"]),
                        "role": str(row["role"]),
                        "employee_id": int(row["employee_id"]) if not pd.isna(row["employee_id"]) else None,
                        "employee_name": "" if pd.isna(row["employee_name"]) else str(row["employee_name"]),
                    }
                    st.success("Logged in.")
                    st.rerun()

    st.info("Default admin login: admin / admin123. Change this immediately in User Access.")
    st.stop()


def logout_button():
    user = get_current_user()
    if user:
        st.sidebar.write(f"Logged in as **{user['username']}**")
        st.sidebar.caption(f"Role: {user['role']}")
        if st.sidebar.button("Logout"):
            st.session_state["user"] = None
            st.rerun()


def employee_portal():
    user = get_current_user()
    employee_id = user.get("employee_id")
    employee_name = user.get("employee_name") or user.get("username")

    st.header("Employee Portal")
    st.caption("Restricted staff access for job details, equipment and your own hours.")

    if not employee_id:
        st.warning("This login is not linked to an employee record. Ask admin to link it in User Access.")
        return

    tab_jobs, tab_hours, tab_equipment, tab_photos, tab_password = st.tabs([
        "My Job Info",
        "Submit Timesheet",
        "View Equipment",
        "Upload Photos",
        "Change Password",
    ])

    job_options = get_job_options()

    with tab_jobs:
        st.subheader("Job Information")
        if not job_options:
            st.info("No jobs available.")
        else:
            selected_job = st.selectbox("Select Job", list(job_options.keys()), key="employee_job_info")
            selected_job_id = job_options[selected_job]

            job_df = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       bc.name AS 'Builder / Client',
                       bc.contact_name AS 'Contact',
                       bc.phone AS 'Phone',
                       bc.email AS 'Email',
                       j.site_address AS 'Site Address',
                       j.status AS 'Status',
                       j.leading_hand AS 'Leading Hand',
                       j.start_date AS 'Start Date',
                       j.end_date AS 'End Date',
                       j.notes AS 'Notes'
                FROM jobs j
                LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
                WHERE j.id = ?
            """, (selected_job_id,))
            st.dataframe(job_df, width="stretch", hide_index=True)

    with tab_hours:
        timesheets_page(employee_restricted=True)
        st.info("Timesheets are now linked directly to specific jobs.")

        st.subheader("Submit Timesheet")
        if not job_options:
            st.info("No jobs available.")
        else:
            with st.form("employee_wage_submit"):
                selected_job = st.selectbox("Job", list(job_options.keys()), key="employee_hours_job")
                work_date = st.text_input("Date", value=str(date.today()))
                hours = st.number_input("Hours", min_value=0.0, step=0.5)
                notes = st.text_area("Notes")
                submitted = st.form_submit_button("Save My Hours")

                if submitted:
                    execute("""
                        INSERT INTO wage_entries
                        (job_id, employee_id, work_date, hours, notes)
                        VALUES (?, ?, ?, ?, ?)
                    """, (job_options[selected_job], employee_id, work_date, hours, notes))
                    st.success("Hours saved.")

            st.markdown("### My Recent Hours")
            my_hours = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       w.work_date AS 'Date',
                       w.hours AS 'Hours',
                       e.rate_plus_10 AS 'Rate + 10%',
                       ROUND(w.hours * e.rate_plus_10, 2) AS 'Total Cost',
                       w.notes AS 'Notes'
                FROM wage_entries w
                JOIN jobs j ON j.id = w.job_id
                JOIN employees e ON e.id = w.employee_id
                WHERE w.employee_id = ?
                ORDER BY w.id DESC
                LIMIT 50
            """, (employee_id,))
            st.dataframe(my_hours, width="stretch", hide_index=True)

    with tab_equipment:
        st.subheader("View Job Equipment Master List")
        if not job_options:
            st.info("No jobs available.")
        else:
            selected_job = st.selectbox("Select Job", list(job_options.keys()), key="employee_equipment_job")
            selected_job_id = job_options[selected_job]

            equipment_df = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       i.category AS 'Category',
                       i.item_name AS 'Equipment Item',
                       COALESCE(SUM(r.qty_required), 0) AS 'Total Required',
                       COALESCE(SUM(r.qty_taken), 0) AS 'Total Taken',
                       COALESCE(SUM(r.qty_returned), 0) AS 'Total Returned',
                       COALESCE(SUM(r.qty_taken - r.qty_returned), 0) AS 'Still Out'
                FROM equipment_checklist_items i
                CROSS JOIN jobs j
                LEFT JOIN equipment_checklist_records r
                    ON r.checklist_item_id = i.id
                   AND r.job_id = j.id
                WHERE j.id = ?
                GROUP BY j.job_no, j.job_name, i.category, i.item_name
                ORDER BY i.category, i.item_name
            """, (selected_job_id,))
            st.dataframe(equipment_df, width="stretch", hide_index=True)

    with tab_photos:
        job_photos_page(employee_restricted=True)

    with tab_password:
        st.subheader("Change My Password")
        with st.form("employee_change_password"):
            old_password = st.text_input("Current Password", type="password")
            new_password = st.text_input("New Password", type="password")
            confirm_password = st.text_input("Confirm New Password", type="password")
            submitted = st.form_submit_button("Change Password")

            if submitted:
                user_df = df_query("SELECT password_hash FROM app_users WHERE id = ?", (user["id"],))
                if user_df.empty:
                    st.error("User account not found.")
                elif not check_password(old_password, user_df.iloc[0]["password_hash"]):
                    st.error("Current password is incorrect.")
                elif len(new_password) < 6:
                    st.error("Password must be at least 6 characters.")
                elif new_password != confirm_password:
                    st.error("New passwords do not match.")
                else:
                    execute("UPDATE app_users SET password_hash = ? WHERE id = ?", (hash_password(new_password), user["id"]))
                    st.success("Password changed.")


def user_access_page():
    st.header("User Access")
    st.caption("Admin only. Create logins and control who can access the app.")

    if not is_admin():
        st.error("Only admin users can access this page.")
        return

    st.markdown("### Restore / Update Haymes & Taubmans Product Lists")
    st.caption("One button to restore/update both saved paint product lists. Existing matching product codes are updated instead of duplicated.")

    pc1, pc2, pc3 = st.columns(3)
    pc1.metric("Haymes products", haymes_product_count())
    pc2.metric("Taubmans products", taubmans_product_count())
    pc3.metric("Combined saved paint products", combined_paint_product_count())

    paint_confirm = st.text_input(
        "To restore/update Haymes and Taubmans products, type: RESTORE PAINT LISTS",
        key="restore_combined_paint_lists_confirm"
    )

    if st.button("Restore / Update Haymes & Taubmans Product Lists", key="restore_haymes_taubmans_products_btn"):
        if paint_confirm.strip().upper() != "RESTORE PAINT LISTS":
            st.error("Type RESTORE PAINT LISTS exactly before restoring.")
        else:
            restored = restore_haymes_and_taubmans_product_lists()
            st.success(f"Restored/updated {restored} Haymes and Taubmans products.")
            refresh()


    st.divider()

    st.markdown("### Restore Master Builders/Clients & Employees")
    st.caption("Use this if builders, clients, employee names, or employee logins are missing.")

    rc1, rc2 = st.columns(2)
    rc1.metric("Builders/clients currently in database", builders_clients_count())
    rc2.metric("Employees currently in database", employees_count())

    restore_master_confirm = st.text_input(
        "To restore the saved master builders/clients and employees, type: RESTORE MASTER DATA",
        key="restore_master_data_confirm"
    )

    if st.button("Restore Builders/Clients & Employees", key="restore_builders_clients_employees_btn"):
        if restore_master_confirm.strip().upper() != "RESTORE MASTER DATA":
            st.error("Type RESTORE MASTER DATA exactly before restoring.")
        else:
            restored_builders, restored_employees = restore_builders_clients_and_employees()
            st.success(
                f"Restored/updated {restored_builders} builders/clients and {restored_employees} employees. "
                "Missing employee login accounts were recreated where needed."
            )
            refresh()

    st.divider()

    st.markdown("### Clean Up Duplicate User Accounts")
    st.caption("Use this if the same employee/user login appears more than once.")

    duplicates_df = user_duplicate_summary()

    if duplicates_df.empty:
        st.success("No duplicate user accounts detected.")
    else:
        st.warning(f"Found {len(duplicates_df)} duplicate/suspect user account rows.")
        st.dataframe(
            duplicates_df[["id", "username", "role", "employee_name", "active", "notes"]],
            width="stretch",
            hide_index=True,
        )

        clean_confirm = st.text_input(
            "To clean duplicate user accounts, type: CLEAN USERS",
            key="clean_duplicate_users_confirm"
        )

        if st.button("Clean Duplicate User Accounts", key="clean_duplicate_users_button"):
            if clean_confirm.strip().upper() != "CLEAN USERS":
                st.error("Type CLEAN USERS exactly before cleaning duplicate accounts.")
            else:
                result = clean_duplicate_user_accounts()
                st.success(
                    f"Duplicate cleanup complete. Deleted {result['deleted']} duplicate login(s). "
                    f"Skipped/disabled {result['skipped']}."
                )
                refresh()

    st.divider()

    tab_add, tab_edit, tab_list = st.tabs(["Add User", "Edit / Disable / Delete User", "User List"])

    employee_options = get_employee_options(active_only=False)
    employee_labels = ["Not linked"] + list(employee_options.keys())

    with tab_add:
        st.subheader("Add User")
        with st.form("add_user_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            role = st.selectbox("Role", ["employee", "manager", "admin"])
            employee_label = st.selectbox("Link to Employee", employee_labels)
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Create User")

            if submitted:
                if not username or not password:
                    st.error("Username and password are required.")
                elif len(password) < 6:
                    st.error("Password must be at least 6 characters.")
                else:
                    employee_id = employee_options.get(employee_label) if employee_label != "Not linked" else None
                    try:
                        execute("""
                            INSERT INTO app_users
                            (username, password_hash, role, employee_id, active, notes)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (username.strip(), hash_password(password), role, employee_id, 1, notes))
                        st.success(f"Created user {username}.")
                        refresh()
                    except Exception as e:
                        st.error(f"Could not create user: {e}")

    with tab_edit:
        st.subheader("Edit / Disable User")
        users_df = df_query("""
            SELECT u.id, u.username, u.role, u.employee_id, u.active, u.notes,
                   COALESCE(e.name, '') AS employee_name
            FROM app_users u
            LEFT JOIN employees e ON e.id = u.employee_id
            ORDER BY u.username
        """)

        if users_df.empty:
            st.info("No users.")
        else:
            user_map = {row["username"]: int(row["id"]) for _, row in users_df.iterrows()}
            selected_username = st.selectbox("Select User", list(user_map.keys()))
            selected_user_id = user_map[selected_username]
            current = users_df[users_df["id"] == selected_user_id].iloc[0]

            current_employee = str(current["employee_name"] or "Not linked")
            employee_index = employee_labels.index(current_employee) if current_employee in employee_labels else 0
            roles = ["employee", "manager", "admin"]
            role_index = roles.index(str(current["role"])) if str(current["role"]) in roles else 0
            active_options = ["Active", "Inactive"]
            active_index = 0 if int(current["active"] or 0) == 1 else 1

            with st.form("edit_user_form"):
                username = st.text_input("Username", value=str(current["username"]))
                new_password = st.text_input("New Password (leave blank to keep current)", type="password")
                role = st.selectbox("Role", roles, index=role_index)
                employee_label = st.selectbox("Link to Employee", employee_labels, index=employee_index)
                active_label = st.selectbox("Status", active_options, index=active_index)
                notes = st.text_area("Notes", value=str(current["notes"] or ""))
                submitted = st.form_submit_button("Update User")

                if submitted:
                    employee_id = employee_options.get(employee_label) if employee_label != "Not linked" else None
                    active = 1 if active_label == "Active" else 0

                    if new_password and len(new_password) < 6:
                        st.error("Password must be at least 6 characters.")
                    else:
                        success, message = safe_update_user_account(
                            selected_user_id=selected_user_id,
                            username=username,
                            role=role,
                            employee_id=employee_id,
                            active=active,
                            notes=notes,
                        )

                        if success:
                            if new_password:
                                execute("UPDATE app_users SET password_hash = ? WHERE id = ?", (hash_password(new_password), selected_user_id))
                            st.success(message)
                            refresh()
                        else:
                            st.error(message)

            st.markdown("### Delete User Account")
            st.warning(
                "This deletes the selected login account and will also delete the linked employee record where safe. "
                "If the employee has wages, timesheets or job history, they will be marked Inactive instead."
            )

            admin_count_df = df_query("""
                SELECT COUNT(*) AS 'count'
                FROM app_users
                WHERE role = 'admin' AND active = 1
            """)
            active_admin_count = int(admin_count_df.iloc[0]["count"]) if not admin_count_df.empty else 0

            current_user = get_current_user() or {}
            selected_is_current_user = int(current_user.get("id", -1)) == int(selected_user_id)
            selected_is_last_active_admin = (
                str(current["role"]) == "admin"
                and int(current["active"] or 0) == 1
                and active_admin_count <= 1
            )

            delete_confirm = st.text_input(
                "To delete this user login, type: DELETE USER",
                key=f"delete_user_confirm_{selected_user_id}"
            )

            if st.button("Delete Selected User Account", key=f"delete_user_button_{selected_user_id}"):
                if delete_confirm.strip().upper() != "DELETE USER":
                    st.error("Type DELETE USER exactly before deleting this account.")
                elif selected_is_current_user:
                    st.error("You cannot delete the account you are currently logged in with.")
                elif selected_is_last_active_admin:
                    st.error("You cannot delete the last active admin account. Create another admin first, then delete this one.")
                else:
                    result = delete_user_and_linked_employee(selected_user_id)

                    if result["deleted_users"]:
                        st.success(f"Deleted {result['deleted_users']} user login account(s).")

                    if result["deleted_employee"]:
                        st.success(f"Deleted {result['deleted_employee']} linked employee record(s).")

                    if result["deactivated_employee"]:
                        st.info(f"Marked {result['deactivated_employee']} linked employee(s) as Inactive because they had job history or other linked records.")

                    if result["skipped"]:
                        st.warning(f"Skipped {result['skipped']} item(s).")

                    with st.expander("Delete details"):
                        for msg in result["messages"]:
                            st.write(msg)

                    refresh()

            st.markdown("### Unlink Employee From This User")
            st.caption("Use this if this login is incorrectly linked to the wrong employee.")
            if st.button("Unlink Employee From Selected User", key=f"unlink_employee_user_{selected_user_id}"):
                execute("UPDATE app_users SET employee_id = NULL WHERE id = ?", (selected_user_id,))
                st.success("Employee link removed from this user account.")
                refresh()

    st.markdown("### Start Fresh / Clear All Jobs")
    st.warning(
        "This permanently deletes all jobs and all job-linked data, including materials, wages, "
        "equipment checklist records and imported checklist materials. Builders, employees, products, "
        "users and checklist item templates will stay."
    )
    clear_confirm = st.text_input("To clear all jobs, type: CLEAR JOBS", key="clear_jobs_confirm")
    if st.button("Clear All Jobs and Start at 0"):
        if clear_confirm.strip().upper() != "CLEAR JOBS":
            st.error("Type CLEAR JOBS exactly before clearing the job register.")
        else:
            clear_all_jobs_and_linked_data()
            st.success("All jobs and job-linked data have been cleared. Job Register is now at 0.")
            refresh()


    with tab_list:
        st.subheader("User List")

        users_df = df_query("""
            SELECT u.id AS 'ID',
                   u.username AS 'Username',
                   u.role AS 'Role',
                   COALESCE(e.name, '') AS 'Linked Employee',
                   CASE WHEN u.active = 1 THEN 'Active' ELSE 'Inactive' END AS 'Status',
                   u.notes AS 'Notes'
            FROM app_users u
            LEFT JOIN employees e ON e.id = u.employee_id
            ORDER BY u.role, u.username, u.id
        """)

        if users_df.empty:
            st.info("No user accounts found.")
        else:
            st.dataframe(users_df, width="stretch", hide_index=True)

            st.markdown("### Remove Multiple User Accounts")
            st.warning(
                "This deletes selected user login accounts. If a selected login is linked to an employee, "
                "the linked employee will also be deleted where safe. If that employee has wages/timesheets, "
                "they will be marked Inactive instead to protect history."
            )

            delete_options = {
                f"{row['Username']} | {row['Role']} | {row['Linked Employee'] or 'No Employee'} | {row['Status']} | ID {row['ID']}": int(row["ID"])
                for _, row in users_df.iterrows()
            }

            selected_delete_labels = st.multiselect(
                "Select user login accounts to delete",
                list(delete_options.keys()),
                key="bulk_user_delete_multiselect"
            )

            selected_delete_ids = [delete_options[label] for label in selected_delete_labels]

            if selected_delete_ids:
                selected_preview = users_df[users_df["ID"].astype(int).isin(selected_delete_ids)]
                st.markdown("Selected accounts:")
                st.dataframe(selected_preview, width="stretch", hide_index=True)

            bulk_confirm = st.text_input(
                "To delete the selected user login accounts, type: DELETE SELECTED USERS",
                key="bulk_user_delete_confirm"
            )

            if st.button("Delete Selected User Accounts", key="bulk_user_delete_button"):
                if not selected_delete_ids:
                    st.error("Select at least one user account first.")
                elif bulk_confirm.strip().upper() != "DELETE SELECTED USERS":
                    st.error("Type DELETE SELECTED USERS exactly before deleting multiple accounts.")
                else:
                    result = delete_selected_user_accounts(selected_delete_ids)

                    if result["deleted_users"]:
                        st.success(f"Deleted {result['deleted_users']} selected user login account(s).")

                    if result["deleted_employee"]:
                        st.success(f"Deleted {result['deleted_employee']} linked employee record(s).")

                    if result["deactivated_employee"]:
                        st.info(f"Marked {result['deactivated_employee']} linked employee(s) as Inactive because they had job history or other linked records.")

                    if result["skipped"]:
                        st.warning(f"Skipped {result['skipped']} item(s).")

                    with st.expander("Deletion details"):
                        for msg in result["messages"]:
                            st.write(msg)

                    refresh()



def mark_seeded_if_existing_data_present():
    try:
        if starter_data_already_seeded():
            return

        conn = connect()
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM jobs")
        job_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM builders_clients")
        builder_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM employees")
        employee_count = cur.fetchone()[0]

        # If this database already has data, assume starter data has already been seeded.
        # This stops old/deleted jobs reappearing on first run after this update.
        if job_count > 0 or builder_count > 0 or employee_count > 0:
            cur.execute("""
                INSERT OR REPLACE INTO app_settings (setting_key, setting_value)
                VALUES (?, ?)
            """, ("starter_data_seeded", "yes"))
            conn.commit()

        conn.close()
    except Exception:
        pass



def clear_all_jobs_and_linked_data():
    conn = connect()
    cur = conn.cursor()

    # Delete all job-linked records first
    for table in [
        "material_entries",
        "wage_entries",
        "equipment_entries",
        "equipment_checklist_records",
        "imported_material_entries",
        "job_photos",
    ]:
        try:
            cur.execute(f"DELETE FROM {table}")
        except Exception:
            pass

    # Delete all jobs
    cur.execute("DELETE FROM jobs")

    # Make sure starter/demo jobs do not reseed after clearing jobs
    try:
        cur.execute("""
            INSERT OR REPLACE INTO app_settings (setting_key, setting_value)
            VALUES (?, ?)
        """, ("starter_data_seeded", "yes"))
    except Exception:
        pass

    conn.commit()
    conn.close()



# =============================
# JOB PHOTO HELPERS
# =============================
def resize_photo_for_database(uploaded_file, max_size=(1400, 1400), quality=75):
    """
    Converts uploaded image to compressed JPEG base64 for storage in the database.
    This keeps phone uploads smaller for Supabase/Streamlit Cloud.
    """
    image = Image.open(uploaded_file)

    # Convert HEIC is not supported by Pillow by default; JPG/PNG/WebP are best.
    if image.mode not in ["RGB", "L"]:
        image = image.convert("RGB")
    elif image.mode == "L":
        image = image.convert("RGB")

    image.thumbnail(max_size)

    output = BytesIO()
    image.save(output, format="JPEG", quality=quality, optimize=True)
    data = output.getvalue()

    encoded = base64.b64encode(data).decode("utf-8")
    return encoded, "image/jpeg"


def photo_data_to_bytes(photo_data):
    if not photo_data:
        return b""
    return base64.b64decode(photo_data.encode("utf-8"))


def save_job_photo(job_id, uploaded_file, category, caption, notes):
    uploaded_by = ""
    try:
        user = get_current_user()
        if user:
            uploaded_by = user.get("username", "")
    except Exception:
        uploaded_by = ""

    photo_data, photo_type = resize_photo_for_database(uploaded_file)

    execute("""
        INSERT INTO job_photos
        (job_id, photo_name, photo_type, photo_data, category, caption, uploaded_by, uploaded_at, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job_id,
        uploaded_file.name,
        photo_type,
        photo_data,
        category,
        caption,
        uploaded_by,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        notes,
    ))


def delete_job_photo(photo_id):
    execute("DELETE FROM job_photos WHERE id = ?", (photo_id,))


def job_photos_page(employee_restricted=False):
    st.header("Job Photos")
    st.caption("Upload photos against a specific job. Photos will appear in Job Pack reports.")

    job_options = get_job_options()

    if not job_options:
        st.info("Create a job first, then upload photos.")
        return

    tab_upload, tab_view = st.tabs(["Upload Photos", "View / Delete Photos"])

    with tab_upload:
        st.subheader("Upload Job Photos")

        with st.form("upload_job_photos_form"):
            selected_job = st.selectbox("Select Job", list(job_options.keys()), key="photo_upload_job")
            category = st.selectbox(
                "Photo Category",
                [
                    "Before",
                    "During Works",
                    "After",
                    "Defect / Damage",
                    "Access / Safety",
                    "Materials",
                    "Equipment",
                    "Completion / Sign-off",
                    "Other",
                ],
            )
            caption = st.text_input("Caption / Description")
            notes = st.text_area("Notes")
            uploaded_files = st.file_uploader(
                "Upload photos",
                type=["jpg", "jpeg", "png", "webp"],
                accept_multiple_files=True,
            )
            submitted = st.form_submit_button("Save Photos to Job")

            if submitted:
                if not uploaded_files:
                    st.error("Please select at least one photo.")
                else:
                    saved_count = 0
                    for uploaded_file in uploaded_files:
                        try:
                            save_job_photo(
                                job_id=job_options[selected_job],
                                uploaded_file=uploaded_file,
                                category=category,
                                caption=caption,
                                notes=notes,
                            )
                            saved_count += 1
                        except Exception as e:
                            st.error(f"Could not save {uploaded_file.name}: {e}")

                    if saved_count:
                        st.success(f"Saved {saved_count} photo(s) to {selected_job}.")
                        refresh()

    with tab_view:
        st.subheader("View Job Photos")

        selected_job = st.selectbox("Select Job", list(job_options.keys()), key="photo_view_job")
        selected_job_id = job_options[selected_job]

        photos_df = df_query("""
            SELECT id, photo_name, photo_type, photo_data, category, caption, uploaded_by, uploaded_at, notes
            FROM job_photos
            WHERE job_id = ?
            ORDER BY uploaded_at DESC, id DESC
        """, (selected_job_id,))

        if photos_df.empty:
            st.info("No photos saved for this job.")
        else:
            for _, row in photos_df.iterrows():
                photo_id = int(row["id"])
                caption = str(row["caption"] or "")
                category = str(row["category"] or "")
                uploaded_at = str(row["uploaded_at"] or "")
                uploaded_by = str(row["uploaded_by"] or "")
                notes = str(row["notes"] or "")

                st.markdown(f"### {category} - {caption if caption else row['photo_name']}")
                try:
                    st.image(photo_data_to_bytes(row["photo_data"]), width="stretch")
                except Exception:
                    st.warning("Could not display this photo.")

                st.caption(f"Uploaded: {uploaded_at} by {uploaded_by}")
                if notes:
                    st.write(notes)

                if not employee_restricted:
                    delete_confirm = st.checkbox(f"Delete this photo", key=f"delete_photo_confirm_{photo_id}")
                    if st.button("Delete Photo", key=f"delete_photo_{photo_id}"):
                        if not delete_confirm:
                            st.error("Tick the delete checkbox first.")
                        else:
                            delete_job_photo(photo_id)
                            st.success("Photo deleted.")
                            refresh()

                st.divider()



# =============================
# TIMESHEETS
# =============================
def calculate_hours_from_times(start_time, finish_time, break_minutes):
    try:
        if not start_time or not finish_time:
            return 0.0
        sh, sm = [int(x) for x in str(start_time).split(":")[:2]]
        fh, fm = [int(x) for x in str(finish_time).split(":")[:2]]
        start_minutes = sh * 60 + sm
        finish_minutes = fh * 60 + fm
        if finish_minutes < start_minutes:
            finish_minutes += 24 * 60
        total_minutes = finish_minutes - start_minutes - float(break_minutes or 0)
        return max(round(total_minutes / 60, 2), 0.0)
    except Exception:
        return 0.0


def save_timesheet_entry(job_id, employee_id, work_date, start_time, finish_time, break_minutes, total_hours, work_type, notes):
    user = get_current_user() or {}
    submitted_by = user.get("username", "")
    submitted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    execute("""
        INSERT INTO timesheet_entries
        (job_id, employee_id, work_date, start_time, finish_time, break_minutes, total_hours,
         work_type, submitted_by, submitted_at, status, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (job_id, employee_id, work_date, start_time, finish_time, break_minutes, total_hours,
          work_type, submitted_by, submitted_at, "Submitted", notes))

    execute("""
        INSERT INTO wage_entries (job_id, employee_id, work_date, hours, notes)
        VALUES (?, ?, ?, ?, ?)
    """, (job_id, employee_id, work_date, total_hours,
          f"Timesheet: {start_time}-{finish_time}, break {break_minutes} min. {notes}"))


def timesheet_entry_form(employee_id=None, employee_restricted=False, key_prefix="timesheet"):
    job_options = get_job_options()
    if not job_options:
        st.info("Create a job first, then timesheets can be submitted.")
        return

    if employee_id is None:
        employee_options = get_employee_options(active_only=True)
        if not employee_options:
            st.info("Create employees first.")
            return
    else:
        employee_options = None

    with st.form(f"{key_prefix}_form"):
        selected_job = st.selectbox("Job", list(job_options.keys()), key=f"{key_prefix}_job")

        if employee_restricted and employee_id is not None:
            employee_df = df_query("SELECT name FROM employees WHERE id = ?", (employee_id,))
            employee_name = employee_df.iloc[0]["name"] if not employee_df.empty else "Current Employee"
            st.text_input("Employee", value=str(employee_name), disabled=True, key=f"{key_prefix}_employee_name")
            selected_employee_id = employee_id
        else:
            selected_employee = st.selectbox("Employee", list(employee_options.keys()), key=f"{key_prefix}_employee")
            selected_employee_id = employee_options[selected_employee]

        col1, col2, col3, col4 = st.columns(4)
        work_date = col1.text_input("Date", value=str(date.today()), key=f"{key_prefix}_date")
        start_time = col2.text_input("Start Time", value="07:00", key=f"{key_prefix}_start")
        finish_time = col3.text_input("Finish Time", value="15:30", key=f"{key_prefix}_finish")
        break_minutes = col4.number_input("Break Minutes", min_value=0.0, step=15.0, value=30.0, key=f"{key_prefix}_break")

        calculated_hours = calculate_hours_from_times(start_time, finish_time, break_minutes)
        total_hours = st.number_input("Total Hours", min_value=0.0, step=0.25, value=float(calculated_hours), key=f"{key_prefix}_hours")
        work_type = st.selectbox("Work Type", ["Painting", "Prep", "Spraying", "Touch-ups", "Travel", "Site Setup", "Other"], key=f"{key_prefix}_work_type")
        notes = st.text_area("Notes", key=f"{key_prefix}_notes")
        submitted = st.form_submit_button("Submit Timesheet")

        if submitted:
            if total_hours <= 0:
                st.error("Total hours must be greater than 0.")
            else:
                save_timesheet_entry(job_options[selected_job], selected_employee_id, work_date, start_time, finish_time, break_minutes, total_hours, work_type, notes)
                st.success("Timesheet submitted and linked to the selected job.")
                refresh()


def timesheets_page(employee_restricted=False):
    st.header("Timesheets")
    st.caption("Employee hours linked directly to specific jobs.")
    user = get_current_user() or {}
    current_employee_id = user.get("employee_id")

    if employee_restricted:
        if not current_employee_id:
            st.warning("Your login is not linked to an employee record. Ask admin to link your user to your employee profile.")
            return
        tab_submit, tab_my = st.tabs(["Submit Timesheet", "My Timesheets"])
        with tab_submit:
            timesheet_entry_form(employee_id=current_employee_id, employee_restricted=True, key_prefix="employee_timesheet")
        with tab_my:
            my_df = df_query("""
                SELECT t.work_date AS 'Date', j.job_no AS 'Job No', j.job_name AS 'Job Name',
                       t.start_time AS 'Start', t.finish_time AS 'Finish', t.break_minutes AS 'Break Minutes',
                       t.total_hours AS 'Hours', t.work_type AS 'Work Type', t.status AS 'Status', t.notes AS 'Notes'
                FROM timesheet_entries t
                JOIN jobs j ON j.id = t.job_id
                WHERE t.employee_id = ?
                ORDER BY t.work_date DESC, t.id DESC
                LIMIT 100
            """, (current_employee_id,))
            st.dataframe(my_df, width="stretch", hide_index=True)
        return

    tab_submit, tab_review, tab_by_job = st.tabs(["Add Timesheet", "Review Timesheets", "Timesheets by Job"])
    with tab_submit:
        timesheet_entry_form(key_prefix="admin_timesheet")
    with tab_review:
        df = df_query("""
            SELECT t.id, t.work_date AS 'Date', j.job_no AS 'Job No', j.job_name AS 'Job Name', e.name AS 'Employee',
                   t.start_time AS 'Start', t.finish_time AS 'Finish', t.break_minutes AS 'Break Minutes',
                   t.total_hours AS 'Hours', t.work_type AS 'Work Type', t.status AS 'Status',
                   t.submitted_by AS 'Submitted By', t.submitted_at AS 'Submitted At', t.notes AS 'Notes'
            FROM timesheet_entries t
            JOIN jobs j ON j.id = t.job_id
            JOIN employees e ON e.id = t.employee_id
            ORDER BY t.work_date DESC, t.id DESC
            LIMIT 500
        """)
        if df.empty:
            st.info("No timesheets submitted yet.")
        else:
            st.dataframe(df.drop(columns=["id"]), width="stretch", hide_index=True)
            options = {f"{r['Date']} - {r['Employee']} - {r['Job No']} - {r['Hours']} hrs": int(r["id"]) for _, r in df.iterrows()}
            selected = st.selectbox("Select timesheet to approve/delete", list(options.keys()))
            selected_id = options[selected]
            col1, col2, col3 = st.columns(3)
            if col1.button("Mark Approved"):
                execute("UPDATE timesheet_entries SET status = 'Approved' WHERE id = ?", (selected_id,))
                st.success("Timesheet approved.")
                refresh()
            if col2.button("Mark Paid"):
                execute("UPDATE timesheet_entries SET status = 'Paid' WHERE id = ?", (selected_id,))
                st.success("Timesheet marked as paid.")
                refresh()
            if col3.button("Delete Timesheet"):
                execute("DELETE FROM timesheet_entries WHERE id = ?", (selected_id,))
                st.success("Timesheet deleted.")
                refresh()
    with tab_by_job:
        job_options = get_job_options()
        if not job_options:
            st.info("No jobs found.")
        else:
            selected_job = st.selectbox("Select Job", list(job_options.keys()), key="timesheet_by_job_select")
            selected_job_id = job_options[selected_job]
            by_job = df_query("""
                SELECT t.work_date AS 'Date', e.name AS 'Employee', t.start_time AS 'Start', t.finish_time AS 'Finish',
                       t.break_minutes AS 'Break Minutes', t.total_hours AS 'Hours', t.work_type AS 'Work Type',
                       t.status AS 'Status', t.notes AS 'Notes'
                FROM timesheet_entries t
                JOIN employees e ON e.id = t.employee_id
                WHERE t.job_id = ?
                ORDER BY t.work_date DESC, e.name
            """, (selected_job_id,))
            if by_job.empty:
                st.info("No timesheets saved for this job.")
            else:
                st.metric("Total Hours for Job", f"{float(by_job['Hours'].fillna(0).sum()):.2f}")
                st.dataframe(by_job, width="stretch", hide_index=True)


# =============================
# ESTIMATE WORKING SHEET
# =============================
def estimate_totals(estimate_id, labour_hours, labour_rate, material_allowance, access_equipment_allowance, subcontractor_allowance, sundries_allowance, margin_percent, contingency_percent, gst_percent):
    line_df = df_query("SELECT COALESCE(SUM(line_total), 0) AS line_total FROM estimate_line_items WHERE estimate_id = ?", (estimate_id,))
    line_total = float(line_df.iloc[0]["line_total"] or 0) if not line_df.empty else 0.0
    labour_total = float(labour_hours or 0) * float(labour_rate or 0)
    direct_total = line_total + labour_total + float(material_allowance or 0) + float(access_equipment_allowance or 0) + float(subcontractor_allowance or 0) + float(sundries_allowance or 0)
    contingency_amount = direct_total * (float(contingency_percent or 0) / 100)
    subtotal = direct_total + contingency_amount
    margin_amount = subtotal * (float(margin_percent or 0) / 100)
    total_ex_gst = subtotal + margin_amount
    gst_amount = total_ex_gst * (float(gst_percent or 0) / 100)
    total_inc_gst = total_ex_gst + gst_amount
    return {
        "line_total": round(line_total, 2),
        "labour_total": round(labour_total, 2),
        "direct_total": round(direct_total, 2),
        "contingency_amount": round(contingency_amount, 2),
        "margin_amount": round(margin_amount, 2),
        "total_ex_gst": round(total_ex_gst, 2),
        "gst_amount": round(gst_amount, 2),
        "total_inc_gst": round(total_inc_gst, 2),
    }


def recalc_estimate_totals(estimate_id):
    est = df_query("SELECT * FROM estimate_working_sheets WHERE id = ?", (estimate_id,))
    if est.empty:
        return
    r = est.iloc[0]
    totals = estimate_totals(
        estimate_id,
        r["labour_hours"], r["labour_rate"], r["material_allowance"], r["access_equipment_allowance"],
        r["subcontractor_allowance"], r["sundries_allowance"], r["margin_percent"], r["contingency_percent"], r["gst_percent"]
    )
    execute("""
        UPDATE estimate_working_sheets
        SET total_ex_gst = ?, gst_amount = ?, total_inc_gst = ?, updated_at = ?
        WHERE id = ?
    """, (totals["total_ex_gst"], totals["gst_amount"], totals["total_inc_gst"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), estimate_id))


def estimate_working_sheet_page():
    st.header("Estimate Working Sheet")
    st.caption("Build a working estimate and link it directly to the job it relates to.")

    job_options = get_job_options()
    if not job_options:
        st.info("Create a job first, then you can create an estimate working sheet.")
        return

    selected_job = st.selectbox("Select Job", list(job_options.keys()), key="estimate_job_select")
    selected_job_id = job_options[selected_job]

    job_details = df_query("""
        SELECT j.job_no AS 'Job No', j.job_name AS 'Job Name', bc.name AS 'Builder / Client',
               j.site_address AS 'Site Address', j.status AS 'Status', j.contract_value AS 'Contract Value'
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        WHERE j.id = ?
    """, (selected_job_id,))
    if not job_details.empty:
        st.dataframe(job_details, width="stretch", hide_index=True)

    estimates = df_query("""
        SELECT id, estimate_no, revision, estimate_date, status, total_ex_gst, total_inc_gst
        FROM estimate_working_sheets
        WHERE job_id = ?
        ORDER BY id DESC
    """, (selected_job_id,))

    with st.expander("Create New Estimate Working Sheet", expanded=estimates.empty):
        next_rev = len(estimates) + 1
        default_job_no = "EST"
        if not job_details.empty:
            default_job_no = str(job_details.iloc[0]["Job No"])
        with st.form("create_estimate_form"):
            col1, col2, col3 = st.columns(3)
            estimate_no = col1.text_input("Estimate No", value=f"{default_job_no}-EST-{next_rev:02d}")
            estimate_date = col2.text_input("Estimate Date", value=str(date.today()))
            revision = col3.text_input("Revision", value=f"Rev {next_rev}")
            notes = st.text_area("Initial Notes")
            created = st.form_submit_button("Create Estimate Working Sheet")
            if created:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                execute("""
                    INSERT INTO estimate_working_sheets
                    (job_id, estimate_no, estimate_date, revision, status, labour_hours, labour_rate,
                     material_allowance, access_equipment_allowance, subcontractor_allowance, sundries_allowance,
                     margin_percent, contingency_percent, gst_percent, total_ex_gst, gst_amount, total_inc_gst,
                     created_at, updated_at, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (selected_job_id, estimate_no, estimate_date, revision, "Draft", 0, 120, 0, 0, 0, 0, 20, 0, 10, 0, 0, 0, now, now, notes))
                st.success("Estimate working sheet created.")
                refresh()

    estimates = df_query("""
        SELECT id, estimate_no, revision, estimate_date, status, total_ex_gst, total_inc_gst
        FROM estimate_working_sheets
        WHERE job_id = ?
        ORDER BY id DESC
    """, (selected_job_id,))

    if estimates.empty:
        st.info("No estimate working sheets saved for this job yet.")
        return

    estimate_options = {
        f"{row['estimate_no']} - {row['revision']} - {row['status']} - ${float(row['total_inc_gst'] or 0):,.2f} inc GST": int(row["id"])
        for _, row in estimates.iterrows()
    }
    selected_estimate_label = st.selectbox("Select Estimate Working Sheet", list(estimate_options.keys()), key="estimate_select")
    selected_estimate_id = estimate_options[selected_estimate_label]

    current = df_query("SELECT * FROM estimate_working_sheets WHERE id = ?", (selected_estimate_id,))
    if current.empty:
        st.warning("Selected estimate could not be found.")
        return
    current = current.iloc[0]

    tab_summary, tab_lines, tab_view = st.tabs(["Summary / Pricing", "Line Items", "View / Export"])

    with tab_summary:
        with st.form("estimate_summary_form"):
            col1, col2, col3, col4 = st.columns(4)
            estimate_no = col1.text_input("Estimate No", value=str(current["estimate_no"] or ""))
            estimate_date = col2.text_input("Estimate Date", value=str(current["estimate_date"] or str(date.today())))
            revision = col3.text_input("Revision", value=str(current["revision"] or ""))
            statuses = ["Draft", "Sent", "Approved", "Lost", "Superseded"]
            current_status = str(current["status"] or "Draft")
            status_index = statuses.index(current_status) if current_status in statuses else 0
            status = col4.selectbox("Status", statuses, index=status_index)

            col5, col6 = st.columns(2)
            labour_hours = col5.number_input("Labour Hours", min_value=0.0, step=1.0, value=float(current["labour_hours"] or 0))
            labour_rate = col6.number_input("Labour Rate", min_value=0.0, step=5.0, value=float(current["labour_rate"] or 120))

            col7, col8, col9, col10 = st.columns(4)
            material_allowance = col7.number_input("Material Allowance", min_value=0.0, step=100.0, value=float(current["material_allowance"] or 0))
            access_equipment_allowance = col8.number_input("Access / Equipment Allowance", min_value=0.0, step=100.0, value=float(current["access_equipment_allowance"] or 0))
            subcontractor_allowance = col9.number_input("Subcontractor Allowance", min_value=0.0, step=100.0, value=float(current["subcontractor_allowance"] or 0))
            sundries_allowance = col10.number_input("Sundries / Consumables", min_value=0.0, step=50.0, value=float(current["sundries_allowance"] or 0))

            col11, col12, col13 = st.columns(3)
            margin_percent = col11.number_input("Margin %", min_value=0.0, step=1.0, value=float(current["margin_percent"] or 0))
            contingency_percent = col12.number_input("Contingency %", min_value=0.0, step=1.0, value=float(current["contingency_percent"] or 0))
            gst_percent = col13.number_input("GST %", min_value=0.0, step=1.0, value=float(current["gst_percent"] or 10))
            notes = st.text_area("Notes / Scope Notes", value=str(current["notes"] or ""))

            preview = estimate_totals(selected_estimate_id, labour_hours, labour_rate, material_allowance, access_equipment_allowance, subcontractor_allowance, sundries_allowance, margin_percent, contingency_percent, gst_percent)
            st.markdown("### Pricing Preview")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Direct Cost", f"${preview['direct_total']:,.2f}")
            c2.metric("Margin", f"${preview['margin_amount']:,.2f}")
            c3.metric("Total Ex GST", f"${preview['total_ex_gst']:,.2f}")
            c4.metric("Total Inc GST", f"${preview['total_inc_gst']:,.2f}")

            saved = st.form_submit_button("Save Estimate Summary")
            if saved:
                execute("""
                    UPDATE estimate_working_sheets
                    SET estimate_no = ?, estimate_date = ?, revision = ?, status = ?, labour_hours = ?, labour_rate = ?,
                        material_allowance = ?, access_equipment_allowance = ?, subcontractor_allowance = ?, sundries_allowance = ?,
                        margin_percent = ?, contingency_percent = ?, gst_percent = ?, total_ex_gst = ?, gst_amount = ?, total_inc_gst = ?,
                        updated_at = ?, notes = ?
                    WHERE id = ?
                """, (estimate_no, estimate_date, revision, status, labour_hours, labour_rate, material_allowance,
                      access_equipment_allowance, subcontractor_allowance, sundries_allowance, margin_percent, contingency_percent,
                      gst_percent, preview["total_ex_gst"], preview["gst_amount"], preview["total_inc_gst"],
                      datetime.now().strftime("%Y-%m-%d %H:%M:%S"), notes, selected_estimate_id))
                st.success("Estimate summary saved.")
                refresh()

    with tab_lines:
        st.subheader("Estimate Line Items")
        with st.form("add_estimate_line_form"):
            col1, col2 = st.columns(2)
            section = col1.selectbox("Section", ["Preliminaries", "Labour", "Materials", "Access / Equipment", "Subcontractor", "Variations", "Other"])
            item_description = col2.text_input("Item Description")
            col3, col4, col5 = st.columns(3)
            qty = col3.number_input("Qty", min_value=0.0, step=1.0)
            unit = col4.text_input("Unit", value="item")
            unit_rate = col5.number_input("Unit Rate", min_value=0.0, step=10.0)
            line_notes = st.text_area("Line Notes")
            added = st.form_submit_button("Add Line Item")
            if added and item_description:
                line_total = round(float(qty or 0) * float(unit_rate or 0), 2)
                execute("""
                    INSERT INTO estimate_line_items
                    (estimate_id, section, item_description, qty, unit, unit_rate, line_total, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (selected_estimate_id, section, item_description, qty, unit, unit_rate, line_total, line_notes))
                recalc_estimate_totals(selected_estimate_id)
                st.success("Line item added.")
                refresh()

        lines_df = df_query("""
            SELECT id, section AS 'Section', item_description AS 'Description', qty AS 'Qty', unit AS 'Unit',
                   unit_rate AS 'Unit Rate', line_total AS 'Line Total', notes AS 'Notes'
            FROM estimate_line_items
            WHERE estimate_id = ?
            ORDER BY id
        """, (selected_estimate_id,))
        if lines_df.empty:
            st.info("No line items added yet.")
        else:
            st.dataframe(lines_df.drop(columns=["id"]), width="stretch", hide_index=True)
            st.metric("Line Item Total", f"${float(lines_df['Line Total'].fillna(0).sum()):,.2f}")
            delete_options = {f"{r['Section']} - {r['Description']} - ${float(r['Line Total'] or 0):,.2f}": int(r["id"]) for _, r in lines_df.iterrows()}
            selected_delete = st.selectbox("Line item to delete", list(delete_options.keys()))
            confirm = st.checkbox("Confirm delete selected line item")
            if st.button("Delete Selected Line Item"):
                if not confirm:
                    st.error("Tick the confirm box first.")
                else:
                    execute("DELETE FROM estimate_line_items WHERE id = ?", (delete_options[selected_delete],))
                    recalc_estimate_totals(selected_estimate_id)
                    st.success("Line item deleted.")
                    refresh()

    with tab_view:
        summary_df = df_query("""
            SELECT e.estimate_no AS 'Estimate No', e.revision AS 'Revision', e.estimate_date AS 'Date', e.status AS 'Status',
                   j.job_no AS 'Job No', j.job_name AS 'Job Name', e.labour_hours AS 'Labour Hours', e.labour_rate AS 'Labour Rate',
                   e.material_allowance AS 'Material Allowance', e.access_equipment_allowance AS 'Access / Equipment',
                   e.subcontractor_allowance AS 'Subcontractor', e.sundries_allowance AS 'Sundries', e.margin_percent AS 'Margin %',
                   e.contingency_percent AS 'Contingency %', e.total_ex_gst AS 'Total Ex GST', e.gst_amount AS 'GST',
                   e.total_inc_gst AS 'Total Inc GST', e.notes AS 'Notes'
            FROM estimate_working_sheets e
            JOIN jobs j ON j.id = e.job_id
            WHERE e.id = ?
        """, (selected_estimate_id,))
        lines_export = df_query("""
            SELECT section AS 'Section', item_description AS 'Description', qty AS 'Qty', unit AS 'Unit',
                   unit_rate AS 'Unit Rate', line_total AS 'Line Total', notes AS 'Notes'
            FROM estimate_line_items
            WHERE estimate_id = ?
            ORDER BY id
        """, (selected_estimate_id,))
        st.markdown("### Estimate Summary")
        st.dataframe(summary_df, width="stretch", hide_index=True)
        st.markdown("### Estimate Lines")
        st.dataframe(lines_export, width="stretch", hide_index=True)

        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            summary_df.to_excel(writer, index=False, sheet_name="Estimate Summary")
            lines_export.to_excel(writer, index=False, sheet_name="Estimate Lines")
            for ws in writer.book.worksheets:
                for column_cells in ws.columns:
                    max_len = 0
                    col_letter = column_cells[0].column_letter
                    for cell in column_cells:
                        value = "" if cell.value is None else str(cell.value)
                        max_len = max(max_len, len(value))
                    ws.column_dimensions[col_letter].width = min(max(max_len + 2, 12), 45)
        output.seek(0)
        clean_name = str(summary_df.iloc[0]["Estimate No"] if not summary_df.empty else "estimate_working_sheet").replace("/", "-").replace("\\", "-")
        st.download_button(
            "Download Estimate Working Sheet Excel",
            data=output.getvalue(),
            file_name=f"{clean_name}_Estimate_Working_Sheet.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )



# =============================
# PRODUCT LIST RESTORE
# =============================
def restore_product_list():
    products = [('PB-H00001', 'Coverplus Interior L/S White', 'Haymes', '', 168.0, ''), ('PB-H00002', 'Elite Ceiling Toned White, 15L', 'Haymes', '15L', 90.0, ''), ('PB-H00003', 'Elite Ceiling White, 15L', 'Haymes', '15L', 90.0, ''), ('PB-H00004', 'Elite Interior Low Sheen White', 'Haymes', '', 118.0, ''), ('PB-H00005', 'Elite Interior Matt White, 15L', 'Haymes', '15L', 125.0, ''), ('PB-H00006', 'Elite Acrylic Sealer Undercoat', 'Haymes', '', 105.36, ''), ('PB-H00007', 'Elite Quick Dry Primer Undercoat', 'Haymes', '', 123.55, ''), ('PB-H00008', 'Expressions Low Sheen DKT, 4L', 'Haymes', '4L', 74.13, ''), ('PB-H00009', 'Expressions Low Sheen EDT, 4L', 'Haymes', '4L', 74.13, ''), ('PB-H00010', 'Expressions Low Sheen UDT, 4L', 'Haymes', '4L', 74.13, ''), ('PB-H00011', 'Expressions Low Sheen White', 'Haymes', '', 107.48, ''), ('PB-H00012', 'Expressions Low Sheen White', 'Haymes', '', 145.0, ''), ('PB-H00013', 'Expressions Low Sheen White, 4L', 'Haymes', '4L', 67.26, ''), ('PB-H00014', 'Solashield Low Sheen DKT, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00015', 'Solashield Low Sheen DKT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00016', 'Solashield Low Sheen DKT, 4L', 'Haymes', '4L', 73.55, ''), ('PB-H00017', 'Solashield Low Sheen EDT, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00018', 'Solashield Low Sheen EDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00019', 'Solashield Low Sheen EDT, 4L', 'Haymes', '4L', 73.55, ''), ('PB-H00020', 'Solashield Low Sheen UDT, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00021', 'Solashield Low Sheen UDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00022', 'Solashield Low Sheen UDT, 4L', 'Haymes', '4L', 73.55, ''), ('PB-H00023', 'Solashield Low Sheen White, 10L', 'Haymes', '10L', 107.42, ''), ('PB-H00024', 'Solashield Low Sheen White, 15L', 'Haymes', '15L', 148.0, ''), ('PB-H00025', 'Solashield Low Sheen White, 4L', 'Haymes', '4L', 67.4, ''), ('PB-H00026', 'R/Tex Roll On Coarse, 15L', 'Haymes', '15L', 175.0, ''), ('PB-H00027', 'Solashield Satin DKT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00028', 'Solashield Satin EDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00029', 'Solashield Satin UDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00030', 'Solashield Satin White, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00031', 'Solashield Satin White, 15L', 'Haymes', '15L', 148.0, ''), ('PB-H00032', 'Ultra Premium Primer Sealer', 'Haymes', '', 167.46, ''), ('PB-H00033', 'Acrylic Sealer Undercoat', 'Haymes', '', 120.0, ''), ('PB-H00034', 'Ultratrim High Gloss White', 'Haymes', '', 130.0, ''), ('PB-H00035', 'Ultratrim Semi Gloss White', 'Haymes', '', 130.0, ''), ('PB-H00036', 'Woodcare Aqualac Floor Satin', 'Haymes', '', 250.44, '')]

    restored = 0
    for row in products:
        execute("""
            INSERT OR REPLACE INTO products
            (product_code, product_name, supplier, unit, price_ex_gst, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, row)
        restored += 1

    return restored


def product_count():
    try:
        df = df_query("SELECT COUNT(*) AS 'count' FROM products")
        if not df.empty:
            return int(df.iloc[0]["count"])
    except Exception:
        pass
    return 0


def restore_taubmans_product_list():
    products = [('T ALL WEATHER L/S W15L 18', '187200/15L', '30001918', '15L', 145.0), ('T ALL WEATHER L/S A15L 18', '187204/15L', '30001923', '15L', 150.0), ('T ALL WEATHER L/S N15L 18', '187205/15L', '30001928', '15L', 150.0), ('T ALL WEATHER L/S D15L 18', '187209/15L', '30001942', '15L', 150.0), ('T ALL WEATHER L/S W10L 18', '187200/10L', '30001917', '10L', 120.0), ('T ALL WEATHER L/S A10L 18', '187204/10L', '30001922', '10L', 122.5), ('T ALL WEATHER L/S N10L 18', '187205/10L', '30001927', '10L', 122.5), ('T ALL WEATHER L/S D10L 18', '187209/10L', '30001941', '10L', 122.5), ('T ALL WEATHER L/S W4L 18', '187200/4L', '30001921', '4L', 57.5), ('T ALL WEATHER L/S A4L 18', '187204/4L', '30001926', '4L', 60.0), ('T ALL WEATHER L/S N4L 18', '187205/4L', '30001931', '4L', 60.0), ('T ALL WEATHER L/S D4L 18', '187209/4L', '30001944', '4L', 60.0), ('T ALL WEATHER MATT W15L 18', '187100/15L', '30001906', '15L', 145.0), ('T ALL WEATHER MATT A15L 18', '187104/15L', '30001910', '15L', 150.0), ('T ALL WEATHER MATT N15L 18', '187105/15L', '30001914', '15L', 150.0), ('T ALL WEATHER S/G W15L 18', '187400/15L', '30001950', '15L', 145.0), ('T ALL WEATHER S/G D15L 19', '187409/15L', '30001963', '15L', 150.0), ('T ALL WEATHER S/G A10L 19', '187404/10L', '30001954', '10L', 122.5), ('T ENDURE INT L/S W15L 18', '124200/15L', '30001368', '15L', 145.0), ('T ENDURE INT L/S W10L 18', '124200/10L', '30001367', '10L', 120.0), ('T ENDURE INT L/S W4L 18', '124200/4L', '30001371', '4L', 57.5), ('T ENDURE INT MATT W15L 18', '124100/15L', '30001356', '15L', 160.0), ('T ENDURE INT MATT W10L 18', '124100/10L', '30001355', '10L', 135.0), ('T ENDURE INT MATT W4L 18', '124100/4L', '30001359', '4L', 60.0), ('T PURE PERF L/S W15L 21', '279250/15L', '30008591', '15L', 145.0), ('T PURE PERF MATT W15L 21', '279150/15L', '30008588', '15L', 145.0), ('T PURE PERF CEILING W15L 21', '279050/15L', '30008581', '15L', 120.0), ('T Ceiling Premium W15L 22', '128000/15L', '30010919', '15L', 120.0), ('T PURE PERF WB ENAMEL GLOSS W10L 21', '279950/10L', '30008738', '10L', 122.0), ('T PURE PERF WB ENAMEL S/G W10L 21', '279850/10L', '30008596', '10L', 122.0), ('T PURE PERF WB ENAMEL GLOSS W4L 21', '279950/4L', '30008739', '4L', 65.0), ('T PURE PERF WB ENAMEL S/G W4L 21', '279850/4L', '30008737', '4L', 65.0), ('T WB ENAMEL GLOSS W10L 19', '121610/10L', '30001326', '10L', 125.0), ('T WB ENAMEL S/G W10L 19', '121410/10L', '30001294', '10L', 125.0), ('T WB ENAMEL GLOSS W4L 19', '121610/4L', '30001329', '4L', 65.0), ('T WB ENAMEL S/G W4L 19', '121410/4L', '30001297', '4L', 65.0), ('T ULTIMATE ENAMEL S/G W10L 19', '132810/10L', '30001427', '10L', 170.0), ('T ULTIMATE ENAMEL GLOSS W10L 19', '132910/10L', '30001441', '10L', 170.0), ('T ULTIMATE ENAMEL S/G W4L 19', '132810/4L', '30001429', '4L', 80.0), ('T ULTIMATE ENAMEL GLOSS W4L 19', '132910/4L', '30001443', '4L', 80.0), ('T TRADE EDGE UC W15L 16', '259500/15L', '30002265', '15L', 90.0), ('T ULTRA PREP W15L 09', '288500/15L', '30002664', '15L', 110.0), ('T TRADEX ULTRAPREP 15L', '274520/15L', '30002331', '15L', 105.0), ('T PURE PERF PREP W15L 21', '279550/15L', '30008595', '15L', 120.0), ('T TRADEX CEILING W15L 15', '274000/15L', '30002310', '15L', 100.0), ('T PRO INT L/S W15L 20', '278200/15L', '30002370', '15L', 120.0), ('T PRO EXT L/S W15L 20', '278710/15L', '30002387', '15L', 135.0), ('T PRO ENAMEL W/B GLOSS W10L20', '278600/10L', '30002381', '10L', 120.0), ('T PRO ENAMEL W/B S/G W10L 20', '278400/10L', '30002376', '10L', 120.0), ('T PRO CEILING W15L 20', '278000/15L', '30002364', '15L', 105.0), ('T 3IN1 W15L 15', '108100/15L', '30000957', '15L', 130.0), ('T 3IN1 W4L 15', '108100/4L', '30000960', '4L', 60.0), ('J PRO DECK OIL NAT 10L 17', '481200/10L', '30004332', '10L', 170.0), ('J PRO DECK OIL NAT 4L 17', '481200/4L', '30004334', '4L', 75.0), ('J PRO EXT CLEAR GLOSS 4L 17', '481121/4L', '30004331', '4L', 80.0), ('J PRO EXT CLEAR SATIN 4L 17', '481120/4L', '30004328', '4L', 80.0), ('T ARMAWALL A/SHIELD W15L 09', '310400/15L', '30003018', '15L', 150.0), ('T ARMAWALL PRIMER 15L 09', '315500/15L', '30003036', '15L', 135.0), ('T ARMAWALL SEALER BOND C10L', '315705/10L', '30003039', '10L', 135.0), ('T ARMAWALL SEALER BOND W10L', '315700/10L', '30003038', '10L', 135.0)]

    restored = 0
    for product_name, product_code, taubmans_sku, unit, price_ex_gst in products:
        execute("""
            INSERT OR REPLACE INTO products
            (product_code, product_name, supplier, unit, price_ex_gst, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            product_code,
            product_name,
            "Taubmans",
            unit,
            float(price_ex_gst),
            f"Taubmans SKU: {taubmans_sku} | Source: uploaded Premier Brushworks Taubmans price list"
        ))
        restored += 1

    return restored


def taubmans_product_count():
    try:
        df = df_query("SELECT COUNT(*) AS 'count' FROM products WHERE supplier = 'Taubmans'")
        if not df.empty:
            return int(df.iloc[0]["count"])
    except Exception:
        pass
    return 0





def restore_haymes_and_taubmans_product_lists():
    products = [('PB-H00001', 'Coverplus Interior L/S White', 'Haymes', '', 168.0, ''), ('PB-H00002', 'Elite Ceiling Toned White, 15L', 'Haymes', '15L', 90.0, ''), ('PB-H00003', 'Elite Ceiling White, 15L', 'Haymes', '15L', 90.0, ''), ('PB-H00004', 'Elite Interior Low Sheen White', 'Haymes', '', 118.0, ''), ('PB-H00005', 'Elite Interior Matt White, 15L', 'Haymes', '15L', 125.0, ''), ('PB-H00006', 'Elite Acrylic Sealer Undercoat', 'Haymes', '', 105.36, ''), ('PB-H00007', 'Elite Quick Dry Primer Undercoat', 'Haymes', '', 123.55, ''), ('PB-H00008', 'Expressions Low Sheen DKT, 4L', 'Haymes', '4L', 74.13, ''), ('PB-H00009', 'Expressions Low Sheen EDT, 4L', 'Haymes', '4L', 74.13, ''), ('PB-H00010', 'Expressions Low Sheen UDT, 4L', 'Haymes', '4L', 74.13, ''), ('PB-H00011', 'Expressions Low Sheen White', 'Haymes', '', 107.48, ''), ('PB-H00012', 'Expressions Low Sheen White', 'Haymes', '', 145.0, ''), ('PB-H00013', 'Expressions Low Sheen White, 4L', 'Haymes', '4L', 67.26, ''), ('PB-H00014', 'Solashield Low Sheen DKT, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00015', 'Solashield Low Sheen DKT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00016', 'Solashield Low Sheen DKT, 4L', 'Haymes', '4L', 73.55, ''), ('PB-H00017', 'Solashield Low Sheen EDT, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00018', 'Solashield Low Sheen EDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00019', 'Solashield Low Sheen EDT, 4L', 'Haymes', '4L', 73.55, ''), ('PB-H00020', 'Solashield Low Sheen UDT, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00021', 'Solashield Low Sheen UDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00022', 'Solashield Low Sheen UDT, 4L', 'Haymes', '4L', 73.55, ''), ('PB-H00023', 'Solashield Low Sheen White, 10L', 'Haymes', '10L', 107.42, ''), ('PB-H00024', 'Solashield Low Sheen White, 15L', 'Haymes', '15L', 148.0, ''), ('PB-H00025', 'Solashield Low Sheen White, 4L', 'Haymes', '4L', 67.4, ''), ('PB-H00026', 'R/Tex Roll On Coarse, 15L', 'Haymes', '15L', 175.0, ''), ('PB-H00027', 'Solashield Satin DKT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00028', 'Solashield Satin EDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00029', 'Solashield Satin UDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00030', 'Solashield Satin White, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00031', 'Solashield Satin White, 15L', 'Haymes', '15L', 148.0, ''), ('PB-H00032', 'Ultra Premium Primer Sealer', 'Haymes', '', 167.46, ''), ('PB-H00033', 'Acrylic Sealer Undercoat', 'Haymes', '', 120.0, ''), ('PB-H00034', 'Ultratrim High Gloss White', 'Haymes', '', 130.0, ''), ('PB-H00035', 'Ultratrim Semi Gloss White', 'Haymes', '', 130.0, ''), ('PB-H00036', 'Woodcare Aqualac Floor Satin', 'Haymes', '', 250.44, ''), ('187200/15L', 'T ALL WEATHER L/S W15L 18', 'Taubmans', '15L', 145.0, 'Taubmans SKU: 30001918 | Source: uploaded Premier Brushworks Taubmans price list'), ('187204/15L', 'T ALL WEATHER L/S A15L 18', 'Taubmans', '15L', 150.0, 'Taubmans SKU: 30001923 | Source: uploaded Premier Brushworks Taubmans price list'), ('187205/15L', 'T ALL WEATHER L/S N15L 18', 'Taubmans', '15L', 150.0, 'Taubmans SKU: 30001928 | Source: uploaded Premier Brushworks Taubmans price list'), ('187209/15L', 'T ALL WEATHER L/S D15L 18', 'Taubmans', '15L', 150.0, 'Taubmans SKU: 30001942 | Source: uploaded Premier Brushworks Taubmans price list'), ('187200/10L', 'T ALL WEATHER L/S W10L 18', 'Taubmans', '10L', 120.0, 'Taubmans SKU: 30001917 | Source: uploaded Premier Brushworks Taubmans price list'), ('187204/10L', 'T ALL WEATHER L/S A10L 18', 'Taubmans', '10L', 122.5, 'Taubmans SKU: 30001922 | Source: uploaded Premier Brushworks Taubmans price list'), ('187205/10L', 'T ALL WEATHER L/S N10L 18', 'Taubmans', '10L', 122.5, 'Taubmans SKU: 30001927 | Source: uploaded Premier Brushworks Taubmans price list'), ('187209/10L', 'T ALL WEATHER L/S D10L 18', 'Taubmans', '10L', 122.5, 'Taubmans SKU: 30001941 | Source: uploaded Premier Brushworks Taubmans price list'), ('187200/4L', 'T ALL WEATHER L/S W4L 18', 'Taubmans', '4L', 57.5, 'Taubmans SKU: 30001921 | Source: uploaded Premier Brushworks Taubmans price list'), ('187204/4L', 'T ALL WEATHER L/S A4L 18', 'Taubmans', '4L', 60.0, 'Taubmans SKU: 30001926 | Source: uploaded Premier Brushworks Taubmans price list'), ('187205/4L', 'T ALL WEATHER L/S N4L 18', 'Taubmans', '4L', 60.0, 'Taubmans SKU: 30001931 | Source: uploaded Premier Brushworks Taubmans price list'), ('187209/4L', 'T ALL WEATHER L/S D4L 18', 'Taubmans', '4L', 60.0, 'Taubmans SKU: 30001944 | Source: uploaded Premier Brushworks Taubmans price list'), ('187100/15L', 'T ALL WEATHER MATT W15L 18', 'Taubmans', '15L', 145.0, 'Taubmans SKU: 30001906 | Source: uploaded Premier Brushworks Taubmans price list'), ('187104/15L', 'T ALL WEATHER MATT A15L 18', 'Taubmans', '15L', 150.0, 'Taubmans SKU: 30001910 | Source: uploaded Premier Brushworks Taubmans price list'), ('187105/15L', 'T ALL WEATHER MATT N15L 18', 'Taubmans', '15L', 150.0, 'Taubmans SKU: 30001914 | Source: uploaded Premier Brushworks Taubmans price list'), ('187400/15L', 'T ALL WEATHER S/G W15L 18', 'Taubmans', '15L', 145.0, 'Taubmans SKU: 30001950 | Source: uploaded Premier Brushworks Taubmans price list'), ('187409/15L', 'T ALL WEATHER S/G D15L 19', 'Taubmans', '15L', 150.0, 'Taubmans SKU: 30001963 | Source: uploaded Premier Brushworks Taubmans price list'), ('187404/10L', 'T ALL WEATHER S/G A10L 19', 'Taubmans', '10L', 122.5, 'Taubmans SKU: 30001954 | Source: uploaded Premier Brushworks Taubmans price list'), ('124200/15L', 'T ENDURE INT L/S W15L 18', 'Taubmans', '15L', 145.0, 'Taubmans SKU: 30001368 | Source: uploaded Premier Brushworks Taubmans price list'), ('124200/10L', 'T ENDURE INT L/S W10L 18', 'Taubmans', '10L', 120.0, 'Taubmans SKU: 30001367 | Source: uploaded Premier Brushworks Taubmans price list'), ('124200/4L', 'T ENDURE INT L/S W4L 18', 'Taubmans', '4L', 57.5, 'Taubmans SKU: 30001371 | Source: uploaded Premier Brushworks Taubmans price list'), ('124100/15L', 'T ENDURE INT MATT W15L 18', 'Taubmans', '15L', 160.0, 'Taubmans SKU: 30001356 | Source: uploaded Premier Brushworks Taubmans price list'), ('124100/10L', 'T ENDURE INT MATT W10L 18', 'Taubmans', '10L', 135.0, 'Taubmans SKU: 30001355 | Source: uploaded Premier Brushworks Taubmans price list'), ('124100/4L', 'T ENDURE INT MATT W4L 18', 'Taubmans', '4L', 60.0, 'Taubmans SKU: 30001359 | Source: uploaded Premier Brushworks Taubmans price list'), ('279250/15L', 'T PURE PERF L/S W15L 21', 'Taubmans', '15L', 145.0, 'Taubmans SKU: 30008591 | Source: uploaded Premier Brushworks Taubmans price list'), ('279150/15L', 'T PURE PERF MATT W15L 21', 'Taubmans', '15L', 145.0, 'Taubmans SKU: 30008588 | Source: uploaded Premier Brushworks Taubmans price list'), ('279050/15L', 'T PURE PERF CEILING W15L 21', 'Taubmans', '15L', 120.0, 'Taubmans SKU: 30008581 | Source: uploaded Premier Brushworks Taubmans price list'), ('128000/15L', 'T Ceiling Premium W15L 22', 'Taubmans', '15L', 120.0, 'Taubmans SKU: 30010919 | Source: uploaded Premier Brushworks Taubmans price list'), ('279950/10L', 'T PURE PERF WB ENAMEL GLOSS W10L 21', 'Taubmans', '10L', 122.0, 'Taubmans SKU: 30008738 | Source: uploaded Premier Brushworks Taubmans price list'), ('279850/10L', 'T PURE PERF WB ENAMEL S/G W10L 21', 'Taubmans', '10L', 122.0, 'Taubmans SKU: 30008596 | Source: uploaded Premier Brushworks Taubmans price list'), ('279950/4L', 'T PURE PERF WB ENAMEL GLOSS W4L 21', 'Taubmans', '4L', 65.0, 'Taubmans SKU: 30008739 | Source: uploaded Premier Brushworks Taubmans price list'), ('279850/4L', 'T PURE PERF WB ENAMEL S/G W4L 21', 'Taubmans', '4L', 65.0, 'Taubmans SKU: 30008737 | Source: uploaded Premier Brushworks Taubmans price list'), ('121610/10L', 'T WB ENAMEL GLOSS W10L 19', 'Taubmans', '10L', 125.0, 'Taubmans SKU: 30001326 | Source: uploaded Premier Brushworks Taubmans price list'), ('121410/10L', 'T WB ENAMEL S/G W10L 19', 'Taubmans', '10L', 125.0, 'Taubmans SKU: 30001294 | Source: uploaded Premier Brushworks Taubmans price list'), ('121610/4L', 'T WB ENAMEL GLOSS W4L 19', 'Taubmans', '4L', 65.0, 'Taubmans SKU: 30001329 | Source: uploaded Premier Brushworks Taubmans price list'), ('121410/4L', 'T WB ENAMEL S/G W4L 19', 'Taubmans', '4L', 65.0, 'Taubmans SKU: 30001297 | Source: uploaded Premier Brushworks Taubmans price list'), ('132810/10L', 'T ULTIMATE ENAMEL S/G W10L 19', 'Taubmans', '10L', 170.0, 'Taubmans SKU: 30001427 | Source: uploaded Premier Brushworks Taubmans price list'), ('132910/10L', 'T ULTIMATE ENAMEL GLOSS W10L 19', 'Taubmans', '10L', 170.0, 'Taubmans SKU: 30001441 | Source: uploaded Premier Brushworks Taubmans price list'), ('132810/4L', 'T ULTIMATE ENAMEL S/G W4L 19', 'Taubmans', '4L', 80.0, 'Taubmans SKU: 30001429 | Source: uploaded Premier Brushworks Taubmans price list'), ('132910/4L', 'T ULTIMATE ENAMEL GLOSS W4L 19', 'Taubmans', '4L', 80.0, 'Taubmans SKU: 30001443 | Source: uploaded Premier Brushworks Taubmans price list'), ('259500/15L', 'T TRADE EDGE UC W15L 16', 'Taubmans', '15L', 90.0, 'Taubmans SKU: 30002265 | Source: uploaded Premier Brushworks Taubmans price list'), ('288500/15L', 'T ULTRA PREP W15L 09', 'Taubmans', '15L', 110.0, 'Taubmans SKU: 30002664 | Source: uploaded Premier Brushworks Taubmans price list'), ('274520/15L', 'T TRADEX ULTRAPREP 15L', 'Taubmans', '15L', 105.0, 'Taubmans SKU: 30002331 | Source: uploaded Premier Brushworks Taubmans price list'), ('279550/15L', 'T PURE PERF PREP W15L 21', 'Taubmans', '15L', 120.0, 'Taubmans SKU: 30008595 | Source: uploaded Premier Brushworks Taubmans price list'), ('274000/15L', 'T TRADEX CEILING W15L 15', 'Taubmans', '15L', 100.0, 'Taubmans SKU: 30002310 | Source: uploaded Premier Brushworks Taubmans price list'), ('278200/15L', 'T PRO INT L/S W15L 20', 'Taubmans', '15L', 120.0, 'Taubmans SKU: 30002370 | Source: uploaded Premier Brushworks Taubmans price list'), ('278710/15L', 'T PRO EXT L/S W15L 20', 'Taubmans', '15L', 135.0, 'Taubmans SKU: 30002387 | Source: uploaded Premier Brushworks Taubmans price list'), ('278600/10L', 'T PRO ENAMEL W/B GLOSS W10L20', 'Taubmans', '10L', 120.0, 'Taubmans SKU: 30002381 | Source: uploaded Premier Brushworks Taubmans price list'), ('278400/10L', 'T PRO ENAMEL W/B S/G W10L 20', 'Taubmans', '10L', 120.0, 'Taubmans SKU: 30002376 | Source: uploaded Premier Brushworks Taubmans price list'), ('278000/15L', 'T PRO CEILING W15L 20', 'Taubmans', '15L', 105.0, 'Taubmans SKU: 30002364 | Source: uploaded Premier Brushworks Taubmans price list'), ('108100/15L', 'T 3IN1 W15L 15', 'Taubmans', '15L', 130.0, 'Taubmans SKU: 30000957 | Source: uploaded Premier Brushworks Taubmans price list'), ('108100/4L', 'T 3IN1 W4L 15', 'Taubmans', '4L', 60.0, 'Taubmans SKU: 30000960 | Source: uploaded Premier Brushworks Taubmans price list'), ('481200/10L', 'J PRO DECK OIL NAT 10L 17', 'Taubmans', '10L', 170.0, 'Taubmans SKU: 30004332 | Source: uploaded Premier Brushworks Taubmans price list'), ('481200/4L', 'J PRO DECK OIL NAT 4L 17', 'Taubmans', '4L', 75.0, 'Taubmans SKU: 30004334 | Source: uploaded Premier Brushworks Taubmans price list'), ('481121/4L', 'J PRO EXT CLEAR GLOSS 4L 17', 'Taubmans', '4L', 80.0, 'Taubmans SKU: 30004331 | Source: uploaded Premier Brushworks Taubmans price list'), ('481120/4L', 'J PRO EXT CLEAR SATIN 4L 17', 'Taubmans', '4L', 80.0, 'Taubmans SKU: 30004328 | Source: uploaded Premier Brushworks Taubmans price list'), ('310400/15L', 'T ARMAWALL A/SHIELD W15L 09', 'Taubmans', '15L', 150.0, 'Taubmans SKU: 30003018 | Source: uploaded Premier Brushworks Taubmans price list'), ('315500/15L', 'T ARMAWALL PRIMER 15L 09', 'Taubmans', '15L', 135.0, 'Taubmans SKU: 30003036 | Source: uploaded Premier Brushworks Taubmans price list'), ('315705/10L', 'T ARMAWALL SEALER BOND C10L', 'Taubmans', '10L', 135.0, 'Taubmans SKU: 30003039 | Source: uploaded Premier Brushworks Taubmans price list'), ('315700/10L', 'T ARMAWALL SEALER BOND W10L', 'Taubmans', '10L', 135.0, 'Taubmans SKU: 30003038 | Source: uploaded Premier Brushworks Taubmans price list')]

    restored = 0
    for product_code, product_name, supplier, unit, price_ex_gst, notes in products:
        execute("""
            INSERT OR REPLACE INTO products
            (product_code, product_name, supplier, unit, price_ex_gst, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            product_code,
            product_name,
            supplier,
            unit,
            float(price_ex_gst or 0),
            notes
        ))
        restored += 1

    return restored


def haymes_product_count():
    try:
        df = df_query("SELECT COUNT(*) AS 'count' FROM products WHERE supplier = 'Haymes'")
        if not df.empty:
            return int(df.iloc[0]["count"])
    except Exception:
        pass
    return 0


def combined_paint_product_count():
    try:
        df = df_query("SELECT COUNT(*) AS 'count' FROM products WHERE supplier IN ('Haymes', 'Taubmans')")
        if not df.empty:
            return int(df.iloc[0]["count"])
    except Exception:
        pass
    return 0


def restore_builders_clients_and_employees():
    builders = [('Builder', 'Ausmar Homes Pty Ltd', 'Compliance Team', '07 5319 1500', 'compliance@ausmargroup.com.au', '8 Flinders Lane, Maroochydore QLD 4558', '1083000', '55 087 236 208', '30 Days', 'Annual Period Trade Contract'), ('Developer / Builder', 'OneLife Property Group', 'Bryce Curran', '0421 069 817', 'brycecurran@hotmail.com', 'Sunshine Coast', '', '', '30 Days', 'Multi-residential complexes'), ('Builder', 'Thompson Homes', '', '', '', '', '', '', '30 Days', 'Existing JobHub builder'), ('Client / Developer', 'Palm Lakes', '', '', '', 'Pelican Waters', '', '', '30 Days', 'Palm Lakes Pelican Waters'), ('Interior Designer', 'Box Clever Interiors', 'Design Team', '07 5309 5640', 'info@boxcleverinteriors.com.au', 'PO Box 208, Moffat Beach QLD 4551', '', '08 007 428 613', '', 'Bannister project designer'), ('Interior Designer', 'Inka Interiors', 'Sheena Hanks', '0438 308 672', 'info@inkainteriors.com.au', 'Basement Level, 811 Stanley St, Woolloongabba', '', '', '', 'Cunningham project designer'), ('Painting Contractor', 'Emerald Painting Company Pty Ltd', 'Anthony Des Johnston', '0410 949 719', 'des@emeraldpainting.com.au', '20 Warenna Crescent, Glenvale QLD 4350', '', '85 169 333 957', '', 'Industry contact'), ('Supplier', 'Dulux Australia', '', '07 5443 7255', '', 'Cnr Amaroo St & Maroochydore Rd, Maroochydore QLD 4558', '', '67 000 049 427', '', 'Supplier'), ('Builder', 'Greenrock Building', '', '', '', '', '', '', '30 Days', 'Client history'), ('Builder', 'Rejuvenate Group', '', '', '', '', '', '', '30 Days', 'School works'), ('Builder', 'Adlar Homes', '', '', '', 'Maroochydore', '', '', '30 Days', 'Client history'), ('Builder', 'Darren Hunt Homes', '', '', '', '', '', '', '30 Days', 'Custom homes'), ('Builder', 'Watherston Building', '', '', '', '', '', '', '30 Days', 'Custom homes'), ('Commercial Client', 'Stockland Aura', '', '', '', 'Aura', '', '', '', 'Commercial developments'), ('Commercial Builder', 'FDC Constructions', 'Simon Hawkins / Adam Pickering', '', '', '', '', '', '', 'Outreach'), ('Commercial Client', 'Comiskey Group', 'Paul / David / Rob & team', '', '', 'Sunshine Coast', '', '', '', 'Hospitality venue'), ('Education Client', 'Nambour State College', '', '', '', 'Nambour', '', '', '', 'School works'), ('Education Client', 'Currimundi State School', '', '', '', 'Currimundi', '', '', '', 'School works'), ('Education Client', 'Currimundi Special School', '', '', '', 'Currimindi', '', '', '', 'School works'), ('Education Client', 'Gympie South State School', '', '', '', 'Gympie', '', '', '', 'School works'), ('Education Client', 'Good Shepherd Lutheran School', '', '', '', '', '', '', '', 'School works')]

    employees = [('Bryce', '', '', 60.0, 66.0, 'Active', ''), ('Brodrick', '', '', 45.0, 49.5, 'Active', ''), ('Sol', '', '', 50.0, 55.0, 'Active', ''), ('Critter', '', '', 40.0, 44.0, 'Active', ''), ('Greg', '', '', 46.0, 50.6, 'Active', ''), ('Chris Nagy', '', '', 50.0, 55.0, 'Active', ''), ('Isaac', '', '', 46.0, 50.6, 'Active', ''), ('Rob Pullin', '', '', 45.0, 49.5, 'Active', ''), ('Ian', '', '', 46.0, 50.6, 'Active', ''), ('Tim', '', '', 45.0, 49.5, 'Active', ''), ('Anth', '', '', 35.0, 38.5, 'Active', ''), ('River', '', '', 32.5, 35.75, 'Active', ''), ('Dipper', '', '', 45.0, 49.5, 'Active', ''), ('Vlad 1', '', '', 45.0, 49.5, 'Active', ''), ('Vlad 2', '', '', 45.0, 49.5, 'Active', ''), ('Ryan', '', '', 45.0, 49.5, 'Active', '')]

    restored_builders = 0
    restored_employees = 0

    for row in builders:
        execute("""
            INSERT OR REPLACE INTO builders_clients
            (type, name, contact_name, phone, email, address, qbcc, abn, terms, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, row)
        restored_builders += 1

    for row in employees:
        execute("""
            INSERT OR REPLACE INTO employees
            (name, role, phone, base_hourly_rate, rate_plus_10, status, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, row)
        restored_employees += 1

    # Recreate employee login accounts where missing, without duplicating existing logins.
    try:
        seed_app_users()
    except Exception:
        pass

    return restored_builders, restored_employees


def builders_clients_count():
    try:
        df = df_query("SELECT COUNT(*) AS 'count' FROM builders_clients")
        if not df.empty:
            return int(df.iloc[0]["count"])
    except Exception:
        pass
    return 0


def employees_count():
    try:
        df = df_query("SELECT COUNT(*) AS 'count' FROM employees")
        if not df.empty:
            return int(df.iloc[0]["count"])
    except Exception:
        pass
    return 0




# =============================
# USER ACCOUNT DUPLICATE CLEANUP
# =============================
def normalise_username_value(username):
    return str(username or "").strip().lower()


def user_duplicate_summary():
    try:
        users = df_query("""
            SELECT u.id,
                   u.username,
                   u.role,
                   u.employee_id,
                   u.active,
                   COALESCE(e.name, '') AS employee_name,
                   u.notes
            FROM app_users u
            LEFT JOIN employees e ON e.id = u.employee_id
            ORDER BY LOWER(TRIM(u.username)), u.id
        """)
    except Exception:
        return pd.DataFrame()

    if users.empty:
        return users

    duplicate_ids = set()

    # Same username duplicates, ignoring case/spaces.
    username_groups = {}
    for _, row in users.iterrows():
        key = normalise_username_value(row["username"])
        if key:
            username_groups.setdefault(key, []).append(int(row["id"]))

    for ids in username_groups.values():
        if len(ids) > 1:
            duplicate_ids.update(ids)

    # Same linked employee duplicates.
    employee_groups = {}
    for _, row in users.iterrows():
        try:
            emp_id = int(row["employee_id"]) if row["employee_id"] not in [None, "", "None"] and pd.notna(row["employee_id"]) else None
        except Exception:
            emp_id = None
        if emp_id:
            employee_groups.setdefault(emp_id, []).append(int(row["id"]))

    for ids in employee_groups.values():
        if len(ids) > 1:
            duplicate_ids.update(ids)

    if not duplicate_ids:
        return pd.DataFrame()

    return users[users["id"].isin(duplicate_ids)].copy()


def clean_duplicate_user_accounts():
    """
    Deletes duplicate login rows.
    Keeps:
    - the currently logged-in user if they are in a duplicate group
    - otherwise an active admin where possible
    - otherwise an active account
    - otherwise the lowest id
    """
    users = df_query("""
        SELECT u.id,
               u.username,
               u.role,
               u.employee_id,
               u.active,
               COALESCE(e.name, '') AS employee_name,
               u.notes
        FROM app_users u
        LEFT JOIN employees e ON e.id = u.employee_id
        ORDER BY u.id
    """)

    if users.empty:
        return {"deleted": 0, "kept": 0, "skipped": 0}

    current_user = get_current_user() or {}
    current_user_id = int(current_user.get("id", -1))

    ids_to_delete = set()
    keep_ids = set()

    def choose_keep(group_df):
        # Keep current logged-in user if present.
        current_rows = group_df[group_df["id"].astype(int) == current_user_id]
        if not current_rows.empty:
            return int(current_rows.iloc[0]["id"])

        # Prefer active admin.
        active_admin = group_df[
            (group_df["role"].astype(str) == "admin") &
            (group_df["active"].fillna(0).astype(int) == 1)
        ]
        if not active_admin.empty:
            return int(active_admin.sort_values("id").iloc[0]["id"])

        # Prefer active account.
        active = group_df[group_df["active"].fillna(0).astype(int) == 1]
        if not active.empty:
            return int(active.sort_values("id").iloc[0]["id"])

        # Otherwise keep first row.
        return int(group_df.sort_values("id").iloc[0]["id"])

    # Duplicates by username.
    users["_username_key"] = users["username"].apply(normalise_username_value)
    for key, group in users.groupby("_username_key"):
        if key and len(group) > 1:
            keep_id = choose_keep(group)
            keep_ids.add(keep_id)
            for uid in group["id"].astype(int).tolist():
                if uid != keep_id:
                    ids_to_delete.add(uid)

    # Duplicates by linked employee.
    linked = users[users["employee_id"].notna()].copy()
    if not linked.empty:
        for emp_id, group in linked.groupby("employee_id"):
            if emp_id not in [None, "", "None"] and len(group) > 1:
                keep_id = choose_keep(group)
                keep_ids.add(keep_id)
                for uid in group["id"].astype(int).tolist():
                    if uid != keep_id:
                        ids_to_delete.add(uid)

    # Never delete current user.
    ids_to_delete.discard(current_user_id)

    # Never delete last active admin.
    admin_count_df = df_query("""
        SELECT COUNT(*) AS 'count'
        FROM app_users
        WHERE role = 'admin' AND active = 1
    """)
    active_admin_count = int(admin_count_df.iloc[0]["count"]) if not admin_count_df.empty else 0

    skipped = 0
    deleted = 0

    for uid in sorted(ids_to_delete):
        row_df = users[users["id"].astype(int) == int(uid)]
        if row_df.empty:
            continue

        row = row_df.iloc[0]
        is_active_admin = str(row["role"]) == "admin" and int(row["active"] or 0) == 1

        if is_active_admin and active_admin_count <= 1:
            skipped += 1
            continue

        try:
            execute("DELETE FROM app_users WHERE id = ?", (int(uid),))
            deleted += 1
            if is_active_admin:
                active_admin_count -= 1
        except Exception:
            # If deletion fails, safely disable it instead.
            try:
                execute("UPDATE app_users SET active = 0, notes = COALESCE(notes, '') || ' | duplicate disabled' WHERE id = ?", (int(uid),))
                skipped += 1
            except Exception:
                skipped += 1

    # Add unique indexes after cleanup so they cannot double up again.
    try:
        execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_app_users_username_lower_unique ON app_users (LOWER(TRIM(username)))")
    except Exception:
        pass

    try:
        execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_app_users_employee_unique ON app_users (employee_id) WHERE employee_id IS NOT NULL")
    except Exception:
        pass

    return {"deleted": deleted, "kept": len(keep_ids), "skipped": skipped}



# =============================
# USER LINK SAFETY
# =============================
def employee_linked_to_other_user(employee_id, selected_user_id):
    """
    Returns the other user account already linked to an employee, if any.
    Prevents app_users.employee_id unique constraint crashes.
    """
    if employee_id in [None, "", "None"]:
        return pd.DataFrame()

    try:
        return df_query("""
            SELECT id, username, role, active
            FROM app_users
            WHERE employee_id = ? AND id <> ?
            LIMIT 1
        """, (employee_id, selected_user_id))
    except Exception:
        return pd.DataFrame()


def safe_update_user_account(selected_user_id, username, role, employee_id, active, notes):
    """
    Safely updates app_users and prevents duplicate employee login links.
    Returns (success, message).
    """
    username = str(username or "").strip()

    if not username:
        return False, "Username cannot be blank."

    # Check username duplicate, ignoring case/spaces.
    existing_username = df_query("""
        SELECT id, username
        FROM app_users
        WHERE LOWER(TRIM(username)) = LOWER(TRIM(?)) AND id <> ?
        LIMIT 1
    """, (username, selected_user_id))

    if not existing_username.empty:
        return False, f"Username '{username}' is already used by another account."

    # Check employee duplicate link.
    other_link = employee_linked_to_other_user(employee_id, selected_user_id)
    if not other_link.empty:
        other = other_link.iloc[0]
        return False, (
            f"This employee is already linked to user account '{other['username']}'. "
            "Delete, disable, or unlink that duplicate account first, or choose 'No Employee Link'."
        )

    try:
        execute("""
            UPDATE app_users
            SET username = ?, role = ?, employee_id = ?, active = ?, notes = ?
            WHERE id = ?
        """, (username, role, employee_id, active, notes, selected_user_id))
        return True, "User updated."
    except Exception as e:
        message = str(e)
        if "idx_app_users_employee_unique" in message or "app_users_employee_id" in message or "duplicate key" in message:
            return False, (
                "That employee is already linked to another user account. "
                "Open User Access and use Clean Duplicate User Accounts, or select No Employee Link."
            )
        return False, f"User update failed: {message}"



# =============================
# BULK USER ACCOUNT DELETE
# =============================
# =============================
# BULK EMPLOYEE DELETE / DEACTIVATE
# =============================

# =============================
# LINKED USER / EMPLOYEE DELETE
# =============================
def employee_has_job_history(employee_id):
    """
    Employees with wage/timesheet history should not be fully deleted because
    deleting them can break job costing history. They are marked Inactive instead.
    """
    linked = []

    for table, column, label in [
        ("wage_entries", "employee_id", "wage records"),
        ("timesheet_entries", "employee_id", "timesheets"),
    ]:
        try:
            if has_related_records(table, column, employee_id):
                linked.append(label)
        except Exception:
            pass

    return linked


def delete_employee_and_linked_users(employee_id):
    """
    Employee delete button behaviour:
    - Deletes linked app user login account(s).
    - Deletes the employee record only if there is no wage/timesheet history.
    - If history exists, the employee is marked Inactive.
    - Protects current logged-in user and last active admin.
    """
    result = {
        "deleted_users": 0,
        "deleted_employee": 0,
        "deactivated_employee": 0,
        "skipped": 0,
        "messages": [],
    }

    try:
        employee_id = int(employee_id)
    except Exception:
        result["skipped"] += 1
        result["messages"].append("Invalid employee id.")
        return result

    emp_df = df_query("SELECT id, name, status FROM employees WHERE id = ? LIMIT 1", (employee_id,))
    if emp_df.empty:
        result["skipped"] += 1
        result["messages"].append(f"Employee id {employee_id} not found.")
        return result

    employee_name = str(emp_df.iloc[0]["name"])

    current_user = get_current_user() or {}
    try:
        current_user_id = int(current_user.get("id", -1))
    except Exception:
        current_user_id = -1

    linked_users = df_query("""
        SELECT id, username, role, active
        FROM app_users
        WHERE employee_id = ?
        ORDER BY id
    """, (employee_id,))

    for _, user_row in linked_users.iterrows():
        user_id = int(user_row["id"])
        username = str(user_row["username"])
        role = str(user_row["role"])
        active = int(user_row["active"] or 0)

        if user_id == current_user_id:
            result["skipped"] += 1
            result["messages"].append(f"Skipped linked user {username}: cannot delete the account currently logged in.")
            continue

        if role == "admin" and active == 1:
            admin_count_df = df_query("SELECT COUNT(*) AS 'count' FROM app_users WHERE role = 'admin' AND active = 1")
            active_admin_count = int(admin_count_df.iloc[0]["count"]) if not admin_count_df.empty else 0
            if active_admin_count <= 1:
                result["skipped"] += 1
                result["messages"].append(f"Skipped linked user {username}: cannot delete the last active admin account.")
                continue

        try:
            execute("DELETE FROM app_users WHERE id = ?", (user_id,))
            result["deleted_users"] += 1
            result["messages"].append(f"Deleted linked user login: {username}")
        except Exception as e:
            result["skipped"] += 1
            result["messages"].append(f"Could not delete linked user {username}: {e}")

    # If a protected linked user remains, do not fully delete the employee.
    remaining_users = df_query("SELECT COUNT(*) AS 'count' FROM app_users WHERE employee_id = ?", (employee_id,))
    remaining_user_count = int(remaining_users.iloc[0]["count"]) if not remaining_users.empty else 0

    if remaining_user_count > 0:
        try:
            execute("UPDATE employees SET status = 'Inactive' WHERE id = ?", (employee_id,))
            result["deactivated_employee"] += 1
            result["messages"].append(f"Marked {employee_name} inactive because a protected linked user account remains.")
        except Exception as e:
            result["skipped"] += 1
            result["messages"].append(f"Could not deactivate {employee_name}: {e}")
        return result

    history = employee_has_job_history(employee_id)

    if history:
        try:
            execute("UPDATE employees SET status = 'Inactive' WHERE id = ?", (employee_id,))
            result["deactivated_employee"] += 1
            result["messages"].append(
                f"Deleted linked login(s), but marked {employee_name} inactive because they have: " + ", ".join(history)
            )
        except Exception as e:
            result["skipped"] += 1
            result["messages"].append(f"Could not deactivate {employee_name}: {e}")
    else:
        try:
            execute("DELETE FROM employees WHERE id = ?", (employee_id,))
            result["deleted_employee"] += 1
            result["messages"].append(f"Deleted employee record: {employee_name}")
        except Exception as e:
            try:
                execute("UPDATE employees SET status = 'Inactive' WHERE id = ?", (employee_id,))
                result["deactivated_employee"] += 1
                result["messages"].append(f"Could not fully delete {employee_name}, so marked inactive instead. Reason: {e}")
            except Exception:
                result["skipped"] += 1
                result["messages"].append(f"Could not delete or deactivate {employee_name}: {e}")

    return result


def delete_user_and_linked_employee(user_id):
    """
    User delete button behaviour:
    - Deletes the app user login account.
    - If linked to an employee, also deletes that employee if there is no wage/timesheet history.
    - If history exists, the employee is marked Inactive.
    - Protects current logged-in user and last active admin.
    """
    result = {
        "deleted_users": 0,
        "deleted_employee": 0,
        "deactivated_employee": 0,
        "skipped": 0,
        "messages": [],
    }

    try:
        user_id = int(user_id)
    except Exception:
        result["skipped"] += 1
        result["messages"].append("Invalid user id.")
        return result

    user_df = df_query("""
        SELECT id, username, role, employee_id, active
        FROM app_users
        WHERE id = ?
        LIMIT 1
    """, (user_id,))

    if user_df.empty:
        result["skipped"] += 1
        result["messages"].append(f"User id {user_id} not found.")
        return result

    user_row = user_df.iloc[0]
    username = str(user_row["username"])
    role = str(user_row["role"])
    active = int(user_row["active"] or 0)

    try:
        employee_id = int(user_row["employee_id"]) if user_row["employee_id"] not in [None, "", "None"] and pd.notna(user_row["employee_id"]) else None
    except Exception:
        employee_id = None

    current_user = get_current_user() or {}
    try:
        current_user_id = int(current_user.get("id", -1))
    except Exception:
        current_user_id = -1

    if user_id == current_user_id:
        result["skipped"] += 1
        result["messages"].append(f"Skipped {username}: cannot delete the account currently logged in.")
        return result

    if role == "admin" and active == 1:
        admin_count_df = df_query("SELECT COUNT(*) AS 'count' FROM app_users WHERE role = 'admin' AND active = 1")
        active_admin_count = int(admin_count_df.iloc[0]["count"]) if not admin_count_df.empty else 0
        if active_admin_count <= 1:
            result["skipped"] += 1
            result["messages"].append(f"Skipped {username}: cannot delete the last active admin account.")
            return result

    try:
        execute("DELETE FROM app_users WHERE id = ?", (user_id,))
        result["deleted_users"] += 1
        result["messages"].append(f"Deleted user login: {username}")
    except Exception as e:
        result["skipped"] += 1
        result["messages"].append(f"Could not delete user {username}: {e}")
        return result

    if not employee_id:
        return result

    emp_df = df_query("SELECT id, name, status FROM employees WHERE id = ? LIMIT 1", (employee_id,))
    if emp_df.empty:
        result["messages"].append("Linked employee record was not found.")
        return result

    employee_name = str(emp_df.iloc[0]["name"])

    # If other user accounts still link to this employee, do not fully delete employee.
    other_users = df_query("SELECT COUNT(*) AS 'count' FROM app_users WHERE employee_id = ?", (employee_id,))
    other_user_count = int(other_users.iloc[0]["count"]) if not other_users.empty else 0

    if other_user_count > 0:
        try:
            execute("UPDATE employees SET status = 'Inactive' WHERE id = ?", (employee_id,))
            result["deactivated_employee"] += 1
            result["messages"].append(f"Marked linked employee {employee_name} inactive because another login still references them.")
        except Exception as e:
            result["skipped"] += 1
            result["messages"].append(f"Could not deactivate linked employee {employee_name}: {e}")
        return result

    history = employee_has_job_history(employee_id)

    if history:
        try:
            execute("UPDATE employees SET status = 'Inactive' WHERE id = ?", (employee_id,))
            result["deactivated_employee"] += 1
            result["messages"].append(f"Marked linked employee {employee_name} inactive because they have: " + ", ".join(history))
        except Exception as e:
            result["skipped"] += 1
            result["messages"].append(f"Could not deactivate linked employee {employee_name}: {e}")
    else:
        try:
            execute("DELETE FROM employees WHERE id = ?", (employee_id,))
            result["deleted_employee"] += 1
            result["messages"].append(f"Deleted linked employee record: {employee_name}")
        except Exception as e:
            try:
                execute("UPDATE employees SET status = 'Inactive' WHERE id = ?", (employee_id,))
                result["deactivated_employee"] += 1
                result["messages"].append(f"Could not fully delete linked employee {employee_name}, so marked inactive instead. Reason: {e}")
            except Exception:
                result["skipped"] += 1
                result["messages"].append(f"Could not delete or deactivate linked employee {employee_name}: {e}")

    return result


def delete_or_deactivate_selected_employees(employee_ids):
    """
    Bulk employee delete:
    Deletes linked user login(s) too. If the employee has job history,
    the login is deleted and the employee is marked Inactive.
    """
    combined = {
        "deleted_users": 0,
        "deleted_employee": 0,
        "deactivated_employee": 0,
        "skipped": 0,
        "messages": [],
    }

    if not employee_ids:
        combined["messages"].append("No employees selected.")
        return combined

    for emp_id in employee_ids:
        result = delete_employee_and_linked_users(emp_id)
        for key in ["deleted_users", "deleted_employee", "deactivated_employee", "skipped"]:
            combined[key] += result.get(key, 0)
        combined["messages"].extend(result.get("messages", []))

    return combined


def delete_selected_user_accounts(user_ids):
    """
    Bulk user delete:
    Deletes selected user login(s) and linked employee record(s) where safe.
    If linked employee has job history, employee is marked Inactive.
    """
    combined = {
        "deleted_users": 0,
        "deleted_employee": 0,
        "deactivated_employee": 0,
        "skipped": 0,
        "messages": [],
    }

    if not user_ids:
        combined["messages"].append("No user accounts selected.")
        return combined

    for uid in user_ids:
        result = delete_user_and_linked_employee(uid)
        for key in ["deleted_users", "deleted_employee", "deactivated_employee", "skipped"]:
            combined[key] += result.get(key, 0)
        combined["messages"].extend(result.get("messages", []))

    return combined



# =============================
# JOB COSTS / FORECASTING + JOBHUB AI
# =============================
def jc_float(value, default=0.0):
    try:
        if value is None or value == "" or pd.isna(value):
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def jc_percent(numerator, denominator):
    denominator = jc_float(denominator)
    if denominator == 0:
        return 0.0
    return round((jc_float(numerator) / denominator) * 100, 2)


def jc_parse_date(value):
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()[:10]
    for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"]:
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            pass
    return None


def jc_business_days(start_value, end_value):
    start = jc_parse_date(start_value)
    end = jc_parse_date(end_value)
    if not start or not end or end < start:
        return 0
    days = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days


def jc_add_business_days(start_date, days):
    current = start_date or date.today()
    added = 0
    days = int(max(days, 0))
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def jc_month_label(value):
    d = jc_parse_date(value)
    return d.strftime("%Y-%m") if d else "Unscheduled"


def job_cost_summary_dataframe():
    jobs = df_query("""
        SELECT j.id AS 'job_id',
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               COALESCE(bc.name, '') AS 'Builder / Client',
               j.site_address AS 'Site Address',
               j.status AS 'Status',
               j.leading_hand AS 'Leading Hand',
               j.start_date AS 'Start Date',
               j.end_date AS 'End Date',
               COALESCE(j.contract_value, 0) AS 'Contract Value',
               j.notes AS 'Notes'
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        ORDER BY j.job_no
    """)

    if jobs.empty:
        return jobs

    materials = df_query("""
        SELECT m.job_id,
               COALESCE(SUM(COALESCE(m.qty_required, 0) * COALESCE(p.price_ex_gst, 0)), 0) AS 'Actual Material Cost',
               COALESCE(SUM(COALESCE(m.qty_required, 0)), 0) AS 'Material Qty Required',
               COALESCE(SUM(COALESCE(m.qty_received, 0)), 0) AS 'Material Qty Received',
               COUNT(*) AS 'Material Lines'
        FROM material_entries m
        LEFT JOIN products p ON p.id = m.product_id
        GROUP BY m.job_id
    """)

    wages = df_query("""
        SELECT w.job_id,
               COALESCE(SUM(COALESCE(w.hours, 0)), 0) AS 'Wage Hours',
               COALESCE(SUM(COALESCE(w.hours, 0) * COALESCE(e.rate_plus_10, e.base_hourly_rate, 0)), 0) AS 'Actual Labour Cost',
               COUNT(*) AS 'Wage Lines'
        FROM wage_entries w
        LEFT JOIN employees e ON e.id = w.employee_id
        GROUP BY w.job_id
    """)

    timesheets = df_query("""
        SELECT job_id,
               COALESCE(SUM(COALESCE(total_hours, 0)), 0) AS 'Timesheet Hours',
               COUNT(*) AS 'Timesheet Lines'
        FROM timesheet_entries
        GROUP BY job_id
    """)

    estimates = df_query("""
        SELECT e.job_id,
               e.estimate_no AS 'Latest Estimate',
               e.revision AS 'Estimate Revision',
               COALESCE(e.labour_hours, 0) AS 'Estimated Labour Hours',
               COALESCE(e.labour_rate, 0) AS 'Estimated Labour Rate',
               COALESCE(e.material_allowance, 0) AS 'Estimated Materials',
               COALESCE(e.access_equipment_allowance, 0) AS 'Estimated Access / Equipment',
               COALESCE(e.subcontractor_allowance, 0) AS 'Estimated Subcontractor',
               COALESCE(e.sundries_allowance, 0) AS 'Estimated Sundries',
               COALESCE(e.total_ex_gst, 0) AS 'Estimate Total Ex GST',
               COALESCE(e.total_inc_gst, 0) AS 'Estimate Total Inc GST'
        FROM estimate_working_sheets e
        JOIN (
            SELECT job_id, MAX(id) AS max_id
            FROM estimate_working_sheets
            GROUP BY job_id
        ) latest ON latest.max_id = e.id
    """)

    df = jobs.copy()
    for extra in [materials, wages, timesheets, estimates]:
        if extra is not None and not extra.empty:
            df = df.merge(extra, on="job_id", how="left")

    number_cols = [
        "Contract Value", "Actual Material Cost", "Material Qty Required", "Material Qty Received",
        "Material Lines", "Wage Hours", "Actual Labour Cost", "Wage Lines", "Timesheet Hours",
        "Timesheet Lines", "Estimated Labour Hours", "Estimated Labour Rate", "Estimated Materials",
        "Estimated Access / Equipment", "Estimated Subcontractor", "Estimated Sundries",
        "Estimate Total Ex GST", "Estimate Total Inc GST"
    ]

    for col in number_cols:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = df[col].fillna(0)

    for col in ["Latest Estimate", "Estimate Revision"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("")

    df["Actual Labour Hours"] = df["Wage Hours"]
    df["Total Actual Cost"] = df["Actual Material Cost"] + df["Actual Labour Cost"]
    df["Gross Profit"] = df["Contract Value"] - df["Total Actual Cost"]
    df["Gross Profit %"] = df.apply(lambda r: jc_percent(r["Gross Profit"], r["Contract Value"]), axis=1)
    df["Cost to Date %"] = df.apply(lambda r: jc_percent(r["Total Actual Cost"], r["Contract Value"]), axis=1)
    df["Remaining Labour Hours"] = (df["Estimated Labour Hours"] - df["Timesheet Hours"]).clip(lower=0)
    df["Working Days Scheduled"] = df.apply(lambda r: jc_business_days(r["Start Date"], r["End Date"]), axis=1)
    df["Forecast Month"] = df["Start Date"].apply(jc_month_label)
    return df


def job_costs_forecasting_page():
    st.header("Job Costs / Forecasting")
    st.caption("Job cost breakdowns, financial forecasting and labour/schedule forecasting.")

    df = job_cost_summary_dataframe()
    if df.empty:
        st.info("No jobs found yet.")
        return

    section = st.radio(
        "Section",
        ["Selected Job Breakdown", "Financial Forecast", "Scheduling Forecast", "Export"],
        horizontal=True,
        key="job_cost_forecast_section",
    )

    if section == "Selected Job Breakdown":
        job_options = {f"{r['Job No']} - {r['Job Name']}": int(r["job_id"]) for _, r in df.iterrows()}
        selected = st.selectbox("Select Job", list(job_options.keys()), key="job_cost_selected")
        row = df[df["job_id"].astype(int) == int(job_options[selected])].iloc[0]

        st.subheader(f"{row['Job No']} - {row['Job Name']}")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Contract Value", f"${jc_float(row['Contract Value']):,.2f}")
        c2.metric("Actual Cost to Date", f"${jc_float(row['Total Actual Cost']):,.2f}")
        c3.metric("Gross Profit", f"${jc_float(row['Gross Profit']):,.2f}")
        c4.metric("Gross Profit %", f"{jc_float(row['Gross Profit %']):.2f}%")

        c5, c6, c7, c8 = st.columns(4)
        c5.metric("Material Cost", f"${jc_float(row['Actual Material Cost']):,.2f}")
        c6.metric("Labour Cost", f"${jc_float(row['Actual Labour Cost']):,.2f}")
        c7.metric("Timesheet Hours", f"{jc_float(row['Timesheet Hours']):.2f}")
        c8.metric("Remaining Est. Hours", f"{jc_float(row['Remaining Labour Hours']):.2f}")

        st.markdown("### Forecast Inputs")
        i1, i2, i3, i4 = st.columns(4)
        target_gp = i1.number_input("Target GP %", min_value=0.0, max_value=100.0, value=35.0, step=1.0)
        labour_cost_hour = i2.number_input("Labour Cost / Hour", min_value=0.0, value=120.0, step=5.0)
        crew_size = i3.number_input("Crew Size", min_value=1.0, value=3.0, step=1.0)
        hours_day = i4.number_input("Hours / Person / Day", min_value=1.0, value=7.5, step=0.5)

        target_cost = jc_float(row["Contract Value"]) * (1 - target_gp / 100)
        remaining_cost_budget = max(target_cost - jc_float(row["Total Actual Cost"]), 0)
        remaining_by_budget = remaining_cost_budget / labour_cost_hour if labour_cost_hour else 0
        remaining_hours = jc_float(row["Remaining Labour Hours"]) or remaining_by_budget
        daily_capacity = crew_size * hours_day
        days_required = int((remaining_hours + daily_capacity - 0.001) // daily_capacity) if daily_capacity else 0
        if daily_capacity and remaining_hours % daily_capacity:
            days_required += 1
        finish_date = jc_add_business_days(date.today(), days_required)

        forecast_cost = jc_float(row["Total Actual Cost"]) + remaining_hours * labour_cost_hour
        forecast_profit = jc_float(row["Contract Value"]) - forecast_cost
        forecast_gp = jc_percent(forecast_profit, row["Contract Value"])

        f1, f2, f3, f4 = st.columns(4)
        f1.metric("Remaining Cost Budget", f"${remaining_cost_budget:,.2f}")
        f2.metric("Forecast Remaining Hours", f"{remaining_hours:,.2f}")
        f3.metric("Forecast Finish", str(finish_date))
        f4.metric("Forecast GP %", f"{forecast_gp:.2f}%")

        if forecast_gp < target_gp:
            st.warning("Forecast is below target. Check labour, materials, scope changes and variations.")
        else:
            st.success("Forecast is at or above target based on these inputs.")

        detail_cols = [
            "Job No", "Job Name", "Builder / Client", "Status", "Leading Hand", "Start Date", "End Date",
            "Contract Value", "Actual Material Cost", "Actual Labour Cost", "Total Actual Cost",
            "Gross Profit", "Gross Profit %", "Estimate Total Ex GST", "Estimated Labour Hours",
            "Timesheet Hours", "Remaining Labour Hours", "Working Days Scheduled"
        ]
        st.dataframe(pd.DataFrame([row[detail_cols]]), width="stretch", hide_index=True)

    elif section == "Financial Forecast":
        st.subheader("Financial Forecast by Job")
        statuses = ["All"] + sorted([str(x) for x in df["Status"].fillna("").unique() if str(x).strip()])
        selected_status = st.selectbox("Status Filter", statuses)
        filtered = df.copy()
        if selected_status != "All":
            filtered = filtered[filtered["Status"].astype(str) == selected_status]

        total_contract = jc_float(filtered["Contract Value"].sum()) if not filtered.empty else 0
        total_cost = jc_float(filtered["Total Actual Cost"].sum()) if not filtered.empty else 0
        total_profit = total_contract - total_cost
        total_gp = jc_percent(total_profit, total_contract)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Contract Value", f"${total_contract:,.2f}")
        c2.metric("Cost to Date", f"${total_cost:,.2f}")
        c3.metric("Gross Profit", f"${total_profit:,.2f}")
        c4.metric("Gross Profit %", f"{total_gp:.2f}%")

        show_cols = [
            "Job No", "Job Name", "Builder / Client", "Status", "Start Date", "End Date",
            "Contract Value", "Total Actual Cost", "Gross Profit", "Gross Profit %",
            "Actual Material Cost", "Actual Labour Cost", "Timesheet Hours", "Estimate Total Ex GST"
        ]
        st.dataframe(filtered[[c for c in show_cols if c in filtered.columns]], width="stretch", hide_index=True)

        monthly = filtered.groupby("Forecast Month", dropna=False).agg({
            "Contract Value": "sum",
            "Total Actual Cost": "sum",
            "Gross Profit": "sum",
            "Timesheet Hours": "sum",
        }).reset_index()
        if not monthly.empty:
            monthly["Gross Profit %"] = monthly.apply(lambda r: jc_percent(r["Gross Profit"], r["Contract Value"]), axis=1)
            st.markdown("### Forecast by Month")
            st.dataframe(monthly, width="stretch", hide_index=True)

    elif section == "Scheduling Forecast":
        st.subheader("Scheduling / Labour Forecast")
        hours_day = st.number_input("Default Hours / Person / Day", min_value=1.0, value=7.5, step=0.5)
        sched = df.copy()
        sched["Budget Labour Hours"] = sched["Estimated Labour Hours"]
        sched["Budget Labour Hours"] = sched.apply(
            lambda r: jc_float(r["Budget Labour Hours"]) if jc_float(r["Budget Labour Hours"]) > 0 else jc_float(r["Contract Value"]) / 120,
            axis=1,
        )
        sched["Remaining Hours"] = (sched["Budget Labour Hours"] - sched["Timesheet Hours"]).clip(lower=0)
        sched["Remaining Painter Days"] = (sched["Remaining Hours"] / hours_day).round(2)
        sched["Required Painters"] = sched.apply(
            lambda r: round(jc_float(r["Budget Labour Hours"]) / (jc_float(r["Working Days Scheduled"]) * hours_day), 2)
            if jc_float(r["Working Days Scheduled"]) > 0 else 0,
            axis=1,
        )

        c1, c2, c3 = st.columns(3)
        c1.metric("Remaining Labour Hours", f"{jc_float(sched['Remaining Hours'].sum()):,.2f}")
        c2.metric("Remaining Painter Days", f"{jc_float(sched['Remaining Painter Days'].sum()):,.2f}")
        c3.metric("Jobs in Forecast", len(sched))

        cols = [
            "Job No", "Job Name", "Status", "Leading Hand", "Start Date", "End Date",
            "Working Days Scheduled", "Budget Labour Hours", "Timesheet Hours",
            "Remaining Hours", "Required Painters", "Remaining Painter Days"
        ]
        st.dataframe(sched[[c for c in cols if c in sched.columns]], width="stretch", hide_index=True)

    else:
        st.subheader("Export Job Cost / Forecast Data")
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.drop(columns=["job_id"], errors="ignore").to_excel(writer, index=False, sheet_name="Job Forecast")
            monthly = df.groupby("Forecast Month", dropna=False).agg({
                "Contract Value": "sum",
                "Total Actual Cost": "sum",
                "Gross Profit": "sum",
                "Timesheet Hours": "sum",
            }).reset_index()
            if not monthly.empty:
                monthly["Gross Profit %"] = monthly.apply(lambda r: jc_percent(r["Gross Profit"], r["Contract Value"]), axis=1)
            monthly.to_excel(writer, index=False, sheet_name="Monthly Forecast")
            for ws in writer.book.worksheets:
                for column_cells in ws.columns:
                    max_len = 0
                    col_letter = column_cells[0].column_letter
                    for cell in column_cells:
                        value = "" if cell.value is None else str(cell.value)
                        max_len = max(max_len, len(value))
                    ws.column_dimensions[col_letter].width = min(max(max_len + 2, 12), 45)
        output.seek(0)
        st.download_button(
            "Download Job Cost / Forecast Excel",
            data=output.getvalue(),
            file_name="PB_JobHub_Job_Cost_Forecast.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def jobhub_ai_api_key():
    try:
        if "OPENAI_API_KEY" in st.secrets:
            return st.secrets["OPENAI_API_KEY"]
    except Exception:
        pass
    return os.environ.get("OPENAI_API_KEY", "")


def jobhub_ai_model():
    try:
        if "OPENAI_MODEL" in st.secrets:
            return st.secrets["OPENAI_MODEL"]
    except Exception:
        pass
    return os.environ.get("OPENAI_MODEL", "gpt-5.5")


def jobhub_ai_context(selected_job_id=None):
    df = job_cost_summary_dataframe()
    lines = []

    if selected_job_id and not df.empty:
        selected = df[df["job_id"].astype(int) == int(selected_job_id)]
        if not selected.empty:
            r = selected.iloc[0]
            lines.append("SELECTED JOB SUMMARY")
            for col in [
                "Job No", "Job Name", "Builder / Client", "Status", "Leading Hand", "Start Date", "End Date",
                "Contract Value", "Actual Material Cost", "Actual Labour Cost", "Total Actual Cost",
                "Gross Profit", "Gross Profit %", "Timesheet Hours", "Estimated Labour Hours", "Remaining Labour Hours"
            ]:
                if col in selected.columns:
                    lines.append(f"{col}: {r.get(col, '')}")

            materials = df_query("""
                SELECT p.product_code AS 'Product Code',
                       p.product_name AS 'Product Name',
                       m.qty_required AS 'Qty Required',
                       m.qty_received AS 'Qty Received',
                       p.price_ex_gst AS 'Unit Price',
                       ROUND(COALESCE(m.qty_required, 0) * COALESCE(p.price_ex_gst, 0), 2) AS 'Line Cost',
                       m.notes AS 'Notes'
                FROM material_entries m
                LEFT JOIN products p ON p.id = m.product_id
                WHERE m.job_id = ?
                ORDER BY m.id DESC
                LIMIT 50
            """, (selected_job_id,))
            if not materials.empty:
                lines.append("\nMATERIALS")
                lines.append(materials.to_csv(index=False))

            timesheets = df_query("""
                SELECT e.name AS 'Employee',
                       t.work_date AS 'Date',
                       t.total_hours AS 'Hours',
                       t.work_type AS 'Work Type',
                       t.status AS 'Status',
                       t.notes AS 'Notes'
                FROM timesheet_entries t
                LEFT JOIN employees e ON e.id = t.employee_id
                WHERE t.job_id = ?
                ORDER BY t.work_date DESC
                LIMIT 50
            """, (selected_job_id,))
            if not timesheets.empty:
                lines.append("\nTIMESHEETS")
                lines.append(timesheets.to_csv(index=False))
    else:
        if not df.empty:
            overview_cols = [
                "Job No", "Job Name", "Status", "Start Date", "End Date", "Contract Value",
                "Total Actual Cost", "Gross Profit", "Gross Profit %", "Timesheet Hours"
            ]
            lines.append("ALL JOBS OVERVIEW")
            lines.append(df[[c for c in overview_cols if c in df.columns]].head(60).to_csv(index=False))

    return "\n".join(lines)[:18000]


def jobhub_ai_answer(question, context_text):
    api_key = jobhub_ai_api_key()
    if not api_key:
        return None, "OPENAI_API_KEY is missing. Add it in Streamlit Secrets first."

    payload = {
        "model": jobhub_ai_model(),
        "input": (
            "You are JobHub AI for Premier Brushworks, a painting and decorating business. "
            "Use only the JobHub context provided. Give practical, direct advice for quoting, job costs, scheduling, materials, "
            "staffing, risks and next actions. If data is missing, say what is missing. Do not invent details.\n\n"
            "JOBHUB CONTEXT:\n" + context_text + "\n\nUSER QUESTION:\n" + str(question)
        ),
    }

    try:
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        if response.status_code >= 400:
            return None, f"OpenAI API error {response.status_code}: {response.text[:1000]}"

        data = response.json()
        if data.get("output_text"):
            return data["output_text"], None

        parts = []
        for item in data.get("output", []) or []:
            for content in item.get("content", []) or []:
                if isinstance(content, dict) and content.get("text"):
                    parts.append(str(content["text"]))

        return "\n".join(parts) if parts else json.dumps(data)[:3000], None
    except Exception as e:
        return None, f"OpenAI request failed: {e}"


def jobhub_ai_assistant_page():
    st.header("JobHub AI Assistant")
    st.caption("Ask an AI assistant about your JobHub data, job costs, quotes, scheduling and risks.")

    if not jobhub_ai_api_key():
        st.warning("OPENAI_API_KEY is not set yet. Add it in Streamlit Secrets.")
        st.code('OPENAI_API_KEY = "sk-..."\nOPENAI_MODEL = "gpt-5.5"', language="toml")
        return

    job_options = get_job_options()
    mode = st.radio("Context", ["All Jobs Overview", "Selected Job"], horizontal=True, key="ai_context_mode")
    selected_job_id = None

    if mode == "Selected Job":
        if not job_options:
            st.info("Create a job first.")
            return
        selected_job = st.selectbox("Select Job", list(job_options.keys()), key="ai_selected_job")
        selected_job_id = job_options[selected_job]

    quick = st.selectbox(
        "Quick Question",
        [
            "Custom",
            "Which jobs are at risk of running over budget?",
            "What should I check before quoting this job?",
            "How many painters do I need to finish this job on time?",
            "What materials or timesheets look unusual?",
            "Give me a director-level summary for this week.",
        ],
        key="ai_quick_question",
    )
    default_question = "" if quick == "Custom" else quick

    question = st.text_area(
        "Ask JobHub AI",
        value=default_question,
        height=120,
        placeholder="Example: Review this job and tell me the margin risk, labour pressure and next actions.",
        key="ai_question",
    )

    context_text = jobhub_ai_context(selected_job_id)
    if st.checkbox("Show data being sent to AI", value=False, key="ai_show_context"):
        st.text_area("Context Preview", value=context_text, height=300)

    if st.button("Ask JobHub AI", key="ask_jobhub_ai"):
        if not question.strip():
            st.error("Enter a question first.")
        else:
            with st.spinner("JobHub AI is reviewing your data..."):
                answer, error = jobhub_ai_answer(question, context_text)
            if error:
                st.error(error)
            else:
                st.markdown("### Answer")
                st.write(answer)



# =============================
# APP BUILDER AI
# =============================
def app_builder_read_file(path, max_chars=12000):
    try:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return ""
        text = p.read_text(encoding="utf-8", errors="ignore")
        if len(text) > max_chars:
            return text[:max_chars] + f"\n\n...[trimmed after {max_chars} characters]..."
        return text
    except Exception as e:
        return f"Could not read {path}: {e}"


def app_builder_file_tree():
    allowed = []
    try:
        root = Path(".")
        for p in root.rglob("*"):
            if p.is_file():
                name = str(p).replace("\\", "/")
                if "__pycache__" in name or ".git" in name or "pb_jobhub.db" in name or "secrets.toml" in name:
                    continue
                if name.endswith((".py", ".txt", ".toml", ".sql")):
                    allowed.append(name)
    except Exception:
        allowed = ["pb_jobhub_app.py", "requirements.txt", "SUPABASE_SCHEMA_MANUAL_BACKUP.sql"]
    return sorted(allowed)[:80]


def app_builder_relevant_code_snippets(question, max_snippets=8, chars_per_snippet=1800):
    """
    Pulls relevant sections from pb_jobhub_app.py without sending the full app every time.
    """
    source = app_builder_read_file("pb_jobhub_app.py", max_chars=400000)
    if not source:
        return ""

    terms = []
    for raw in re.findall(r"[A-Za-z_]{4,}", str(question).lower()):
        if raw not in ["this", "that", "with", "from", "your", "have", "will", "make", "need", "want", "please"]:
            terms.append(raw)

    priority_terms = [
        "streamlit", "supabase", "postgres", "connect", "df_query", "execute", "job", "employee",
        "timesheet", "estimate", "material", "product", "user", "login", "forecast", "ai", "openai"
    ]
    terms = list(dict.fromkeys(terms + priority_terms))

    snippets = []
    lines = source.splitlines()
    lower_lines = [l.lower() for l in lines]

    matched_indexes = []
    for i, line in enumerate(lower_lines):
        if any(t in line for t in terms):
            matched_indexes.append(i)

    # group nearby line matches
    used = set()
    for idx in matched_indexes:
        if len(snippets) >= max_snippets:
            break
        start = max(idx - 20, 0)
        end = min(idx + 60, len(lines))
        key = (start // 40, end // 40)
        if key in used:
            continue
        used.add(key)
        snippet = "\n".join(lines[start:end])
        if len(snippet) > chars_per_snippet:
            snippet = snippet[:chars_per_snippet] + "\n...[snippet trimmed]..."
        snippets.append(f"--- pb_jobhub_app.py lines approx {start+1}-{end} ---\n{snippet}")

    return "\n\n".join(snippets)


def app_builder_notes_context(limit=20):
    try:
        notes = df_query("""
            SELECT topic AS 'Topic', note AS 'Note', source AS 'Source', created_at AS 'Created'
            FROM app_builder_notes
            ORDER BY id DESC
            LIMIT ?
        """, (limit,))
        if notes.empty:
            return ""
        return notes.to_csv(index=False)
    except Exception:
        return ""


def save_app_builder_note(topic, note, source="Manual / AI"):
    execute("""
        INSERT INTO app_builder_notes (topic, note, source, created_at)
        VALUES (?, ?, ?, ?)
    """, (topic, note, source, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))


def app_builder_ai_call(question, include_web=False, require_web=False, selected_mode="Code Helper"):
    api_key = jobhub_ai_api_key()
    if not api_key:
        return None, "OPENAI_API_KEY is missing. Add it in Streamlit Secrets first."

    file_tree = "\n".join(app_builder_file_tree())
    reqs = app_builder_read_file("requirements.txt", max_chars=6000)
    schema = app_builder_read_file("SUPABASE_SCHEMA_MANUAL_BACKUP.sql", max_chars=12000)
    snippets = app_builder_relevant_code_snippets(question)
    saved_notes = app_builder_notes_context()

    system_prompt = f"""
You are App Builder AI inside Premier Brushworks JobHub.
You help the owner improve and maintain this Streamlit + Supabase business app.

Rules:
- Be practical and direct.
- Help design features, find likely bugs, improve speed, improve database structure, and plan safe changes.
- If asked to change the app, provide a clear build plan and exact code/pseudocode sections.
- Do NOT pretend you have already changed GitHub or deployed the app.
- Do NOT expose or ask for secrets.
- If live web research is enabled, use current public documentation and include citations in the answer where available.
- If something is risky, say so and suggest the safest next step.
- This AI can learn by saving notes in app_builder_notes. It does not retrain model weights.
Mode: {selected_mode}
"""

    context = f"""
APP FILE TREE:
{file_tree}

REQUIREMENTS:
{reqs}

DATABASE SCHEMA EXCERPT:
{schema}

RELEVANT CURRENT APP CODE SNIPPETS:
{snippets}

SAVED APP BUILDER LEARNINGS:
{saved_notes}
"""

    payload = {
        "model": jobhub_ai_model(),
        "input": system_prompt + "\n\n" + context + "\n\nUSER REQUEST:\n" + str(question),
    }

    if include_web:
        payload["tools"] = [{"type": "web_search"}]
        payload["tool_choice"] = "required" if require_web else "auto"

    try:
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=90,
        )
        if response.status_code >= 400:
            return None, f"OpenAI API error {response.status_code}: {response.text[:1200]}"

        data = response.json()

        if data.get("output_text"):
            return data["output_text"], None

        parts = []
        for item in data.get("output", []) or []:
            for content in item.get("content", []) or []:
                if isinstance(content, dict):
                    if content.get("text"):
                        parts.append(str(content["text"]))
                    elif content.get("type") == "output_text" and content.get("text"):
                        parts.append(str(content["text"]))

        return "\n".join(parts) if parts else json.dumps(data)[:4000], None
    except Exception as e:
        return None, f"OpenAI request failed: {e}"


def app_builder_ai_page():
    st.header("App Builder AI")
    st.caption("Use this to help build, improve and research the JobHub app. It can use live internet research and save learnings for later.")

    if not jobhub_ai_api_key():
        st.warning("OPENAI_API_KEY is not set yet. Add it in Streamlit Secrets.")
        st.code('OPENAI_API_KEY = "sk-..."\nOPENAI_MODEL = "gpt-5.5"', language="toml")
        return

    section = st.radio(
        "Section",
        ["Build / Fix the App", "Self-Edit Code", "Internet Learning", "Saved Learnings"],
        horizontal=True,
        key="app_builder_section",
    )

    if section == "Build / Fix the App":
        st.subheader("Build / Fix the App")
        mode = st.selectbox(
            "Mode",
            ["Code Helper", "Bug Fixer", "Feature Planner", "Speed Optimiser", "Database / Supabase Helper", "Streamlit UI Helper"],
            key="app_builder_mode",
        )

        include_web = st.checkbox("Allow live internet research", value=True, key="app_builder_include_web")
        require_web = st.checkbox("Force web search for this request", value=False, key="app_builder_require_web")

        quick = st.selectbox(
            "Quick request",
            [
                "Custom",
                "Review this app and suggest the next 5 improvements",
                "Help me make the app faster",
                "Help me add a new feature safely",
                "Review the latest Streamlit/Supabase/OpenAI docs before answering",
                "Tell me what code files need changing for this feature",
            ],
            key="app_builder_quick",
        )
        default_question = "" if quick == "Custom" else quick

        question = st.text_area(
            "What do you want to build or fix?",
            value=default_question,
            height=150,
            placeholder="Example: Add a daily dashboard showing jobs starting this week, overdue invoices, missing timesheets and jobs at margin risk.",
            key="app_builder_question",
        )

        if st.checkbox("Show app code context being sent", value=False, key="app_builder_show_context"):
            st.markdown("### File tree")
            st.code("\n".join(app_builder_file_tree()))
            st.markdown("### Relevant snippets")
            st.code(app_builder_relevant_code_snippets(question or "jobhub app"))

        if st.button("Ask App Builder AI", key="ask_app_builder_ai"):
            if not question.strip():
                st.error("Enter a build/fix request first.")
            else:
                with st.spinner("App Builder AI is reviewing JobHub..."):
                    answer, error = app_builder_ai_call(
                        question=question,
                        include_web=include_web,
                        require_web=require_web,
                        selected_mode=mode,
                    )

                if error:
                    st.error(error)
                else:
                    st.markdown("### App Builder AI")
                    st.write(answer)

                    with st.expander("Save this as a learning note"):
                        note_topic = st.text_input("Topic", value=question[:80], key="save_ai_learning_topic")
                        note_text = st.text_area("Note to save", value=answer[:4000], height=200, key="save_ai_learning_text")
                        if st.button("Save Learning Note", key="save_ai_learning_button"):
                            save_app_builder_note(note_topic, note_text, source="App Builder AI")
                            st.success("Learning note saved.")

    elif section == "Self-Edit Code":
        app_builder_self_edit_section()

    elif section == "Internet Learning":
        st.subheader("Internet Learning")
        st.caption("Ask the AI to research current docs or ideas online, then save the best findings into JobHub's learning notes.")

        topic = st.text_input(
            "Research topic",
            value="Latest Streamlit performance best practices for database apps",
            key="internet_learning_topic",
        )
        focus = st.text_area(
            "What should it learn?",
            value="Find current practical guidance that would help improve Premier Brushworks JobHub. Keep it focused on Streamlit, Supabase, OpenAI, security, speed and maintainability.",
            height=120,
            key="internet_learning_focus",
        )

        if st.button("Research Internet and Summarise", key="internet_learning_button"):
            prompt = (
                f"Research this topic using current web sources: {topic}\n\n"
                f"Focus: {focus}\n\n"
                "Return practical findings, implementation notes for this app, risks, and links/citations where available. "
                "Then finish with a short 'Save this learning' summary."
            )
            with st.spinner("Researching online..."):
                answer, error = app_builder_ai_call(
                    question=prompt,
                    include_web=True,
                    require_web=True,
                    selected_mode="Internet Researcher",
                )

            if error:
                st.error(error)
            else:
                st.markdown("### Research Summary")
                st.write(answer)

                with st.expander("Save research into JobHub learning notes", expanded=True):
                    note_topic = st.text_input("Topic to save", value=topic, key="save_research_topic")
                    note_text = st.text_area("Research note", value=answer[:6000], height=250, key="save_research_note")
                    if st.button("Save Research Learning", key="save_research_button"):
                        save_app_builder_note(note_topic, note_text, source="Internet Research")
                        st.success("Research learning saved.")

    else:
        st.subheader("Saved Learnings")
        notes = df_query("""
            SELECT id AS 'ID',
                   topic AS 'Topic',
                   source AS 'Source',
                   created_at AS 'Created',
                   note AS 'Note'
            FROM app_builder_notes
            ORDER BY id DESC
        """)

        if notes.empty:
            st.info("No saved learnings yet.")
        else:
            st.dataframe(notes[["ID", "Topic", "Source", "Created"]], width="stretch", hide_index=True)

            note_options = {f"{row['Topic']} | {row['Source']} | ID {row['ID']}": int(row["ID"]) for _, row in notes.iterrows()}
            selected = st.selectbox("Open learning note", list(note_options.keys()), key="open_learning_note")
            selected_id = note_options[selected]
            row = notes[notes["ID"].astype(int) == selected_id].iloc[0]
            st.markdown(f"### {row['Topic']}")
            st.caption(f"{row['Source']} • {row['Created']}")
            st.write(row["Note"])

            col1, col2 = st.columns(2)
            if col1.button("Delete This Learning Note", key="delete_learning_note"):
                execute("DELETE FROM app_builder_notes WHERE id = ?", (selected_id,))
                st.success("Learning note deleted.")
                refresh()

        with st.expander("Add manual learning note"):
            with st.form("manual_learning_note_form"):
                topic = st.text_input("Topic")
                source = st.text_input("Source", value="Manual")
                note = st.text_area("Note", height=180)
                submitted = st.form_submit_button("Save Manual Learning")
                if submitted:
                    if not topic.strip() or not note.strip():
                        st.error("Topic and note are required.")
                    else:
                        save_app_builder_note(topic, note, source=source)
                        st.success("Learning note saved.")
                        refresh()



# =============================
# APP BUILDER SELF-EDIT HELPERS
# =============================
SELF_EDIT_ALLOWED_FILES = {
    "pb_jobhub_app.py",
    "requirements.txt",
    "SUPABASE_SCHEMA_MANUAL_BACKUP.sql",
    ".streamlit/config.toml",
}


def self_edit_safe_path(target_file):
    target_file = str(target_file or "").strip().replace("\\", "/")
    if target_file not in SELF_EDIT_ALLOWED_FILES:
        return None, f"File not allowed for self-edit: {target_file}"

    p = Path(target_file)
    if ".." in p.parts or p.is_absolute():
        return None, "Unsafe file path."

    return p, None


def self_edit_extract_json(text):
    """
    Extracts a JSON array from AI output.
    Expected format:
    [
      {
        "target_file": "pb_jobhub_app.py",
        "find": "exact old text",
        "replace": "new text",
        "reason": "why"
      }
    ]
    """
    raw = str(text or "").strip()

    # Remove markdown fences if present.
    raw = re.sub(r"^```(?:json)?", "", raw.strip(), flags=re.I).strip()
    raw = re.sub(r"```$", "", raw.strip()).strip()

    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "replacements" in data:
            data = data["replacements"]
        return data if isinstance(data, list) else []
    except Exception:
        pass

    # Try to find the first JSON array in text.
    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(raw[start:end+1])
            return data if isinstance(data, list) else []
        except Exception:
            pass

    return []


def self_edit_validate_replacements(replacements):
    issues = []
    if not replacements:
        issues.append("No replacement JSON found.")
        return issues

    for i, item in enumerate(replacements, start=1):
        if not isinstance(item, dict):
            issues.append(f"Replacement {i} is not an object.")
            continue

        target = item.get("target_file", "")
        find = item.get("find", "")
        replace = item.get("replace", "")

        path, error = self_edit_safe_path(target)
        if error:
            issues.append(f"Replacement {i}: {error}")

        if not find:
            issues.append(f"Replacement {i}: find text is empty.")

        if replace is None:
            issues.append(f"Replacement {i}: replace text is missing.")

        if path and path.exists():
            try:
                file_text = path.read_text(encoding="utf-8", errors="ignore")
                if find and find not in file_text:
                    issues.append(f"Replacement {i}: find text was not found in {target}.")
            except Exception as e:
                issues.append(f"Replacement {i}: could not read {target}: {e}")
        elif path:
            issues.append(f"Replacement {i}: target file does not exist: {target}")

    return issues


def self_edit_apply_replacements(replacements):
    """
    Applies exact find/replace patches.
    Creates backups first.
    If pb_jobhub_app.py compile fails, restores the backup.
    """
    result = {
        "applied": 0,
        "backups": [],
        "messages": [],
        "success": False,
    }

    issues = self_edit_validate_replacements(replacements)
    if issues:
        result["messages"].extend(issues)
        return result

    backup_root = Path(tempfile.gettempdir()) / "pb_jobhub_self_edit_backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    touched_files = set()

    try:
        for item in replacements:
            target_file = item["target_file"]
            find = item["find"]
            replace = item["replace"]

            path, error = self_edit_safe_path(target_file)
            if error:
                raise RuntimeError(error)

            if str(path) not in touched_files:
                backup_path = backup_root / f"{path.name}.{stamp}.bak"
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup_path)
                result["backups"].append(str(backup_path))
                touched_files.add(str(path))

            current = path.read_text(encoding="utf-8", errors="ignore")
            updated = current.replace(find, replace, 1)
            path.write_text(updated, encoding="utf-8")
            result["applied"] += 1
            result["messages"].append(f"Applied replacement to {target_file}: {item.get('reason', 'No reason provided')}")

        # Compile check and rollback for Python app file.
        if "pb_jobhub_app.py" in [str(item.get("target_file")) for item in replacements]:
            try:
                py_compile.compile("pb_jobhub_app.py", doraise=True)
                result["messages"].append("Python compile check passed after self-edit.")
            except Exception as compile_error:
                # Restore all backups.
                for backup in result["backups"]:
                    backup_path = Path(backup)
                    original_name = backup_path.name.split(".")[0]
                    if original_name == "pb_jobhub_app":
                        # backup filename is pb_jobhub_app.py.TIMESTAMP.bak
                        shutil.copy2(backup_path, Path("pb_jobhub_app.py"))
                result["messages"].append(f"Compile failed. Restored backup. Error: {compile_error}")
                return result

        result["success"] = True
        return result

    except Exception as e:
        result["messages"].append(f"Self-edit failed: {e}")
        return result


def save_app_code_change(title, request, ai_response, patch_json, target_files, status, result_message=""):
    execute("""
        INSERT INTO app_code_changes
        (title, request, ai_response, patch_json, target_files, status, created_at, applied_at, result_message)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        title,
        request,
        ai_response,
        patch_json,
        target_files,
        status,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S") if status == "Applied" else "",
        result_message,
    ))


def app_builder_self_edit_prompt(user_request):
    current_code = app_builder_relevant_code_snippets(user_request, max_snippets=12, chars_per_snippet=2500)
    file_tree = "\n".join(app_builder_file_tree())

    return f"""
You are App Builder AI for Premier Brushworks JobHub.

The user wants you to alter the app code. You must return ONLY valid JSON. No markdown. No explanation outside JSON.

Return a JSON array of exact text replacements:
[
  {{
    "target_file": "pb_jobhub_app.py",
    "find": "exact existing text to find",
    "replace": "replacement text",
    "reason": "short reason"
  }}
]

Rules:
- Only target these files: pb_jobhub_app.py, requirements.txt, SUPABASE_SCHEMA_MANUAL_BACKUP.sql, .streamlit/config.toml
- Use exact find text from the code context.
- Keep changes small and safe.
- If the request needs a large rebuild, return one small safe first step.
- Do not include secrets.
- Do not include markdown fences.
- Do not invent code locations that are not in context.

FILE TREE:
{file_tree}

RELEVANT CODE:
{current_code}

USER REQUEST:
{user_request}
"""


def app_builder_self_edit_section():
    st.subheader("Controlled Self-Edit")
    st.warning(
        "This lets App Builder AI apply exact code replacements to the running app files. "
        "On Streamlit Cloud, file changes may not permanently survive a redeploy unless you download the changed file and upload it to GitHub."
    )

    st.caption(
        "Safety: only exact text replacements are allowed, only approved files can be changed, "
        "a backup is created, and pb_jobhub_app.py is compile-checked after changes."
    )

    request = st.text_area(
        "What code change should the AI make?",
        height=140,
        placeholder="Example: Add a dashboard card showing jobs with missing timesheets this week.",
        key="self_edit_request",
    )

    if st.checkbox("Show relevant code context", value=False, key="self_edit_show_context"):
        st.code(app_builder_relevant_code_snippets(request or "jobhub app"), language="python")

    if st.button("Generate Self-Edit Patch", key="generate_self_edit_patch"):
        if not request.strip():
            st.error("Enter a code change request first.")
        else:
            prompt = app_builder_self_edit_prompt(request)
            with st.spinner("Generating safe code replacement JSON..."):
                answer, error = jobhub_ai_answer(prompt, "")

            if error:
                st.error(error)
            else:
                st.session_state["self_edit_ai_response"] = answer
                st.session_state["self_edit_request"] = request
                st.success("Patch proposal generated.")

    ai_response = st.session_state.get("self_edit_ai_response", "")
    stored_request = st.session_state.get("self_edit_request", request)

    if ai_response:
        st.markdown("### Proposed Patch JSON")
        st.code(ai_response, language="json")

        replacements = self_edit_extract_json(ai_response)
        issues = self_edit_validate_replacements(replacements)

        if issues:
            st.error("Patch is not ready to apply:")
            for issue in issues:
                st.write(f"- {issue}")
        else:
            st.success(f"Patch validated. {len(replacements)} replacement(s) ready.")
            preview_rows = []
            for i, item in enumerate(replacements, start=1):
                preview_rows.append({
                    "No": i,
                    "Target File": item.get("target_file", ""),
                    "Find Length": len(str(item.get("find", ""))),
                    "Replace Length": len(str(item.get("replace", ""))),
                    "Reason": item.get("reason", ""),
                })
            st.dataframe(pd.DataFrame(preview_rows), width="stretch", hide_index=True)

            confirm = st.text_input(
                "To apply this AI code change to the running app, type: APPLY CODE CHANGE",
                key="self_edit_confirm",
            )

            if st.button("Apply AI Code Change", key="apply_self_edit_patch"):
                if confirm.strip().upper() != "APPLY CODE CHANGE":
                    st.error("Type APPLY CODE CHANGE exactly before applying.")
                else:
                    result = self_edit_apply_replacements(replacements)
                    status = "Applied" if result["success"] else "Failed"
                    save_app_code_change(
                        title=stored_request[:100],
                        request=stored_request,
                        ai_response=ai_response,
                        patch_json=json.dumps(replacements, indent=2),
                        target_files=", ".join(sorted(set(str(x.get("target_file", "")) for x in replacements))),
                        status=status,
                        result_message="\n".join(result["messages"]),
                    )

                    if result["success"]:
                        st.success(f"Applied {result['applied']} code replacement(s).")
                        st.info("Download the changed file below and upload it to GitHub so the change persists after redeploy.")
                    else:
                        st.error("Patch was not applied or was rolled back.")

                    with st.expander("Self-edit result details", expanded=True):
                        for msg in result["messages"]:
                            st.write(msg)

    st.markdown("### Download Current App Files")
    for file_name in ["pb_jobhub_app.py", "requirements.txt", "SUPABASE_SCHEMA_MANUAL_BACKUP.sql"]:
        p, error = self_edit_safe_path(file_name)
        if p and p.exists():
            data = p.read_text(encoding="utf-8", errors="ignore").encode("utf-8")
            st.download_button(
                f"Download {file_name}",
                data=data,
                file_name=file_name,
                mime="text/plain",
                key=f"download_{file_name}",
            )

    st.markdown("### Code Change History")
    try:
        changes = df_query("""
            SELECT id AS 'ID',
                   title AS 'Title',
                   target_files AS 'Target Files',
                   status AS 'Status',
                   created_at AS 'Created',
                   result_message AS 'Result'
            FROM app_code_changes
            ORDER BY id DESC
            LIMIT 50
        """)
        if changes.empty:
            st.info("No code changes saved yet.")
        else:
            st.dataframe(changes, width="stretch", hide_index=True)
    except Exception:
        st.info("Code change history table will be available after the app initializes the database.")



# =============================
# FREE LOCAL AI / OLLAMA OVERRIDES
# =============================
def ai_secret(name, default=""):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name, default)


def ai_provider():
    """
    AI provider rules:
    - AI_PROVIDER=openai: use OpenAI online/cloud.
    - AI_PROVIDER=ollama: use local Ollama only.
    - AI_PROVIDER=auto or blank:
        * if OPENAI_API_KEY exists, use OpenAI
        * if hosted on Render and no OpenAI key, switch AI off
        * if running locally and no OpenAI key, use Ollama
    - AI_PROVIDER=none/off/disabled: switch AI off
    """
    provider = str(ai_secret("AI_PROVIDER", "auto")).strip().lower()

    if provider in ["none", "off", "disabled", "disable", "false", "0", "no", "no_ai", "no-ai"]:
        return "none"

    if provider not in ["ollama", "openai", "auto"]:
        provider = "auto"

    has_openai_key = bool(str(jobhub_ai_api_key() or "").strip())
    is_render = bool(os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID"))

    if provider == "openai":
        return "openai"

    if provider == "ollama":
        return "ollama"

    # auto mode
    if has_openai_key:
        return "openai"

    if is_render:
        return "none"

    return "ollama"


def ai_disabled_message():
    return (
        "AI is switched off on this hosted Render app because no OpenAI API key is configured. "
        "Add AI_PROVIDER=openai and OPENAI_API_KEY in Render Environment to use online AI. "
        "For free Ollama AI, run JobHub locally on the same computer as Ollama."
    )


def ollama_base_url():
    return str(ai_secret("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")


def ollama_model():
    return str(ai_secret("OLLAMA_MODEL", "llama3.2:3b")).strip() or "llama3.2:3b"


def ollama_timeout():
    try:
        return int(ai_secret("OLLAMA_TIMEOUT", "120"))
    except Exception:
        return 120


def openai_enabled():
    return bool(str(jobhub_ai_api_key() or "").strip())


def ollama_status():
    try:
        response = requests.get(f"{ollama_base_url()}/api/tags", timeout=5)
        if response.status_code == 200:
            return True, f"Ollama connected at {ollama_base_url()} using model {ollama_model()}."
        return False, f"Ollama responded with status {response.status_code}. Check Ollama is running."
    except Exception as e:
        return False, f"Ollama not reachable at {ollama_base_url()}. Start Ollama on this computer. Details: {e}"


def ai_backend_ready():
    provider = ai_provider()

    if provider == "none":
        return False, ai_disabled_message()

    if provider == "openai":
        if openai_enabled():
            return True, f"Using OpenAI online model {jobhub_ai_model()}."
        return False, "AI_PROVIDER is openai but OPENAI_API_KEY is missing."

    if provider == "ollama":
        return ollama_status()

    return False, ai_disabled_message()


def ollama_generate(prompt, system="", context="", model=None, timeout=None):
    if ai_provider() == "none":
        return None, ai_disabled_message()

    if ai_provider() == "openai":
        return None, "Ollama is not used in OpenAI mode. Use the JobHub AI Assistant or App Builder AI with OpenAI."

    model = model or ollama_model()
    timeout = timeout or ollama_timeout()

    full_prompt = ""
    if system:
        full_prompt += "SYSTEM:\n" + str(system).strip() + "\n\n"
    if context:
        full_prompt += "CONTEXT:\n" + str(context).strip() + "\n\n"
    full_prompt += "USER:\n" + str(prompt).strip()

    payload = {
        "model": model,
        "prompt": full_prompt,
        "stream": False,
    }

    try:
        response = requests.post(
            f"{ollama_base_url()}/api/generate",
            json=payload,
            timeout=timeout,
        )
        if response.status_code >= 400:
            return None, f"Ollama error {response.status_code}: {response.text[:1000]}"

        data = response.json()
        return data.get("response", "").strip(), None
    except Exception as e:
        return None, f"Ollama request failed: {e}"


def openai_responses_answer(prompt, context_text="", include_web=False, require_web=False, system_text=""):
    api_key = jobhub_ai_api_key()
    if not api_key:
        return None, "OPENAI_API_KEY is missing."

    payload = {
        "model": jobhub_ai_model(),
        "input": (
            (system_text or "You are a helpful assistant for Premier Brushworks JobHub.") +
            "\n\nCONTEXT:\n" + str(context_text or "") +
            "\n\nUSER REQUEST:\n" + str(prompt)
        ),
    }

    if include_web:
        payload["tools"] = [{"type": "web_search"}]
        payload["tool_choice"] = "required" if require_web else "auto"

    try:
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=90,
        )
        if response.status_code >= 400:
            return None, f"OpenAI API error {response.status_code}: {response.text[:1000]}"

        data = response.json()
        if data.get("output_text"):
            return data["output_text"], None

        parts = []
        for item in data.get("output", []) or []:
            for content in item.get("content", []) or []:
                if isinstance(content, dict) and content.get("text"):
                    parts.append(str(content["text"]))

        return "\n".join(parts) if parts else json.dumps(data)[:3000], None
    except Exception as e:
        return None, f"OpenAI request failed: {e}"


def jobhub_ai_answer(question, context_text):
    system = (
        "You are JobHub AI for Premier Brushworks, a painting and decorating business. "
        "Use only the JobHub context provided. Give practical, direct advice for quoting, job costs, scheduling, "
        "materials, staffing, risks and next actions. If data is missing, say what is missing. Do not invent details."
    )

    provider = ai_provider()
    if provider == "none":
        return None, ai_disabled_message()

    if provider == "openai":
        return openai_responses_answer(question, context_text, include_web=False, require_web=False, system_text=system)

    return ollama_generate(question, system=system, context=context_text)


def app_builder_ai_call(question, include_web=False, require_web=False, selected_mode="Code Helper"):
    file_tree = "\n".join(app_builder_file_tree())
    reqs = app_builder_read_file("requirements.txt", max_chars=6000)
    schema = app_builder_read_file("SUPABASE_SCHEMA_MANUAL_BACKUP.sql", max_chars=12000)
    snippets = app_builder_relevant_code_snippets(question)
    saved_notes = app_builder_notes_context()

    system_prompt = f"""
You are App Builder AI inside Premier Brushworks JobHub.
You help improve and maintain this Streamlit + Supabase business app.

Rules:
- Be practical and direct.
- Help design features, find likely bugs, improve speed, improve database structure, and plan safe changes.
- If asked to change the app, provide a clear build plan and exact code/pseudocode sections.
- Do not pretend you have already changed GitHub or deployed the app.
- Do not expose or ask for secrets.
- If internet/web content is provided in context, use it and mention source URLs.
- If something is risky, say so and suggest the safest next step.
- This AI learns by saving notes in app_builder_notes. It does not retrain model weights.
Mode: {selected_mode}
"""

    context = f"""
APP FILE TREE:
{file_tree}

REQUIREMENTS:
{reqs}

DATABASE SCHEMA EXCERPT:
{schema}

RELEVANT CURRENT APP CODE SNIPPETS:
{snippets}

SAVED APP BUILDER LEARNINGS:
{saved_notes}
"""

    provider = ai_provider()
    if provider == "none":
        return None, ai_disabled_message()

    if provider == "openai":
        return openai_responses_answer(
            question,
            context,
            include_web=include_web,
            require_web=require_web,
            system_text=system_prompt,
        )

    if include_web:
        context += (
            "\n\nNOTE: Local Ollama mode does not have paid live web_search. "
            "Use the Internet Learning section with specific URLs to fetch pages for free and save notes."
        )

    return ollama_generate(question, system=system_prompt, context=context, timeout=ollama_timeout())


def fetch_web_page_text(url, max_chars=18000):
    """
    Free URL fetcher for internet learning.
    The user provides URLs. JobHub fetches the page and local Ollama summarises it.
    """
    url = str(url or "").strip()
    if not url:
        return "", "URL is blank."

    parsed = urlparse(url)
    if parsed.scheme not in ["http", "https"]:
        return "", "Only http and https URLs are allowed."

    try:
        response = requests.get(
            url,
            timeout=20,
            headers={
                "User-Agent": "PremierBrushworksJobHubLearningBot/1.0"
            }
        )
        if response.status_code >= 400:
            return "", f"Could not fetch URL. Status {response.status_code}"

        text = response.text
        text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
        text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
        text = re.sub(r"(?is)<noscript.*?>.*?</noscript>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"&lt;", "<", text)
        text = re.sub(r"&gt;", ">", text)
        text = re.sub(r"\s+", " ", text).strip()

        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[trimmed]..."

        return text, None
    except Exception as e:
        return "", f"Fetch failed: {e}"


def save_learning_source(topic, url, summary="", active=1):
    execute("""
        INSERT INTO app_learning_sources
        (topic, url, active, last_checked, last_summary, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        topic,
        url,
        int(active),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S") if summary else "",
        summary,
        "",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ))


def summarise_url_into_learning(topic, url):
    page_text, error = fetch_web_page_text(url)
    if error:
        return None, error

    prompt = (
        "Summarise this web page into practical JobHub learning notes for Premier Brushworks. "
        "Focus on what should be saved for future app building, quoting, cost forecasting, Streamlit, Supabase, "
        "Ollama/local AI, safety, or business operations. "
        "Return concise notes and include the source URL.\n\n"
        f"TOPIC: {topic}\nSOURCE URL: {url}\nPAGE TEXT:\n{page_text}"
    )

    answer, ai_error = app_builder_ai_call(
        question=prompt,
        include_web=False,
        require_web=False,
        selected_mode="Internet Learning Summariser",
    )
    if ai_error:
        return None, ai_error

    save_app_builder_note(topic, answer, source=f"URL: {url}")
    save_learning_source(topic, url, summary=answer, active=1)
    return answer, None


def free_local_ai_setup_page():
    st.header("Free Local AI Setup")
    st.caption("Use OpenAI online on Render, or Ollama for free local AI when running JobHub on your own computer.")

    status_ok, status_message = ai_backend_ready()

    c1, c2 = st.columns(2)
    c1.metric("AI Provider", ai_provider())
    c2.metric("OpenAI Model", jobhub_ai_model() if ai_provider() == "openai" else ollama_model())

    if status_ok:
        st.success(status_message)
    else:
        st.warning(status_message)

    st.markdown("### Recommended Streamlit Secrets")
    st.code(
        'AI_PROVIDER = "ollama"\n'
        'OLLAMA_BASE_URL = "http://localhost:11434"\n'
        'OLLAMA_MODEL = "llama3.2:3b"\n'
        'OLLAMA_TIMEOUT = "120"\n\n'
        '# Optional paid fallback only if you ever want it:\n'
        '# OPENAI_API_KEY = "sk-..."\n'
        '# OPENAI_MODEL = "gpt-5.5"\n',
        language="toml",
    )

    st.markdown("### Test Local AI")
    test_prompt = st.text_input("Test prompt", value="Say hello and confirm you are connected to JobHub.")
    if st.button("Test Ollama Local AI", key="test_ollama_ai"):
        answer, error = ollama_generate(test_prompt, system="You are a local AI test assistant.")
        if error:
            st.error(error)
        else:
            st.success("Local AI responded.")
            st.write(answer)

    st.markdown("### What free learning means")
    st.info(
        "The model learns by saving useful notes into JobHub's database. "
        "It does not retrain the AI model weights. Saved notes are reused as context in future AI answers."
    )


def app_builder_ai_page():
    st.header("App Builder AI")
    st.caption("Build, improve and learn for JobHub using free local Ollama AI by default.")

    status_ok, status_message = ai_backend_ready()
    if status_ok:
        st.success(status_message)
    else:
        st.warning(status_message)
        st.info("Open the Free Local AI Setup tab for install and connection steps.")

    section = st.radio(
        "Section",
        ["Build / Fix the App", "Self-Edit Code", "Internet Learning", "Saved Learnings", "Free Local AI Setup"],
        horizontal=True,
        key="app_builder_section",
    )

    if section == "Build / Fix the App":
        st.subheader("Build / Fix the App")
        mode = st.selectbox(
            "Mode",
            ["Code Helper", "Bug Fixer", "Feature Planner", "Speed Optimiser", "Database / Supabase Helper", "Streamlit UI Helper"],
            key="app_builder_mode",
        )

        include_web = False
        require_web = False

        if ai_provider() == "openai" or (ai_provider() == "auto" and openai_enabled()):
            include_web = st.checkbox("Allow OpenAI live internet research", value=True, key="app_builder_include_web")
            require_web = st.checkbox("Force OpenAI web search for this request", value=False, key="app_builder_require_web")
        else:
            st.info("Free local Ollama mode is active. For internet learning, use the Internet Learning tab with URLs.")

        quick = st.selectbox(
            "Quick request",
            [
                "Custom",
                "Review this app and suggest the next 5 improvements",
                "Help me make the app faster",
                "Help me add a new feature safely",
                "Review saved learning notes and suggest the best next JobHub upgrade",
                "Tell me what code files need changing for this feature",
            ],
            key="app_builder_quick",
        )
        default_question = "" if quick == "Custom" else quick

        question = st.text_area(
            "What do you want to build or fix?",
            value=default_question,
            height=150,
            placeholder="Example: Add a daily dashboard showing jobs starting this week, overdue invoices, missing timesheets and jobs at margin risk.",
            key="app_builder_question",
        )

        if st.checkbox("Show app code context being sent", value=False, key="app_builder_show_context"):
            st.markdown("### File tree")
            st.code("\n".join(app_builder_file_tree()))
            st.markdown("### Relevant snippets")
            st.code(app_builder_relevant_code_snippets(question or "jobhub app"))

        if st.button("Ask App Builder AI", key="ask_app_builder_ai"):
            if not question.strip():
                st.error("Enter a build/fix request first.")
            else:
                with st.spinner("App Builder AI is reviewing JobHub..."):
                    answer, error = app_builder_ai_call(
                        question=question,
                        include_web=include_web,
                        require_web=require_web,
                        selected_mode=mode,
                    )

                if error:
                    st.error(error)
                else:
                    st.markdown("### App Builder AI")
                    st.write(answer)

                    with st.expander("Save this as a learning note"):
                        note_topic = st.text_input("Topic", value=question[:80], key="save_ai_learning_topic")
                        note_text = st.text_area("Note to save", value=answer[:4000], height=200, key="save_ai_learning_text")
                        if st.button("Save Learning Note", key="save_ai_learning_button"):
                            save_app_builder_note(note_topic, note_text, source="App Builder AI")
                            st.success("Learning note saved.")

    elif section == "Self-Edit Code":
        app_builder_self_edit_section()

    elif section == "Internet Learning":
        st.subheader("Free Internet Learning by URL")
        st.caption("Paste useful URLs. JobHub fetches the page, local AI summarises it, and the learning is saved for future use.")

        with st.form("url_learning_form"):
            topic = st.text_input(
                "Learning topic",
                value="Streamlit / Supabase / JobHub app improvement",
            )
            urls_text = st.text_area(
                "URLs to learn from, one per line",
                height=140,
                placeholder="https://docs.streamlit.io/...\nhttps://docs.ollama.com/...",
            )
            submitted = st.form_submit_button("Fetch URLs, Summarise and Save Learning")

        if submitted:
            urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
            if not urls:
                st.error("Paste at least one URL.")
            else:
                for url in urls:
                    st.markdown(f"### Learning from: {url}")
                    with st.spinner(f"Fetching and summarising {url}..."):
                        summary, error = summarise_url_into_learning(topic, url)
                    if error:
                        st.error(error)
                    else:
                        st.success("Saved learning note.")
                        st.write(summary)

        st.markdown("### Saved Learning Sources")
        sources = df_query("""
            SELECT id AS 'ID',
                   topic AS 'Topic',
                   url AS 'URL',
                   active AS 'Active',
                   last_checked AS 'Last Checked',
                   last_summary AS 'Last Summary'
            FROM app_learning_sources
            ORDER BY id DESC
            LIMIT 100
        """)
        if sources.empty:
            st.info("No learning sources saved yet.")
        else:
            st.dataframe(sources[["ID", "Topic", "URL", "Active", "Last Checked"]], width="stretch", hide_index=True)

            if st.button("Refresh All Active Learning Sources", key="refresh_learning_sources"):
                active_sources = sources[sources["Active"].astype(int) == 1]
                if active_sources.empty:
                    st.info("No active sources to refresh.")
                else:
                    for _, row in active_sources.iterrows():
                        st.markdown(f"Refreshing: {row['URL']}")
                        summary, error = summarise_url_into_learning(row["Topic"], row["URL"])
                        if error:
                            st.error(error)
                        else:
                            execute(
                                "UPDATE app_learning_sources SET last_checked = ?, last_summary = ? WHERE id = ?",
                                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), summary, int(row["ID"]))
                            )
                            st.success("Refreshed and saved.")

    elif section == "Saved Learnings":
        st.subheader("Saved Learnings")
        notes = df_query("""
            SELECT id AS 'ID',
                   topic AS 'Topic',
                   source AS 'Source',
                   created_at AS 'Created',
                   note AS 'Note'
            FROM app_builder_notes
            ORDER BY id DESC
        """)

        if notes.empty:
            st.info("No saved learnings yet.")
        else:
            st.dataframe(notes[["ID", "Topic", "Source", "Created"]], width="stretch", hide_index=True)

            note_options = {f"{row['Topic']} | {row['Source']} | ID {row['ID']}": int(row["ID"]) for _, row in notes.iterrows()}
            selected = st.selectbox("Open learning note", list(note_options.keys()), key="open_learning_note")
            selected_id = note_options[selected]
            row = notes[notes["ID"].astype(int) == selected_id].iloc[0]
            st.markdown(f"### {row['Topic']}")
            st.caption(f"{row['Source']} • {row['Created']}")
            st.write(row["Note"])

            col1, col2 = st.columns(2)
            if col1.button("Delete This Learning Note", key="delete_learning_note"):
                execute("DELETE FROM app_builder_notes WHERE id = ?", (selected_id,))
                st.success("Learning note deleted.")
                refresh()

        with st.expander("Add manual learning note"):
            with st.form("manual_learning_note_form"):
                topic = st.text_input("Topic")
                source = st.text_input("Source", value="Manual")
                note = st.text_area("Note", height=180)
                submitted = st.form_submit_button("Save Manual Learning")
                if submitted:
                    if not topic.strip() or not note.strip():
                        st.error("Topic and note are required.")
                    else:
                        save_app_builder_note(topic, note, source=source)
                        st.success("Learning note saved.")
                        refresh()

    else:
        free_local_ai_setup_page()


def jobhub_ai_assistant_page():
    st.header("JobHub AI Assistant")
    st.caption("Ask an AI assistant about your JobHub data, job costs, quotes, scheduling and risks.")

    status_ok, status_message = ai_backend_ready()
    if status_ok:
        st.success(status_message)
    else:
        st.warning(status_message)
        st.info("For free mode, install Ollama and use App Builder AI > Free Local AI Setup.")
        return

    job_options = get_job_options()
    mode = st.radio("Context", ["All Jobs Overview", "Selected Job"], horizontal=True, key="ai_context_mode")
    selected_job_id = None

    if mode == "Selected Job":
        if not job_options:
            st.info("Create a job first.")
            return
        selected_job = st.selectbox("Select Job", list(job_options.keys()), key="ai_selected_job")
        selected_job_id = job_options[selected_job]

    quick = st.selectbox(
        "Quick Question",
        [
            "Custom",
            "Which jobs are at risk of running over budget?",
            "What should I check before quoting this job?",
            "How many painters do I need to finish this job on time?",
            "What materials or timesheets look unusual?",
            "Give me a director-level summary for this week.",
        ],
        key="ai_quick_question",
    )
    default_question = "" if quick == "Custom" else quick

    question = st.text_area(
        "Ask JobHub AI",
        value=default_question,
        height=120,
        placeholder="Example: Review this job and tell me the margin risk, labour pressure and next actions.",
        key="ai_question",
    )

    context_text = jobhub_ai_context(selected_job_id)
    learning_context = app_builder_notes_context(limit=20)
    if learning_context:
        context_text += "\n\nSAVED JOBHUB LEARNINGS:\n" + learning_context

    if st.checkbox("Show data being sent to AI", value=False, key="ai_show_context"):
        st.text_area("Context Preview", value=context_text, height=300)

    if st.button("Ask JobHub AI", key="ask_jobhub_ai"):
        if not question.strip():
            st.error("Enter a question first.")
        else:
            with st.spinner("JobHub AI is reviewing your data..."):
                answer, error = jobhub_ai_answer(question, context_text)
            if error:
                st.error(error)
            else:
                st.markdown("### Answer")
                st.write(answer)



# =============================
# PB CONTROL CENTRE
# =============================
def pb_float(value, default=0.0):
    try:
        if value is None or value == "" or pd.isna(value):
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def pb_date(value):
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()[:10]
    for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"]:
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            pass
    return None


def pb_percent(numerator, denominator):
    denominator = pb_float(denominator)
    if denominator == 0:
        return 0.0
    return round((pb_float(numerator) / denominator) * 100, 2)


def pb_business_days(start_value, end_value):
    start = pb_date(start_value)
    end = pb_date(end_value)
    if not start or not end or end < start:
        return 0
    total = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            total += 1
        current += timedelta(days=1)
    return total


def pb_next_variation_no(job_id):
    df = df_query("SELECT COUNT(*) AS c FROM job_variations WHERE job_id = ?", (job_id,))
    return f"VAR-{int(df.iloc[0]['c']) + 1:03d}" if not df.empty else "VAR-001"


def pb_next_claim_no(job_id):
    df = df_query("SELECT COUNT(*) AS c FROM invoice_claims WHERE job_id = ?", (job_id,))
    return f"CLAIM-{int(df.iloc[0]['c']) + 1:03d}" if not df.empty else "CLAIM-001"


def pb_job_cost_frame():
    jobs = df_query("""
        SELECT j.id AS job_id,
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               COALESCE(bc.name, '') AS 'Builder / Client',
               j.site_address AS 'Site Address',
               j.status AS 'Status',
               j.leading_hand AS 'Leading Hand',
               j.start_date AS 'Start Date',
               j.end_date AS 'End Date',
               COALESCE(j.contract_value, 0) AS 'Contract Value',
               j.notes AS 'Notes'
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        ORDER BY j.job_no
    """)
    if jobs.empty:
        return jobs

    materials = df_query("""
        SELECT m.job_id,
               COALESCE(SUM(COALESCE(m.qty_required, 0) * COALESCE(p.price_ex_gst, 0)), 0) AS 'Material Cost',
               COALESCE(SUM(COALESCE(m.qty_required, 0)), 0) AS 'Material Qty Required',
               COALESCE(SUM(COALESCE(m.qty_received, 0)), 0) AS 'Material Qty Received',
               COUNT(*) AS 'Material Lines'
        FROM material_entries m
        LEFT JOIN products p ON p.id = m.product_id
        GROUP BY m.job_id
    """)

    wages = df_query("""
        SELECT w.job_id,
               COALESCE(SUM(COALESCE(w.hours, 0)), 0) AS 'Wage Hours',
               COALESCE(SUM(COALESCE(w.hours, 0) * COALESCE(e.rate_plus_10, e.base_hourly_rate, 0)), 0) AS 'Labour Cost'
        FROM wage_entries w
        LEFT JOIN employees e ON e.id = w.employee_id
        GROUP BY w.job_id
    """)

    timesheets = df_query("""
        SELECT job_id,
               COALESCE(SUM(COALESCE(total_hours, 0)), 0) AS 'Timesheet Hours',
               COUNT(*) AS 'Timesheet Lines'
        FROM timesheet_entries
        WHERE COALESCE(status, 'Submitted') <> 'Rejected'
        GROUP BY job_id
    """)

    budgets = df_query("""
        SELECT job_id,
               COALESCE(quoted_labour_hours, 0) AS 'Budget Labour Hours',
               COALESCE(quoted_labour_cost, 0) AS 'Budget Labour Cost',
               COALESCE(quoted_materials, 0) AS 'Budget Materials',
               COALESCE(quoted_access_equipment, 0) AS 'Budget Access',
               COALESCE(quoted_subcontractors, 0) AS 'Budget Subcontractors',
               COALESCE(quoted_sundries, 0) AS 'Budget Sundries',
               COALESCE(target_gp_percent, 35) AS 'Target GP %',
               locked_at AS 'Budget Locked'
        FROM job_budgets
    """)

    variations = df_query("""
        SELECT job_id,
               COALESCE(SUM(CASE WHEN status IN ('Approved', 'Sent') THEN COALESCE(amount_ex_gst, 0) ELSE 0 END), 0) AS 'Variation Value',
               COALESCE(SUM(CASE WHEN status = 'Approved' THEN COALESCE(amount_ex_gst, 0) ELSE 0 END), 0) AS 'Approved Variation Value',
               COUNT(*) AS 'Variation Count'
        FROM job_variations
        GROUP BY job_id
    """)

    claims = df_query("""
        SELECT job_id,
               COALESCE(SUM(COALESCE(amount_ex_gst, 0)), 0) AS 'Claimed Amount',
               COALESCE(SUM(CASE WHEN status = 'Paid' THEN COALESCE(amount_ex_gst, 0) ELSE 0 END), 0) AS 'Paid Amount',
               COUNT(*) AS 'Claim Count'
        FROM invoice_claims
        GROUP BY job_id
    """)

    df = jobs.copy()
    for extra in [materials, wages, timesheets, budgets, variations, claims]:
        if extra is not None and not extra.empty:
            df = df.merge(extra, on="job_id", how="left")

    numeric_cols = [
        "Contract Value", "Material Cost", "Material Qty Required", "Material Qty Received", "Material Lines",
        "Wage Hours", "Labour Cost", "Timesheet Hours", "Timesheet Lines", "Budget Labour Hours",
        "Budget Labour Cost", "Budget Materials", "Budget Access", "Budget Subcontractors", "Budget Sundries",
        "Target GP %", "Variation Value", "Approved Variation Value", "Variation Count", "Claimed Amount",
        "Paid Amount", "Claim Count"
    ]
    for col in numeric_cols:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = df[col].fillna(0)

    for col in ["Budget Locked"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("")

    df["Adjusted Contract Value"] = df["Contract Value"] + df["Approved Variation Value"]
    df["Total Budget Cost"] = df["Budget Labour Cost"] + df["Budget Materials"] + df["Budget Access"] + df["Budget Subcontractors"] + df["Budget Sundries"]
    df["Total Actual Cost"] = df["Material Cost"] + df["Labour Cost"]
    df["Gross Profit"] = df["Adjusted Contract Value"] - df["Total Actual Cost"]
    df["Gross Profit %"] = df.apply(lambda r: pb_percent(r["Gross Profit"], r["Adjusted Contract Value"]), axis=1)
    df["Cost to Date %"] = df.apply(lambda r: pb_percent(r["Total Actual Cost"], r["Adjusted Contract Value"]), axis=1)
    df["Remaining Budget"] = (df["Adjusted Contract Value"] - df["Total Actual Cost"]).clip(lower=0)
    df["Budget Variance"] = df["Total Budget Cost"] - df["Total Actual Cost"]
    df["Remaining Labour Hours"] = (df["Budget Labour Hours"] - df["Timesheet Hours"]).clip(lower=0)
    df["Working Days"] = df.apply(lambda r: pb_business_days(r["Start Date"], r["End Date"]), axis=1)
    df["Unclaimed Amount"] = (df["Adjusted Contract Value"] - df["Claimed Amount"]).clip(lower=0)
    df["Unpaid Claimed"] = (df["Claimed Amount"] - df["Paid Amount"]).clip(lower=0)

    def health(row):
        today = date.today()
        issues = []
        gp = pb_float(row["Gross Profit %"])
        cost_pct = pb_float(row["Cost to Date %"])
        target_gp = pb_float(row["Target GP %"], 35)
        end = pb_date(row["End Date"])

        if pb_float(row["Adjusted Contract Value"]) <= 0:
            issues.append("No contract value")
        if row["Budget Locked"] in [None, ""]:
            issues.append("Budget not locked")
        if gp < target_gp:
            issues.append("GP below target")
        if cost_pct > 85 and str(row["Status"]).lower() not in ["complete", "completed", "closed", "archived"]:
            issues.append("Cost high")
        if end and end < today and str(row["Status"]).lower() not in ["complete", "completed", "closed", "archived"]:
            issues.append("Past end date")
        if pb_float(row["Material Qty Required"]) > 0 and pb_float(row["Material Qty Received"]) < pb_float(row["Material Qty Required"]):
            issues.append("Materials short")

        if len(issues) >= 2:
            return "Red", "; ".join(issues)
        if len(issues) == 1:
            return "Orange", "; ".join(issues)
        return "Green", "On track"

    health_data = df.apply(health, axis=1)
    df["Health"] = [x[0] for x in health_data]
    df["Health Notes"] = [x[1] for x in health_data]
    return df


def pb_control_daily_dashboard(df):
    st.subheader("Daily Dashboard")

    today = date.today()
    week_end = today + timedelta(days=7)

    active = df[~df["Status"].astype(str).str.lower().isin(["complete", "completed", "closed", "archived"])]
    red = df[df["Health"] == "Red"]
    orange = df[df["Health"] == "Orange"]

    pending_timesheets = df_query("""
        SELECT COUNT(*) AS c
        FROM timesheet_entries
        WHERE COALESCE(status, 'Submitted') = 'Submitted'
    """)
    pending_count = int(pending_timesheets.iloc[0]["c"]) if not pending_timesheets.empty else 0

    overdue_claims = df_query("""
        SELECT COUNT(*) AS c,
               COALESCE(SUM(COALESCE(amount_ex_gst, 0)), 0) AS total
        FROM invoice_claims
        WHERE status <> 'Paid'
          AND due_date IS NOT NULL
          AND due_date <> ''
          AND due_date < ?
    """, (str(today),))
    overdue_count = int(overdue_claims.iloc[0]["c"]) if not overdue_claims.empty else 0
    overdue_total = pb_float(overdue_claims.iloc[0]["total"]) if not overdue_claims.empty else 0

    cols = st.columns(6)
    cols[0].metric("Active Jobs", len(active))
    cols[1].metric("Red Jobs", len(red))
    cols[2].metric("Orange Jobs", len(orange))
    cols[3].metric("Timesheets Pending", pending_count)
    cols[4].metric("Overdue Claims", overdue_count)
    cols[5].metric("Overdue $", f"${overdue_total:,.0f}")

    st.markdown("### Jobs Needing Attention")
    risk_cols = ["Job No", "Job Name", "Status", "Health", "Health Notes", "Adjusted Contract Value", "Total Actual Cost", "Gross Profit %", "End Date"]
    risks = df[df["Health"].isin(["Red", "Orange"])][risk_cols]
    if risks.empty:
        st.success("No red or orange jobs found.")
    else:
        st.dataframe(risks, width="stretch", hide_index=True)

    st.markdown("### Jobs Starting / Finishing This Week")
    week_rows = []
    for _, row in df.iterrows():
        start = pb_date(row["Start Date"])
        end = pb_date(row["End Date"])
        if (start and today <= start <= week_end) or (end and today <= end <= week_end):
            week_rows.append(row)
    if week_rows:
        week_df = pd.DataFrame(week_rows)
        st.dataframe(week_df[["Job No", "Job Name", "Status", "Leading Hand", "Start Date", "End Date", "Health"]], width="stretch", hide_index=True)
    else:
        st.info("No jobs starting or finishing in the next 7 days.")


def pb_control_job_health(df):
    st.subheader("Job Health Score")
    st.caption("Green = on track, Orange = needs attention, Red = margin/schedule/data risk.")

    status_filter = st.selectbox("Status Filter", ["All"] + sorted([str(x) for x in df["Status"].fillna("").unique() if str(x).strip()]), key="health_status_filter")
    filtered = df.copy()
    if status_filter != "All":
        filtered = filtered[filtered["Status"].astype(str) == status_filter]

    health_filter = st.multiselect("Health Filter", ["Green", "Orange", "Red"], default=["Green", "Orange", "Red"], key="health_filter")
    filtered = filtered[filtered["Health"].isin(health_filter)]

    cols = ["Job No", "Job Name", "Builder / Client", "Status", "Health", "Health Notes", "Adjusted Contract Value", "Total Actual Cost", "Gross Profit %", "Cost to Date %", "Remaining Labour Hours", "End Date"]
    st.dataframe(filtered[cols], width="stretch", hide_index=True)


def pb_control_budget_lock(df):
    st.subheader("Job Budget Lock-In")
    st.caption("Lock in accepted quote budgets so actual labour/materials can be compared against the allowed budget.")

    job_options = get_job_options()
    if not job_options:
        st.info("Create a job first.")
        return

    selected_job = st.selectbox("Job", list(job_options.keys()), key="budget_lock_job")
    job_id = job_options[selected_job]

    existing = df_query("SELECT * FROM job_budgets WHERE job_id = ?", (job_id,))
    current = existing.iloc[0].to_dict() if not existing.empty else {}

    with st.form("job_budget_form"):
        c1, c2, c3 = st.columns(3)
        quoted_labour_hours = c1.number_input("Quoted Labour Hours", min_value=0.0, value=pb_float(current.get("quoted_labour_hours", 0)), step=1.0)
        quoted_labour_cost = c2.number_input("Quoted Labour Cost", min_value=0.0, value=pb_float(current.get("quoted_labour_cost", 0)), step=100.0)
        quoted_materials = c3.number_input("Quoted Materials", min_value=0.0, value=pb_float(current.get("quoted_materials", 0)), step=100.0)

        c4, c5, c6 = st.columns(3)
        quoted_access = c4.number_input("Access / Equipment Allowance", min_value=0.0, value=pb_float(current.get("quoted_access_equipment", 0)), step=100.0)
        quoted_subbies = c5.number_input("Subcontractor Allowance", min_value=0.0, value=pb_float(current.get("quoted_subcontractors", 0)), step=100.0)
        quoted_sundries = c6.number_input("Sundries / Consumables", min_value=0.0, value=pb_float(current.get("quoted_sundries", 0)), step=50.0)

        target_gp = st.number_input("Target GP %", min_value=0.0, max_value=100.0, value=pb_float(current.get("target_gp_percent", 35), 35), step=1.0)
        notes = st.text_area("Budget Notes", value=str(current.get("notes", "") or ""))
        submitted = st.form_submit_button("Save / Lock Job Budget")

    if submitted:
        if existing.empty:
            execute("""
                INSERT INTO job_budgets
                (job_id, quoted_labour_hours, quoted_labour_cost, quoted_materials, quoted_access_equipment,
                 quoted_subcontractors, quoted_sundries, target_gp_percent, locked_at, locked_by, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (job_id, quoted_labour_hours, quoted_labour_cost, quoted_materials, quoted_access, quoted_subbies, quoted_sundries, target_gp, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), current_username(), notes))
        else:
            execute("""
                UPDATE job_budgets
                SET quoted_labour_hours = ?, quoted_labour_cost = ?, quoted_materials = ?, quoted_access_equipment = ?,
                    quoted_subcontractors = ?, quoted_sundries = ?, target_gp_percent = ?, locked_at = ?, locked_by = ?, notes = ?
                WHERE job_id = ?
            """, (quoted_labour_hours, quoted_labour_cost, quoted_materials, quoted_access, quoted_subbies, quoted_sundries, target_gp, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), current_username(), notes, job_id))
        st.success("Job budget saved.")
        refresh()

    budget_df = df_query("""
        SELECT j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               b.quoted_labour_hours AS 'Labour Hours',
               b.quoted_labour_cost AS 'Labour Cost',
               b.quoted_materials AS 'Materials',
               b.quoted_access_equipment AS 'Access',
               b.quoted_subcontractors AS 'Subcontractors',
               b.quoted_sundries AS 'Sundries',
               b.target_gp_percent AS 'Target GP %',
               b.locked_at AS 'Locked At',
               b.locked_by AS 'Locked By'
        FROM job_budgets b
        JOIN jobs j ON j.id = b.job_id
        ORDER BY j.job_no
    """)
    st.markdown("### Locked Budgets")
    st.dataframe(budget_df, width="stretch", hide_index=True)


def pb_control_variations():
    st.subheader("Variations Register")
    job_options = get_job_options()
    if not job_options:
        st.info("Create a job first.")
        return

    with st.expander("Add Variation", expanded=True):
        selected_job = st.selectbox("Job", list(job_options.keys()), key="variation_job")
        job_id = job_options[selected_job]
        with st.form("variation_form"):
            c1, c2, c3 = st.columns(3)
            variation_no = c1.text_input("Variation No", value=pb_next_variation_no(job_id))
            amount = c2.number_input("Amount Ex GST", min_value=0.0, step=100.0)
            status = c3.selectbox("Status", ["Draft", "Sent", "Approved", "Rejected"])
            description = st.text_area("Description")
            reason = st.text_area("Reason")
            c4, c5, c6 = st.columns(3)
            sent_date = c4.text_input("Sent Date", value=str(date.today()) if status in ["Sent", "Approved"] else "")
            approved_date = c5.text_input("Approved Date", value=str(date.today()) if status == "Approved" else "")
            approved_by = c6.text_input("Approved By")
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Variation")
        if submitted:
            execute("""
                INSERT INTO job_variations
                (job_id, variation_no, description, reason, amount_ex_gst, status, sent_date, approved_date, approved_by, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (job_id, variation_no, description, reason, amount, status, sent_date, approved_date, approved_by, notes, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            st.success("Variation saved.")
            refresh()

    variations = df_query("""
        SELECT v.id AS 'ID',
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               v.variation_no AS 'Variation',
               v.description AS 'Description',
               v.amount_ex_gst AS 'Amount Ex GST',
               v.status AS 'Status',
               v.sent_date AS 'Sent',
               v.approved_date AS 'Approved',
               v.approved_by AS 'Approved By'
        FROM job_variations v
        JOIN jobs j ON j.id = v.job_id
        ORDER BY v.id DESC
    """)
    st.dataframe(variations, width="stretch", hide_index=True)


def pb_control_invoice_claims():
    st.subheader("Invoice / Claim Tracker")
    job_options = get_job_options()
    if not job_options:
        st.info("Create a job first.")
        return

    with st.expander("Add Invoice / Claim", expanded=True):
        selected_job = st.selectbox("Job", list(job_options.keys()), key="claim_job")
        job_id = job_options[selected_job]
        with st.form("claim_form"):
            c1, c2, c3 = st.columns(3)
            claim_no = c1.text_input("Claim / Invoice No", value=pb_next_claim_no(job_id))
            amount = c2.number_input("Amount Ex GST", min_value=0.0, step=100.0)
            status = c3.selectbox("Status", ["Draft", "Sent", "Approved", "Paid", "Overdue", "Void"])
            description = st.text_area("Description")
            c4, c5, c6 = st.columns(3)
            invoice_date = c4.text_input("Invoice Date", value=str(date.today()))
            due_date = c5.text_input("Due Date")
            paid_date = c6.text_input("Paid Date")
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Claim")
        if submitted:
            execute("""
                INSERT INTO invoice_claims
                (job_id, claim_no, description, amount_ex_gst, invoice_date, due_date, paid_date, status, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (job_id, claim_no, description, amount, invoice_date, due_date, paid_date, status, notes, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            st.success("Invoice / claim saved.")
            refresh()

    claims = df_query("""
        SELECT c.id AS 'ID',
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               c.claim_no AS 'Claim',
               c.description AS 'Description',
               c.amount_ex_gst AS 'Amount Ex GST',
               c.invoice_date AS 'Invoice Date',
               c.due_date AS 'Due Date',
               c.paid_date AS 'Paid Date',
               c.status AS 'Status'
        FROM invoice_claims c
        JOIN jobs j ON j.id = c.job_id
        ORDER BY c.id DESC
    """)
    st.dataframe(claims, width="stretch", hide_index=True)


def pb_control_staff_schedule():
    st.subheader("Staff Scheduling Board")
    job_options = get_job_options()
    employee_options = get_employee_options(active_only=True)
    if not job_options or not employee_options:
        st.info("Create jobs and active employees first.")
        return

    with st.expander("Add Staff Schedule Entry", expanded=True):
        with st.form("staff_schedule_form"):
            c1, c2 = st.columns(2)
            selected_job = c1.selectbox("Job", list(job_options.keys()), key="schedule_job")
            selected_employee = c2.selectbox("Employee", list(employee_options.keys()), key="schedule_employee")
            c3, c4, c5 = st.columns(3)
            schedule_date = c3.text_input("Date", value=str(date.today()))
            start_time = c4.text_input("Start Time", value="07:00")
            finish_time = c5.text_input("Finish Time", value="15:00")
            site_role = st.selectbox("Site Role", ["Painter", "Leading Hand", "Supervisor", "Apprentice", "Subcontractor", "Other"])
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Schedule Entry")
        if submitted:
            execute("""
                INSERT INTO staff_schedule
                (job_id, employee_id, schedule_date, start_time, finish_time, site_role, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (job_options[selected_job], employee_options[selected_employee], schedule_date, start_time, finish_time, site_role, notes, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            st.success("Schedule entry saved.")
            refresh()

    c1, c2 = st.columns(2)
    start_filter = c1.text_input("From Date", value=str(date.today()))
    end_filter = c2.text_input("To Date", value=str(date.today() + timedelta(days=7)))
    schedule = df_query("""
        SELECT s.id AS 'ID',
               s.schedule_date AS 'Date',
               e.name AS 'Employee',
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               s.start_time AS 'Start',
               s.finish_time AS 'Finish',
               s.site_role AS 'Role',
               s.notes AS 'Notes'
        FROM staff_schedule s
        JOIN jobs j ON j.id = s.job_id
        JOIN employees e ON e.id = s.employee_id
        WHERE s.schedule_date >= ? AND s.schedule_date <= ?
        ORDER BY s.schedule_date, e.name
    """, (start_filter, end_filter))
    st.dataframe(schedule, width="stretch", hide_index=True)


def pb_control_timesheet_approval():
    st.subheader("Timesheet Approval")
    st.caption("Approve or reject submitted timesheets before they are treated as final.")

    pending = df_query("""
        SELECT t.id AS 'ID',
               t.work_date AS 'Date',
               e.name AS 'Employee',
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               t.start_time AS 'Start',
               t.finish_time AS 'Finish',
               t.break_minutes AS 'Break',
               t.total_hours AS 'Hours',
               t.work_type AS 'Work Type',
               COALESCE(t.status, 'Submitted') AS 'Status',
               t.notes AS 'Notes'
        FROM timesheet_entries t
        JOIN jobs j ON j.id = t.job_id
        JOIN employees e ON e.id = t.employee_id
        WHERE COALESCE(t.status, 'Submitted') = 'Submitted'
        ORDER BY t.work_date DESC, t.id DESC
    """)

    if pending.empty:
        st.success("No submitted timesheets waiting for approval.")
        return

    st.dataframe(pending, width="stretch", hide_index=True)
    options = {f"{row['Date']} | {row['Employee']} | {row['Job No']} | {row['Hours']} hrs | ID {row['ID']}": int(row["ID"]) for _, row in pending.iterrows()}
    selected = st.multiselect("Select timesheets", list(options.keys()), key="approve_timesheets_select")
    selected_ids = [options[x] for x in selected]

    c1, c2, c3 = st.columns(3)
    if c1.button("Approve Selected Timesheets"):
        for ts_id in selected_ids:
            execute("UPDATE timesheet_entries SET status = 'Approved' WHERE id = ?", (ts_id,))
        st.success(f"Approved {len(selected_ids)} timesheet(s).")
        refresh()
    if c2.button("Reject Selected Timesheets"):
        for ts_id in selected_ids:
            execute("UPDATE timesheet_entries SET status = 'Rejected' WHERE id = ?", (ts_id,))
        st.warning(f"Rejected {len(selected_ids)} timesheet(s).")
        refresh()
    if c3.button("Mark Selected As Paid/Processed"):
        for ts_id in selected_ids:
            execute("UPDATE timesheet_entries SET status = 'Processed' WHERE id = ?", (ts_id,))
        st.info(f"Marked {len(selected_ids)} timesheet(s) as processed.")
        refresh()


def pb_control_ai_job_review(df):
    st.subheader("AI Job Review")
    st.caption("Uses your JobHub AI/local Ollama setup to review margin, labour, material and schedule risk.")

    job_options = {f"{r['Job No']} - {r['Job Name']}": int(r["job_id"]) for _, r in df.iterrows()}
    if not job_options:
        st.info("Create a job first.")
        return

    selected = st.selectbox("Select Job", list(job_options.keys()), key="control_ai_review_job")
    job_id = job_options[selected]
    row = df[df["job_id"].astype(int) == int(job_id)].iloc[0]

    context = "\n".join([f"{col}: {row[col]}" for col in df.columns if col != "job_id"])
    prompt = (
        "Review this painting job for Premier Brushworks. "
        "Give a practical job risk review with: margin risk, labour risk, materials risk, schedule risk, "
        "missing information, and the next 5 actions for Nick/Bryce.\n\n"
        + context
    )

    if st.checkbox("Show AI context", value=False, key="show_control_ai_context"):
        st.text_area("Context", value=context, height=300)

    if st.button("Review This Job With AI"):
        with st.spinner("AI reviewing job..."):
            answer, error = jobhub_ai_answer(prompt, context)
        if error:
            st.error(error)
        else:
            st.markdown("### AI Review")
            st.write(answer)


def control_centre_page():
    st.header("Premier Brushworks Control Centre")
    st.caption("Daily dashboard, job health, budget lock-in, variations, claims, scheduling, timesheet approval and AI job review.")

    df = pb_job_cost_frame()
    if df.empty:
        st.info("Create your first job to start using the Control Centre.")
        return

    section = st.radio(
        "Control Centre Section",
        [
            "Daily Dashboard",
            "Job Health Score",
            "Job Budget Lock-In",
            "Variations Register",
            "Invoice / Claim Tracker",
            "Staff Scheduling Board",
            "Timesheet Approval",
            "AI Job Review",
            "Export Control Centre"
        ],
        horizontal=False,
        key="control_centre_section"
    )

    if section == "Daily Dashboard":
        pb_control_daily_dashboard(df)
    elif section == "Job Health Score":
        pb_control_job_health(df)
    elif section == "Job Budget Lock-In":
        pb_control_budget_lock(df)
    elif section == "Variations Register":
        pb_control_variations()
    elif section == "Invoice / Claim Tracker":
        pb_control_invoice_claims()
    elif section == "Staff Scheduling Board":
        pb_control_staff_schedule()
    elif section == "Timesheet Approval":
        pb_control_timesheet_approval()
    elif section == "AI Job Review":
        pb_control_ai_job_review(df)
    else:
        st.subheader("Export Control Centre")
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.drop(columns=["job_id"], errors="ignore").to_excel(writer, index=False, sheet_name="Job Health")
            df_query("""
                SELECT v.*, j.job_no, j.job_name
                FROM job_variations v
                JOIN jobs j ON j.id = v.job_id
                ORDER BY v.id DESC
            """).to_excel(writer, index=False, sheet_name="Variations")
            df_query("""
                SELECT c.*, j.job_no, j.job_name
                FROM invoice_claims c
                JOIN jobs j ON j.id = c.job_id
                ORDER BY c.id DESC
            """).to_excel(writer, index=False, sheet_name="Claims")
            df_query("""
                SELECT s.*, j.job_no, j.job_name, e.name AS employee
                FROM staff_schedule s
                JOIN jobs j ON j.id = s.job_id
                JOIN employees e ON e.id = s.employee_id
                ORDER BY s.schedule_date DESC
            """).to_excel(writer, index=False, sheet_name="Staff Schedule")
            for ws in writer.book.worksheets:
                for column_cells in ws.columns:
                    max_len = 0
                    col_letter = column_cells[0].column_letter
                    for cell in column_cells:
                        value = "" if cell.value is None else str(cell.value)
                        max_len = max(max_len, len(value))
                    ws.column_dimensions[col_letter].width = min(max(max_len + 2, 12), 45)
        output.seek(0)
        st.download_button(
            "Download Control Centre Excel",
            data=output.getvalue(),
            file_name="PB_JobHub_Control_Centre.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def current_username():
    user = get_current_user() or {}
    return str(user.get("username", "unknown"))




# =============================
# LINKED JOB LOOKUP / DRILL-DOWN
# =============================
def safe_df_query(sql, params=()):
    try:
        return df_query(sql, params)
    except Exception:
        return pd.DataFrame()


def go_to_linked_job_view(job_id=None, builder_id=None, mode=None):
    if job_id is not None:
        st.session_state["linked_view_selected_job_id"] = int(job_id)
    if builder_id is not None:
        st.session_state["linked_view_selected_builder_id"] = int(builder_id)
    if mode:
        st.session_state["linked_view_mode"] = mode
    st.session_state["go_to_menu"] = "Job Lookup / Links"
    st.rerun()


def job_lookup_dataframe(include_archived=True):
    where_clause = "" if include_archived else "WHERE COALESCE(j.status, '') != 'Archived'"
    return df_query(f"""
        SELECT j.id AS job_id,
               j.job_no AS "Job No",
               j.job_name AS "Job Name",
               COALESCE(bc.id, 0) AS builder_id,
               COALESCE(bc.name, '') AS "Builder / Client",
               COALESCE(bc.contact_name, '') AS "Contact",
               COALESCE(bc.phone, '') AS "Phone",
               COALESCE(bc.email, '') AS "Email",
               COALESCE(j.site_address, '') AS "Site Address",
               COALESCE(j.status, '') AS "Status",
               COALESCE(j.leading_hand, '') AS "Leading Hand",
               COALESCE(j.start_date, '') AS "Start Date",
               COALESCE(j.end_date, '') AS "End Date",
               COALESCE(j.contract_value, 0) AS "Contract Value"
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        {where_clause}
        ORDER BY j.job_no
    """)


def make_job_label(row):
    return (
        f"{row['Job No']} - {row['Job Name']} | "
        f"{row['Builder / Client']} | {row['Site Address']} | {row['Status']}"
    )


def select_job_from_dataframe(jobs_df, label, key, default_job_id=None):
    if jobs_df.empty:
        st.info("No matching jobs found.")
        return None

    job_map = {make_job_label(row): int(row["job_id"]) for _, row in jobs_df.iterrows()}
    labels = list(job_map.keys())

    default_index = 0
    if default_job_id is not None:
        for i, item in enumerate(labels):
            if int(job_map[item]) == int(default_job_id):
                default_index = i
                break

    selected = st.selectbox(label, labels, index=default_index, key=key)
    return job_map[selected]


def display_job_table_with_open_button(jobs_df, table_label, key_prefix):
    if jobs_df.empty:
        st.info("No matching jobs found.")
        return None

    visible_df = jobs_df.drop(columns=["job_id", "builder_id"], errors="ignore")
    st.dataframe(visible_df, width="stretch", hide_index=True)

    selected_job_id = select_job_from_dataframe(
        jobs_df,
        f"Open one of these jobs - {table_label}",
        key=f"{key_prefix}_job_select"
    )

    if st.button("Open selected job and all linked info", key=f"{key_prefix}_open_job"):
        go_to_linked_job_view(job_id=selected_job_id, mode="Open Job")

    return selected_job_id


def render_job_linked_info(job_id, expanded=True):
    job_id = int(job_id)

    job_details = safe_df_query("""
        SELECT j.job_no AS "Job No",
               j.job_name AS "Job Name",
               COALESCE(bc.name, '') AS "Builder / Client",
               COALESCE(bc.contact_name, '') AS "Contact",
               COALESCE(bc.phone, '') AS "Phone",
               COALESCE(bc.email, '') AS "Email",
               COALESCE(bc.terms, '') AS "Terms",
               COALESCE(j.site_address, '') AS "Site Address",
               COALESCE(j.status, '') AS "Status",
               COALESCE(j.leading_hand, '') AS "Leading Hand",
               COALESCE(j.start_date, '') AS "Start Date",
               COALESCE(j.end_date, '') AS "End Date",
               COALESCE(j.contract_value, 0) AS "Contract Value Ex GST",
               COALESCE(j.notes, '') AS "Notes"
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        WHERE j.id = ?
    """, (job_id,))

    if job_details.empty:
        st.warning("Selected job could not be found.")
        return

    job_no = str(job_details.iloc[0]["Job No"])
    job_name = str(job_details.iloc[0]["Job Name"])
    st.markdown(f"## {job_no} - {job_name}")

    material_details = safe_df_query("""
        SELECT m.id AS "ID",
               p.product_code AS "Product Code",
               p.product_name AS "Product Name",
               p.supplier AS "Supplier",
               p.unit AS "Unit",
               p.price_ex_gst AS "Unit Price Ex GST",
               m.qty_required AS "Qty Required",
               m.qty_received AS "Qty Received",
               ROUND(p.price_ex_gst * m.qty_required, 2) AS "Total Cost Ex GST",
               m.date_ordered AS "Date Ordered",
               m.supplier AS "Supplier Override",
               m.notes AS "Notes"
        FROM material_entries m
        JOIN products p ON p.id = m.product_id
        WHERE m.job_id = ?
        ORDER BY m.id DESC
    """, (job_id,))

    imported_materials = safe_df_query("""
        SELECT id AS "ID",
               product AS "Product",
               colour AS "Colour",
               qty_required AS "Qty Required",
               qty_loaded AS "Qty Loaded",
               source_file AS "Source File",
               imported_at AS "Imported At",
               notes AS "Notes"
        FROM imported_material_entries
        WHERE job_id = ?
        ORDER BY id DESC
    """, (job_id,))

    wage_details = safe_df_query("""
        SELECT w.id AS "ID",
               e.name AS "Employee",
               w.work_date AS "Date",
               w.hours AS "Hours",
               e.base_hourly_rate AS "Base Rate",
               e.rate_plus_10 AS "Rate + 10%",
               ROUND(w.hours * e.rate_plus_10, 2) AS "Total Wage Cost",
               w.notes AS "Notes"
        FROM wage_entries w
        JOIN employees e ON e.id = w.employee_id
        WHERE w.job_id = ?
        ORDER BY w.work_date DESC, w.id DESC
    """, (job_id,))

    timesheet_details = safe_df_query("""
        SELECT t.id AS "ID",
               t.work_date AS "Date",
               e.name AS "Employee",
               t.start_time AS "Start",
               t.finish_time AS "Finish",
               t.break_minutes AS "Break Minutes",
               t.total_hours AS "Hours",
               t.work_type AS "Work Type",
               COALESCE(t.status, 'Submitted') AS "Status",
               t.notes AS "Notes"
        FROM timesheet_entries t
        JOIN employees e ON e.id = t.employee_id
        WHERE t.job_id = ?
        ORDER BY t.work_date DESC, t.id DESC
    """, (job_id,))

    estimate_summary = safe_df_query("""
        SELECT estimate_no AS "Estimate No",
               revision AS "Revision",
               estimate_date AS "Date",
               status AS "Status",
               labour_hours AS "Labour Hours",
               labour_rate AS "Labour Rate",
               material_allowance AS "Material Allowance",
               access_equipment_allowance AS "Access / Equipment",
               subcontractor_allowance AS "Subcontractor",
               sundries_allowance AS "Sundries",
               margin_percent AS "Margin %",
               contingency_percent AS "Contingency %",
               total_ex_gst AS "Total Ex GST",
               gst_amount AS "GST",
               total_inc_gst AS "Total Inc GST",
               notes AS "Notes"
        FROM estimate_working_sheets
        WHERE job_id = ?
        ORDER BY id DESC
    """, (job_id,))

    estimate_lines = safe_df_query("""
        SELECT e.estimate_no AS "Estimate No",
               l.section AS "Section",
               l.item_description AS "Description",
               l.qty AS "Qty",
               l.unit AS "Unit",
               l.unit_rate AS "Unit Rate",
               l.line_total AS "Line Total",
               l.notes AS "Notes"
        FROM estimate_line_items l
        JOIN estimate_working_sheets e ON e.id = l.estimate_id
        WHERE e.job_id = ?
        ORDER BY e.id DESC, l.id ASC
    """, (job_id,))

    equipment_master = safe_df_query("""
        SELECT id AS "ID",
               equipment_item AS "Equipment Item",
               category AS "Category",
               serial_no AS "Serial No",
               date_out AS "Date Out",
               date_in AS "Date In",
               condition_out AS "Condition Out",
               condition_in AS "Condition In",
               assigned_to AS "Assigned To",
               notes AS "Notes"
        FROM equipment_entries
        WHERE job_id = ?
        ORDER BY id DESC
    """, (job_id,))

    equipment_detail = safe_df_query("""
        SELECT r.id AS "ID",
               i.category AS "Category",
               i.item_name AS "Item",
               r.qty_required AS "Qty Required",
               r.qty_taken AS "Qty Taken",
               r.qty_returned AS "Qty Returned",
               CASE WHEN r.is_required = 1 THEN 'Yes' ELSE '' END AS "Required",
               CASE WHEN r.is_packed = 1 THEN 'Yes' ELSE '' END AS "Packed",
               CASE WHEN r.is_returned = 1 THEN 'Yes' ELSE '' END AS "Returned",
               r.date_out AS "Date Out",
               r.date_in AS "Date In",
               r.taken_by AS "Taken By",
               r.returned_by AS "Returned By",
               r.condition_out AS "Condition Out",
               r.condition_in AS "Condition In",
               r.notes AS "Notes"
        FROM equipment_checklist_records r
        JOIN equipment_checklist_items i ON i.id = r.checklist_item_id
        WHERE r.job_id = ?
        ORDER BY i.category, i.item_name
    """, (job_id,))

    budget_df = safe_df_query("""
        SELECT quoted_labour_hours AS "Quoted Labour Hours",
               quoted_labour_cost AS "Quoted Labour Cost",
               quoted_materials AS "Quoted Materials",
               quoted_access_equipment AS "Access / Equipment",
               quoted_subcontractors AS "Subcontractors",
               quoted_sundries AS "Sundries",
               target_gp_percent AS "Target GP %",
               locked_at AS "Locked At",
               locked_by AS "Locked By",
               notes AS "Notes"
        FROM job_budgets
        WHERE job_id = ?
    """, (job_id,))

    variations_df = safe_df_query("""
        SELECT variation_no AS "Variation No",
               description AS "Description",
               reason AS "Reason",
               amount_ex_gst AS "Amount Ex GST",
               status AS "Status",
               sent_date AS "Sent Date",
               approved_date AS "Approved Date",
               approved_by AS "Approved By",
               notes AS "Notes"
        FROM job_variations
        WHERE job_id = ?
        ORDER BY id DESC
    """, (job_id,))

    claims_df = safe_df_query("""
        SELECT claim_no AS "Claim / Invoice No",
               description AS "Description",
               amount_ex_gst AS "Amount Ex GST",
               invoice_date AS "Invoice Date",
               due_date AS "Due Date",
               paid_date AS "Paid Date",
               status AS "Status",
               notes AS "Notes"
        FROM invoice_claims
        WHERE job_id = ?
        ORDER BY id DESC
    """, (job_id,))

    schedule_df = safe_df_query("""
        SELECT s.schedule_date AS "Date",
               e.name AS "Employee",
               s.start_time AS "Start",
               s.finish_time AS "Finish",
               s.site_role AS "Role",
               s.notes AS "Notes"
        FROM staff_schedule s
        JOIN employees e ON e.id = s.employee_id
        WHERE s.job_id = ?
        ORDER BY s.schedule_date DESC, e.name
    """, (job_id,))

    photos_meta = safe_df_query("""
        SELECT id AS "Photo ID",
               photo_name AS "Photo Name",
               category AS "Category",
               caption AS "Caption",
               uploaded_by AS "Uploaded By",
               uploaded_at AS "Uploaded At",
               notes AS "Notes"
        FROM job_photos
        WHERE job_id = ?
        ORDER BY uploaded_at DESC, id DESC
    """, (job_id,))

    photos_full = safe_df_query("""
        SELECT id, photo_name, photo_type, photo_data, category, caption, uploaded_by, uploaded_at, notes
        FROM job_photos
        WHERE job_id = ?
        ORDER BY uploaded_at DESC, id DESC
    """, (job_id,))

    material_total = float(material_details["Total Cost Ex GST"].fillna(0).sum()) if not material_details.empty else 0.0
    wage_total = float(wage_details["Total Wage Cost"].fillna(0).sum()) if not wage_details.empty else 0.0
    timesheet_hours = float(timesheet_details["Hours"].fillna(0).sum()) if not timesheet_details.empty else 0.0
    contract_value = float(job_details.iloc[0]["Contract Value Ex GST"] or 0)
    approved_variations = float(variations_df[variations_df["Status"].astype(str).str.lower() == "approved"]["Amount Ex GST"].fillna(0).sum()) if not variations_df.empty else 0.0
    adjusted_contract = contract_value + approved_variations
    gross_position = adjusted_contract - material_total - wage_total

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Contract Ex GST", f"${contract_value:,.2f}")
    m2.metric("Approved Variations", f"${approved_variations:,.2f}")
    m3.metric("Materials", f"${material_total:,.2f}")
    m4.metric("Wages", f"${wage_total:,.2f}")
    m5.metric("Basic Position", f"${gross_position:,.2f}")

    tab_summary, tab_costs, tab_materials, tab_wages, tab_equipment, tab_control, tab_photos = st.tabs([
        "Summary",
        "Costs & Estimates",
        "Materials",
        "Wages & Timesheets",
        "Equipment",
        "Control / Claims",
        "Photos",
    ])

    with tab_summary:
        st.markdown("### Job Details")
        st.dataframe(job_details, width="stretch", hide_index=True)

        st.markdown("### Staff Schedule")
        if schedule_df.empty:
            st.info("No staff schedule entries saved for this job.")
        else:
            st.dataframe(schedule_df, width="stretch", hide_index=True)

    with tab_costs:
        c1, c2, c3 = st.columns(3)
        c1.metric("Timesheet Hours", f"{timesheet_hours:.2f}")
        c2.metric("Adjusted Contract", f"${adjusted_contract:,.2f}")
        c3.metric("Materials + Wages", f"${(material_total + wage_total):,.2f}")

        st.markdown("### Budget Lock-In")
        if budget_df.empty:
            st.info("No budget lock-in saved for this job.")
        else:
            st.dataframe(budget_df, width="stretch", hide_index=True)

        st.markdown("### Estimate Summary")
        if estimate_summary.empty:
            st.info("No estimate working sheets saved for this job.")
        else:
            st.dataframe(estimate_summary, width="stretch", hide_index=True)

        st.markdown("### Estimate Line Items")
        if estimate_lines.empty:
            st.info("No estimate line items saved for this job.")
        else:
            st.dataframe(estimate_lines, width="stretch", hide_index=True)

    with tab_materials:
        st.markdown("### Material Costs")
        if material_details.empty:
            st.info("No material cost entries saved for this job.")
        else:
            st.dataframe(material_details, width="stretch", hide_index=True)

        st.markdown("### Imported PDF Checklist Paint & Materials")
        if imported_materials.empty:
            st.info("No imported checklist material lines saved for this job.")
        else:
            st.dataframe(imported_materials, width="stretch", hide_index=True)

    with tab_wages:
        st.markdown("### Wage Entries")
        if wage_details.empty:
            st.info("No wage entries saved for this job.")
        else:
            st.dataframe(wage_details, width="stretch", hide_index=True)

        st.markdown("### Timesheets")
        if timesheet_details.empty:
            st.info("No timesheets saved for this job.")
        else:
            st.dataframe(timesheet_details, width="stretch", hide_index=True)

    with tab_equipment:
        st.markdown("### Equipment Master Entries")
        if equipment_master.empty:
            st.info("No equipment master entries saved for this job.")
        else:
            st.dataframe(equipment_master, width="stretch", hide_index=True)

        st.markdown("### Equipment Checklist Detail")
        if equipment_detail.empty:
            st.info("No equipment checklist detail saved for this job.")
        else:
            st.dataframe(equipment_detail, width="stretch", hide_index=True)

    with tab_control:
        st.markdown("### Variations")
        if variations_df.empty:
            st.info("No variations saved for this job.")
        else:
            st.dataframe(variations_df, width="stretch", hide_index=True)

        st.markdown("### Claims / Invoices")
        if claims_df.empty:
            st.info("No claims or invoices saved for this job.")
        else:
            st.dataframe(claims_df, width="stretch", hide_index=True)

    with tab_photos:
        st.markdown("### Photo Register")
        if photos_meta.empty:
            st.info("No photos saved for this job.")
        else:
            st.dataframe(photos_meta, width="stretch", hide_index=True)

            with st.expander("View Photo Gallery"):
                for _, photo_row in photos_full.iterrows():
                    title_parts = [
                        str(photo_row["category"] or ""),
                        str(photo_row["caption"] or photo_row["photo_name"] or ""),
                    ]
                    st.markdown("#### " + " - ".join([p for p in title_parts if p]))
                    try:
                        st.image(photo_data_to_bytes(photo_row["photo_data"]), width="stretch")
                    except Exception:
                        st.warning("Could not display photo.")
                    st.caption(f"Uploaded: {photo_row['uploaded_at']} by {photo_row['uploaded_by']}")


def job_lookup_links_page():
    st.header("Job Lookup / Links")
    st.caption("Select a job number, job name, builder/client, address or leading hand and open all linked information for that job.")

    include_archived = st.checkbox("Include archived jobs", value=True, key="linked_include_archived")
    jobs_df = job_lookup_dataframe(include_archived=include_archived)

    if jobs_df.empty:
        st.info("No jobs found.")
        return

    mode_options = ["Open Job", "Jobs by Builder / Client", "Jobs by Leading Hand", "Search Anything"]
    requested_mode = st.session_state.get("linked_view_mode", "Open Job")
    mode_index = mode_options.index(requested_mode) if requested_mode in mode_options else 0
    mode = st.radio("Lookup Mode", mode_options, index=mode_index, horizontal=True, key="linked_view_mode_radio")
    st.session_state["linked_view_mode"] = mode

    selected_job_id = None

    if mode == "Open Job":
        default_job_id = st.session_state.get("linked_view_selected_job_id")
        selected_job_id = select_job_from_dataframe(
            jobs_df,
            "Select job number / job name / builder / address",
            key="linked_open_job_select",
            default_job_id=default_job_id
        )

    elif mode == "Jobs by Builder / Client":
        builders_df = df_query("""
            SELECT id, name
            FROM builders_clients
            ORDER BY name
        """)
        if builders_df.empty:
            st.info("No builders or clients saved yet.")
            return

        builder_map = {str(row["name"]): int(row["id"]) for _, row in builders_df.iterrows()}
        builder_names = list(builder_map.keys())
        default_builder_id = st.session_state.get("linked_view_selected_builder_id")
        builder_index = 0
        if default_builder_id is not None:
            for i, name in enumerate(builder_names):
                if int(builder_map[name]) == int(default_builder_id):
                    builder_index = i
                    break

        selected_builder = st.selectbox("Select builder/client", builder_names, index=builder_index, key="linked_builder_select")
        builder_id = builder_map[selected_builder]
        st.session_state["linked_view_selected_builder_id"] = int(builder_id)

        builder_jobs = jobs_df[jobs_df["builder_id"].astype(int) == int(builder_id)]
        st.markdown(f"### Jobs for {selected_builder}")
        selected_job_id = display_job_table_with_open_button(builder_jobs, selected_builder, "linked_builder_jobs")

    elif mode == "Jobs by Leading Hand":
        leading_hands = sorted([x for x in jobs_df["Leading Hand"].dropna().astype(str).unique().tolist() if x.strip()])
        if not leading_hands:
            st.info("No leading hands saved against jobs yet.")
            return

        selected_leading_hand = st.selectbox("Select leading hand", leading_hands, key="linked_leading_hand")
        lh_jobs = jobs_df[jobs_df["Leading Hand"].astype(str) == selected_leading_hand]
        st.markdown(f"### Jobs for {selected_leading_hand}")
        selected_job_id = display_job_table_with_open_button(lh_jobs, selected_leading_hand, "linked_lh_jobs")

    else:
        search_text = st.text_input("Search job number, job name, builder/client, address, status or leading hand", key="linked_any_search")
        filtered_jobs = jobs_df.copy()
        if search_text.strip():
            haystack = (
                filtered_jobs["Job No"].astype(str) + " " +
                filtered_jobs["Job Name"].astype(str) + " " +
                filtered_jobs["Builder / Client"].astype(str) + " " +
                filtered_jobs["Site Address"].astype(str) + " " +
                filtered_jobs["Status"].astype(str) + " " +
                filtered_jobs["Leading Hand"].astype(str)
            ).str.lower()
            filtered_jobs = filtered_jobs[haystack.str.contains(search_text.strip().lower(), na=False)]
        st.markdown("### Search Results")
        selected_job_id = display_job_table_with_open_button(filtered_jobs, "search results", "linked_search_jobs")

    if selected_job_id:
        st.divider()
        st.session_state["linked_view_selected_job_id"] = int(selected_job_id)
        render_job_linked_info(selected_job_id)


# =============================
# START APP
# =============================
init_db()
set_app_setting("starter_jobs_disabled", "yes")
set_app_setting("starter_data_seeded", "yes")
mark_seeded_if_existing_data_present()
seed_data()
seed_app_users()
require_login()

st.title("Premier Brushworks JobHub")
st.caption("Local job system for jobs, builders, clients, employees, materials, wages and equipment.")
logout_button()

role = current_role()

if role == "employee":
    allowed_menu = ["Employee Portal"]
elif role == "manager":
    allowed_menu = [
        "Dashboard",
        "Control Centre",
        "Jobs",
        "Job Lookup / Links",
        "Estimate Working Sheet",
        "Job Costs / Forecasting",
        "JobHub AI Assistant",
        "App Builder AI",
        "Builders & Clients",
        "Employees",
        "Products",
        "Material Costs",
        "Wages",
        "Timesheets",
        "Equipment",
        "Job Photos",
        "Reports / Export",
    ]
else:
    allowed_menu = [
        "Dashboard",
        "Control Centre",
        "Jobs",
        "Job Lookup / Links",
        "Estimate Working Sheet",
        "Job Costs / Forecasting",
        "JobHub AI Assistant",
        "App Builder AI",
        "Builders & Clients",
        "Employees",
        "Products",
        "Material Costs",
        "Wages",
        "Timesheets",
        "Equipment",
        "Job Photos",
        "Reports / Export",
        "User Access",
    ]

requested_menu = st.session_state.pop("go_to_menu", None)
if requested_menu in allowed_menu:
    st.session_state["main_menu"] = requested_menu

if st.session_state.get("main_menu") not in allowed_menu:
    st.session_state["main_menu"] = allowed_menu[0]

menu = st.sidebar.radio("Menu", allowed_menu, key="main_menu")


# =============================
# EMPLOYEE PORTAL / USER ACCESS
# =============================
if menu == "Employee Portal":
    employee_portal()

elif menu == "App Builder AI":
    app_builder_ai_page()


elif menu == "User Access":
    user_access_page()


# =============================
# DASHBOARD
# =============================
elif menu == "Control Centre":
    control_centre_page()


elif menu == "Job Lookup / Links":
    job_lookup_links_page()


elif menu == "Dashboard":
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Jobs", int(df_query("SELECT COUNT(*) AS c FROM jobs").iloc[0]["c"]))
    c2.metric("Builders / Clients", int(df_query("SELECT COUNT(*) AS c FROM builders_clients").iloc[0]["c"]))
    c3.metric("Employees", int(df_query("SELECT COUNT(*) AS c FROM employees").iloc[0]["c"]))
    c4.metric("Products", int(df_query("SELECT COUNT(*) AS c FROM products").iloc[0]["c"]))

    st.subheader("Open Jobs")
    active = df_query("""
        SELECT j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               bc.name AS 'Builder / Client',
               j.site_address AS 'Site Address',
               j.status AS 'Status',
               j.leading_hand AS 'Leading Hand',
               j.start_date AS 'Start Date'
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        WHERE j.status NOT IN ('Completed', 'Paid', 'Archived')
        ORDER BY j.job_no
    """)
    st.dataframe(active, width="stretch", hide_index=True)


# =============================
# JOBS - ADD / EDIT / REMOVE
# =============================
elif menu == "Jobs":
    st.header("Job Register")
    builder_options = get_builder_options()

    tab_add, tab_edit, tab_remove, tab_archived, tab_search, tab_list = st.tabs(
        ["Add Job", "Edit Job", "Remove / Archive", "Archived Jobs", "Search by Builder", "Job Register"]
    )

    with tab_add:
        st.subheader("Add New Job")
        with st.form("add_job_form"):
            col1, col2 = st.columns(2)
            job_no = col1.text_input("Job Number", next_job_no())
            job_name = col2.text_input("Job Name")

            builder_label = st.selectbox("Builder / Client", [""] + list(builder_options.keys()))
            site_address = st.text_input("Site Address")

            col3, col4, col5 = st.columns(3)
            status = col3.selectbox("Status", ["Not Started", "Quoted", "Booked", "Active", "On Hold", "Completed", "Invoiced", "Paid", "Archived"])
            employee_options_add_job = get_employee_options(active_only=True)
            leading_hand = col4.selectbox("Leading Hand", [""] + list(employee_options_add_job.keys()))
            contract_value = col5.number_input("Contract Value Ex GST", min_value=0.0, step=100.0)

            col6, col7 = st.columns(2)
            start_date = col6.text_input("Start Date", placeholder="DD/MM/YYYY")
            end_date = col7.text_input("End Date", placeholder="DD/MM/YYYY")

            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Job")

            if submitted and job_no:
                builder_id = builder_options.get(builder_label) if builder_label else None
                execute("""
                    INSERT OR REPLACE INTO jobs
                    (job_no, job_name, builder_client_id, site_address, status, leading_hand, start_date, end_date, contract_value, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (job_no, job_name, builder_id, site_address, status, leading_hand, start_date, end_date, contract_value, notes))
                st.success(f"Saved job {job_no}")
                refresh()

    with tab_edit:
        st.subheader("Edit Existing Job")
        jobs_df = df_query("""
            SELECT j.*, COALESCE(bc.name, '') AS builder_name
            FROM jobs j
            LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
            ORDER BY j.job_no
        """)
        if jobs_df.empty:
            st.info("No jobs yet.")
        else:
            job_map = {f"{row['job_no']} - {row['job_name']}": int(row["id"]) for _, row in jobs_df.iterrows()}
            selected_job = st.selectbox("Select Job to Edit", list(job_map.keys()))
            selected_id = job_map[selected_job]
            current = jobs_df[jobs_df["id"] == selected_id].iloc[0]

            builder_names = [""] + list(builder_options.keys())
            current_builder = str(current["builder_name"] or "")
            builder_index = builder_names.index(current_builder) if current_builder in builder_names else 0

            with st.form("edit_job_form"):
                col1, col2 = st.columns(2)
                edit_job_no = col1.text_input("Job Number", value=str(current["job_no"] or ""))
                edit_job_name = col2.text_input("Job Name", value=str(current["job_name"] or ""))

                edit_builder_label = st.selectbox("Builder / Client", builder_names, index=builder_index)
                edit_site_address = st.text_input("Site Address", value=str(current["site_address"] or ""))

                statuses = ["Not Started", "Quoted", "Booked", "Active", "On Hold", "Completed", "Invoiced", "Paid", "Archived"]
                current_status = str(current["status"] or "Not Started")
                status_index = statuses.index(current_status) if current_status in statuses else 0

                col3, col4, col5 = st.columns(3)
                edit_status = col3.selectbox("Status", statuses, index=status_index)
                employee_options_edit_job = get_employee_options(active_only=True)
                employee_names_edit_job = [""] + list(employee_options_edit_job.keys())
                current_leading_hand = str(current["leading_hand"] or "")
                leading_hand_index = employee_names_edit_job.index(current_leading_hand) if current_leading_hand in employee_names_edit_job else 0
                edit_leading_hand = col4.selectbox("Leading Hand", employee_names_edit_job, index=leading_hand_index)
                edit_contract_value = col5.number_input("Contract Value Ex GST", min_value=0.0, step=100.0, value=float(current["contract_value"] or 0))

                col6, col7 = st.columns(2)
                edit_start_date = col6.text_input("Start Date", value=str(current["start_date"] or ""))
                edit_end_date = col7.text_input("End Date", value=str(current["end_date"] or ""))

                edit_notes = st.text_area("Notes", value=str(current["notes"] or ""))
                submitted = st.form_submit_button("Update Job")

                if submitted:
                    edit_builder_id = builder_options.get(edit_builder_label) if edit_builder_label else None
                    execute("""
                        UPDATE jobs
                        SET job_no = ?, job_name = ?, builder_client_id = ?, site_address = ?, status = ?,
                            leading_hand = ?, start_date = ?, end_date = ?, contract_value = ?, notes = ?
                        WHERE id = ?
                    """, (
                        edit_job_no, edit_job_name, edit_builder_id, edit_site_address, edit_status,
                        edit_leading_hand, edit_start_date, edit_end_date, edit_contract_value, edit_notes, selected_id
                    ))
                    st.success(f"Updated job {edit_job_no}")
                    refresh()

    with tab_remove:
        st.subheader("Remove or Archive Job")
        st.warning("If a job has wages, materials or equipment saved against it, archive it instead of deleting it.")
        jobs_df = df_query("SELECT id, job_no, job_name FROM jobs ORDER BY job_no")
        if jobs_df.empty:
            st.info("No jobs yet.")
        else:
            job_map = {f"{row['job_no']} - {row['job_name']}": int(row["id"]) for _, row in jobs_df.iterrows()}
            selected_job = st.selectbox("Select Job", list(job_map.keys()), key="remove_job_select")
            selected_id = job_map[selected_job]

            col1, col2 = st.columns(2)
            if col1.button("Archive Job"):
                execute("UPDATE jobs SET status = 'Archived' WHERE id = ?", (selected_id,))
                st.success("Job archived.")
                refresh()

            if col2.button("Delete Job"):
                linked = (
                    has_related_records("material_entries", "job_id", selected_id)
                    or has_related_records("wage_entries", "job_id", selected_id)
                    or has_related_records("timesheet_entries", "job_id", selected_id)
                    or has_related_records("estimate_working_sheets", "job_id", selected_id)
                    or has_related_records("equipment_entries", "job_id", selected_id)
                    or has_related_records("equipment_checklist_records", "job_id", selected_id)
                )
                if linked:
                    execute("UPDATE jobs SET status = 'Archived' WHERE id = ?", (selected_id,))
                    st.info("This job has linked records, so it was archived instead of deleted.")
                else:
                    execute("DELETE FROM jobs WHERE id = ?", (selected_id,))
                    st.success("Job deleted.")
                refresh()

    with tab_archived:
        st.subheader("Archived Jobs")

        archived_df = df_query("""
            SELECT j.*, COALESCE(bc.name, '') AS builder_name
            FROM jobs j
            LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
            WHERE j.status = 'Archived'
            ORDER BY j.job_no
        """)

        if archived_df.empty:
            st.info("No archived jobs found.")
        else:
            archived_view = archived_df[[
                "job_no", "job_name", "builder_name", "site_address",
                "leading_hand", "start_date", "end_date", "contract_value", "notes"
            ]].rename(columns={
                "job_no": "Job No",
                "job_name": "Job Name",
                "builder_name": "Builder / Client",
                "site_address": "Site Address",
                "leading_hand": "Leading Hand",
                "start_date": "Start Date",
                "end_date": "End Date",
                "contract_value": "Contract Value",
                "notes": "Notes",
            })

            st.markdown("### View Archived Jobs")
            st.dataframe(archived_view, width="stretch", hide_index=True)

            archived_map = {
                f"{row['job_no']} - {row['job_name']}": int(row["id"])
                for _, row in archived_df.iterrows()
            }

            selected_archived_job = st.selectbox(
                "Select Archived Job",
                list(archived_map.keys()),
                key="archived_job_select"
            )
            selected_archived_id = archived_map[selected_archived_job]
            current = archived_df[archived_df["id"] == selected_archived_id].iloc[0]

            counts = linked_job_counts(selected_archived_id)

            st.markdown("### Linked Data Saved Against This Archived Job")
            count_df = pd.DataFrame([
                ["Materials", counts.get("material_entries", 0)],
                ["Wages", counts.get("wage_entries", 0)],
                ["Old Equipment Entries", counts.get("equipment_entries", 0)],
                ["Equipment Checklist Lines", counts.get("equipment_checklist_records", 0)],
                ["Imported Checklist Materials", counts.get("imported_material_entries", 0)],
            ], columns=["Linked Data", "Record Count"])
            st.dataframe(count_df, width="stretch", hide_index=True)

            st.markdown("### Edit Archived Job")
            builder_options_archived = get_builder_options()
            builder_names_archived = [""] + list(builder_options_archived.keys())
            current_builder = str(current["builder_name"] or "")
            builder_index = builder_names_archived.index(current_builder) if current_builder in builder_names_archived else 0

            with st.form("edit_archived_job_form"):
                col1, col2 = st.columns(2)
                edit_job_no = col1.text_input("Job Number", value=str(current["job_no"] or ""), key="arch_job_no")
                edit_job_name = col2.text_input("Job Name", value=str(current["job_name"] or ""), key="arch_job_name")

                edit_builder_label = st.selectbox(
                    "Builder / Client",
                    builder_names_archived,
                    index=builder_index,
                    key="arch_builder"
                )
                edit_site_address = st.text_input("Site Address", value=str(current["site_address"] or ""), key="arch_site_address")

                employee_options_archived_job = get_employee_options(active_only=True)
                employee_names_archived_job = [""] + list(employee_options_archived_job.keys())
                current_leading_hand = str(current["leading_hand"] or "")
                leading_hand_index = employee_names_archived_job.index(current_leading_hand) if current_leading_hand in employee_names_archived_job else 0

                col3, col4, col5 = st.columns(3)
                edit_status = col3.selectbox("Status", ["Archived", "Not Started", "Quoted", "Booked", "Active", "On Hold", "Completed", "Invoiced", "Paid"], index=0, key="arch_status")
                edit_leading_hand = col4.selectbox("Leading Hand", employee_names_archived_job, index=leading_hand_index, key="arch_leading_hand")
                edit_contract_value = col5.number_input(
                    "Contract Value Ex GST",
                    min_value=0.0,
                    step=100.0,
                    value=float(current["contract_value"] or 0),
                    key="arch_contract_value"
                )

                col6, col7 = st.columns(2)
                edit_start_date = col6.text_input("Start Date", value=str(current["start_date"] or ""), key="arch_start_date")
                edit_end_date = col7.text_input("End Date", value=str(current["end_date"] or ""), key="arch_end_date")

                edit_notes = st.text_area("Notes", value=str(current["notes"] or ""), key="arch_notes")
                update_archived = st.form_submit_button("Update Archived Job")

                if update_archived:
                    edit_builder_id = builder_options_archived.get(edit_builder_label) if edit_builder_label else None
                    execute("""
                        UPDATE jobs
                        SET job_no = ?, job_name = ?, builder_client_id = ?, site_address = ?, status = ?,
                            leading_hand = ?, start_date = ?, end_date = ?, contract_value = ?, notes = ?
                        WHERE id = ?
                    """, (
                        edit_job_no, edit_job_name, edit_builder_id, edit_site_address, edit_status,
                        edit_leading_hand, edit_start_date, edit_end_date, edit_contract_value, edit_notes,
                        selected_archived_id
                    ))

                    if edit_status != "Archived":
                        st.success(f"Updated and restored job {edit_job_no}.")
                    else:
                        st.success(f"Updated archived job {edit_job_no}.")
                    refresh()

            st.markdown("### Restore or Permanently Delete")
            col_restore, col_delete = st.columns(2)

            if col_restore.button("Restore Archived Job to Active"):
                execute("UPDATE jobs SET status = 'Active' WHERE id = ?", (selected_archived_id,))
                st.success("Job restored to Active.")
                refresh()

            with col_delete:
                st.warning("Permanent delete removes the archived job and all linked materials, wages, equipment and imported checklist data.")
                confirm_delete = st.checkbox(
                    "I understand this will permanently delete this archived job and all linked data.",
                    key="confirm_delete_archived_job"
                )

                if st.button("Permanently Delete Archived Job"):
                    if not confirm_delete:
                        st.error("Tick the confirmation box before permanently deleting.")
                    else:
                        permanently_delete_job_and_linked_data(selected_archived_id)
                        st.success("Archived job and linked data permanently deleted.")
                        refresh()


    with tab_search:
        st.subheader("Search Job Numbers by Builder / Client")
        selected_builder = st.selectbox("Select Builder / Client", [""] + list(builder_options.keys()), key="job_search_builder")
        if selected_builder:
            search_df = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       j.status AS 'Status',
                       j.site_address AS 'Site Address'
                FROM jobs j
                JOIN builders_clients bc ON bc.id = j.builder_client_id
                WHERE bc.name = ?
                ORDER BY j.job_no
            """, (selected_builder,))
            st.dataframe(search_df, width="stretch", hide_index=True)

            if st.button("Open this builder/client in Job Lookup", key="open_search_builder_linked_view"):
                go_to_linked_job_view(builder_id=builder_options[selected_builder], mode="Jobs by Builder / Client")

    with tab_list:
        st.subheader("Full Job Register")
        include_archived = st.checkbox("Show archived jobs in register", value=True)
        where_clause = "" if include_archived else "WHERE j.status != 'Archived'"

        job_df = df_query(f"""
            SELECT j.job_no AS 'Job No',
                   j.job_name AS 'Job Name',
                   bc.name AS 'Builder / Client',
                   bc.contact_name AS 'Contact',
                   bc.phone AS 'Phone',
                   bc.email AS 'Email',
                   bc.terms AS 'Terms',
                   j.site_address AS 'Site Address',
                   j.status AS 'Status',
                   j.leading_hand AS 'Leading Hand',
                   j.start_date AS 'Start Date',
                   j.end_date AS 'End Date',
                   j.contract_value AS 'Contract Value',
                   j.notes AS 'Notes'
            FROM jobs j
            LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
            {where_clause}
            ORDER BY j.job_no
        """)
        st.dataframe(job_df, width="stretch", hide_index=True)

        st.markdown("### Open Linked Job Info")
        st.caption("Select a job here to open the full linked file: job details, builder, materials, wages, timesheets, equipment, photos, estimates, variations and claims.")
        open_jobs_df = job_lookup_dataframe(include_archived=include_archived)
        selected_open_job_id = select_job_from_dataframe(
            open_jobs_df,
            "Select job number / name / builder / address to open",
            key="job_register_open_linked_select",
            default_job_id=st.session_state.get("linked_view_selected_job_id")
        )
        if selected_open_job_id and st.button("Open selected job and all linked info", key="job_register_open_linked_button"):
            go_to_linked_job_view(job_id=selected_open_job_id, mode="Open Job")


# =============================
# ESTIMATE WORKING SHEET
# =============================
elif menu == "Estimate Working Sheet":
    estimate_working_sheet_page()


# =============================
# BUILDERS / CLIENTS - ADD / EDIT / REMOVE
# =============================
elif menu == "Job Costs / Forecasting":
    job_costs_forecasting_page()


elif menu == "JobHub AI Assistant":
    jobhub_ai_assistant_page()


elif menu == "Builders & Clients":
    st.header("Builders & Clients")

    tab_add, tab_edit, tab_remove, tab_list = st.tabs(["Add", "Edit", "Remove", "List"])

    with tab_add:
        st.subheader("Add Builder / Client")
        with st.form("add_builder_form"):
            col1, col2 = st.columns(2)
            typ = col1.text_input("Type", "Builder")
            name = col2.text_input("Company / Client Name")
            contact = st.text_input("Contact Name")
            col3, col4 = st.columns(2)
            phone = col3.text_input("Phone / Mobile")
            email = col4.text_input("Email")
            address = st.text_input("Address")
            col5, col6, col7 = st.columns(3)
            qbcc = col5.text_input("QBCC")
            abn = col6.text_input("ABN")
            terms = col7.text_input("Payment Terms")
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Builder / Client")

            if submitted and name:
                execute("""
                    INSERT OR REPLACE INTO builders_clients
                    (type, name, contact_name, phone, email, address, qbcc, abn, terms, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (typ, name, contact, phone, email, address, qbcc, abn, terms, notes))
                st.success(f"Saved {name}")
                refresh()

    with tab_edit:
        st.subheader("Edit Builder / Client")
        builders_df = df_query("SELECT * FROM builders_clients ORDER BY name")
        if builders_df.empty:
            st.info("No builders or clients yet.")
        else:
            builder_map = {row["name"]: int(row["id"]) for _, row in builders_df.iterrows()}
            selected_builder = st.selectbox("Select Builder / Client to Edit", list(builder_map.keys()))
            selected_id = builder_map[selected_builder]
            current = builders_df[builders_df["id"] == selected_id].iloc[0]

            with st.form("edit_builder_form"):
                col1, col2 = st.columns(2)
                typ = col1.text_input("Type", value=str(current["type"] or ""))
                name = col2.text_input("Company / Client Name", value=str(current["name"] or ""))
                contact = st.text_input("Contact Name", value=str(current["contact_name"] or ""))
                col3, col4 = st.columns(2)
                phone = col3.text_input("Phone / Mobile", value=str(current["phone"] or ""))
                email = col4.text_input("Email", value=str(current["email"] or ""))
                address = st.text_input("Address", value=str(current["address"] or ""))
                col5, col6, col7 = st.columns(3)
                qbcc = col5.text_input("QBCC", value=str(current["qbcc"] or ""))
                abn = col6.text_input("ABN", value=str(current["abn"] or ""))
                terms = col7.text_input("Payment Terms", value=str(current["terms"] or ""))
                notes = st.text_area("Notes", value=str(current["notes"] or ""))
                submitted = st.form_submit_button("Update Builder / Client")

                if submitted:
                    execute("""
                        UPDATE builders_clients
                        SET type = ?, name = ?, contact_name = ?, phone = ?, email = ?, address = ?,
                            qbcc = ?, abn = ?, terms = ?, notes = ?
                        WHERE id = ?
                    """, (typ, name, contact, phone, email, address, qbcc, abn, terms, notes, selected_id))
                    st.success(f"Updated {name}")
                    refresh()

    with tab_remove:
        st.subheader("Remove Builder / Client")
        st.warning("If this builder/client has jobs linked, they cannot be deleted until the jobs are changed or archived.")
        builders_df = df_query("SELECT id, name FROM builders_clients ORDER BY name")
        if builders_df.empty:
            st.info("No builders or clients yet.")
        else:
            builder_map = {row["name"]: int(row["id"]) for _, row in builders_df.iterrows()}
            selected_builder = st.selectbox("Select Builder / Client to Remove", list(builder_map.keys()), key="remove_builder_select")
            selected_id = builder_map[selected_builder]

            linked_jobs = df_query("SELECT COUNT(*) AS c FROM jobs WHERE builder_client_id = ?", (selected_id,))
            job_count = int(linked_jobs.iloc[0]["c"])
            st.write(f"Linked jobs: {job_count}")

            if st.button("Delete Builder / Client"):
                if job_count > 0:
                    st.error("Cannot delete this builder/client because jobs are linked to them. Edit those jobs first or leave the builder in the database.")
                else:
                    execute("DELETE FROM builders_clients WHERE id = ?", (selected_id,))
                    st.success("Builder/client deleted.")
                    refresh()

    with tab_list:
        st.subheader("Builder & Client List")
        df = df_query("""
            SELECT type AS 'Type',
                   name AS 'Company / Client',
                   contact_name AS 'Contact',
                   phone AS 'Phone',
                   email AS 'Email',
                   address AS 'Address',
                   qbcc AS 'QBCC',
                   abn AS 'ABN',
                   terms AS 'Terms',
                   notes AS 'Notes'
            FROM builders_clients
            ORDER BY name
        """)
        st.dataframe(df, width="stretch", hide_index=True)

        st.markdown("### View Jobs for a Builder / Client")
        builder_lookup = df_query("SELECT id, name FROM builders_clients ORDER BY name")
        if builder_lookup.empty:
            st.info("No builders or clients saved yet.")
        else:
            builder_map = {str(row["name"]): int(row["id"]) for _, row in builder_lookup.iterrows()}
            selected_builder_lookup = st.selectbox(
                "Select builder/client to view linked jobs",
                list(builder_map.keys()),
                key="builder_list_linked_jobs_select"
            )
            selected_builder_id = builder_map[selected_builder_lookup]

            linked_jobs_df = job_lookup_dataframe(include_archived=True)
            linked_jobs_df = linked_jobs_df[linked_jobs_df["builder_id"].astype(int) == int(selected_builder_id)]

            if linked_jobs_df.empty:
                st.info("No jobs linked to this builder/client.")
            else:
                st.dataframe(linked_jobs_df.drop(columns=["job_id", "builder_id"], errors="ignore"), width="stretch", hide_index=True)
                selected_builder_job_id = select_job_from_dataframe(
                    linked_jobs_df,
                    "Select one of this builder/client's jobs",
                    key="builder_list_job_to_open_select"
                )
                col_open_builder, col_open_job = st.columns(2)
                if col_open_builder.button("Open builder/client in Job Lookup", key="builder_list_open_builder_lookup"):
                    go_to_linked_job_view(builder_id=selected_builder_id, mode="Jobs by Builder / Client")
                if col_open_job.button("Open selected job and all linked info", key="builder_list_open_job_lookup"):
                    go_to_linked_job_view(job_id=selected_builder_job_id, mode="Open Job")


# =============================
# EMPLOYEES - ADD / EDIT / REMOVE
# =============================
elif menu == "Employees":
    st.header("Employees")

    tab_add, tab_edit, tab_remove, tab_list = st.tabs(["Add", "Edit", "Remove / Deactivate", "List"])

    with tab_add:
        st.subheader("Add Employee")
        with st.form("add_employee_form"):
            col1, col2 = st.columns(2)
            name = col1.text_input("Employee Name")
            role = col2.text_input("Role")
            phone = st.text_input("Phone")
            col3, col4 = st.columns(2)
            base_rate = col3.number_input("Base Hourly Rate", min_value=0.0, step=1.0)
            rate_plus = col4.number_input("Rate + 10%", min_value=0.0, step=1.0, value=0.0)
            status = st.selectbox("Status", ["Active", "Inactive"])
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Employee")

            if submitted and name:
                if rate_plus == 0 and base_rate > 0:
                    rate_plus = round(base_rate * 1.10, 2)
                execute("""
                    INSERT OR REPLACE INTO employees
                    (name, role, phone, base_hourly_rate, rate_plus_10, status, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (name, role, phone, base_rate, rate_plus, status, notes))
                st.success(f"Saved {name}")
                refresh()

    with tab_edit:
        st.subheader("Edit Employee")
        employees_df = df_query("SELECT * FROM employees ORDER BY name")
        if employees_df.empty:
            st.info("No employees yet.")
        else:
            employee_map = {row["name"]: int(row["id"]) for _, row in employees_df.iterrows()}
            selected_employee = st.selectbox("Select Employee to Edit", list(employee_map.keys()))
            selected_id = employee_map[selected_employee]
            current = employees_df[employees_df["id"] == selected_id].iloc[0]

            with st.form("edit_employee_form"):
                col1, col2 = st.columns(2)
                name = col1.text_input("Employee Name", value=str(current["name"] or ""))
                role = col2.text_input("Role", value=str(current["role"] or ""))
                phone = st.text_input("Phone", value=str(current["phone"] or ""))

                col3, col4 = st.columns(2)
                base_rate = col3.number_input("Base Hourly Rate", min_value=0.0, step=1.0, value=float(current["base_hourly_rate"] or 0))
                rate_plus = col4.number_input("Rate + 10%", min_value=0.0, step=1.0, value=float(current["rate_plus_10"] or 0))

                statuses = ["Active", "Inactive"]
                current_status = str(current["status"] or "Active")
                status_index = statuses.index(current_status) if current_status in statuses else 0
                status = st.selectbox("Status", statuses, index=status_index)

                notes = st.text_area("Notes", value=str(current["notes"] or ""))
                submitted = st.form_submit_button("Update Employee")

                if submitted:
                    if rate_plus == 0 and base_rate > 0:
                        rate_plus = round(base_rate * 1.10, 2)
                    execute("""
                        UPDATE employees
                        SET name = ?, role = ?, phone = ?, base_hourly_rate = ?, rate_plus_10 = ?, status = ?, notes = ?
                        WHERE id = ?
                    """, (name, role, phone, base_rate, rate_plus, status, notes, selected_id))
                    st.success(f"Updated {name}")
                    refresh()

    with tab_remove:
        st.subheader("Remove or Deactivate Employee")
        st.warning("If the employee has wage records, timesheets, or a linked user login, the app will mark them Inactive instead of deleting their history.")
        employees_df = df_query("SELECT id, name FROM employees ORDER BY name")
        if employees_df.empty:
            st.info("No employees yet.")
        else:
            employee_map = {row["name"]: int(row["id"]) for _, row in employees_df.iterrows()}
            selected_employee = st.selectbox("Select Employee", list(employee_map.keys()), key="remove_employee_select")
            selected_id = employee_map[selected_employee]

            col1, col2 = st.columns(2)
            if col1.button("Deactivate Employee"):
                execute("UPDATE employees SET status = 'Inactive' WHERE id = ?", (selected_id,))
                # If this employee has a login, disable that login as well.
                if has_related_records("app_users", "employee_id", selected_id):
                    execute("UPDATE app_users SET active = 0 WHERE employee_id = ?", (selected_id,))
                st.success("Employee marked Inactive.")
                refresh()

            if col2.button("Delete Employee"):
                result = delete_employee_and_linked_users(selected_id)

                if result["deleted_users"]:
                    st.success(f"Deleted {result['deleted_users']} linked user login account(s).")

                if result["deleted_employee"]:
                    st.success(f"Deleted {result['deleted_employee']} employee record(s).")

                if result["deactivated_employee"]:
                    st.info(f"Marked {result['deactivated_employee']} employee(s) as Inactive because they had job history or protected linked records.")

                if result["skipped"]:
                    st.warning(f"Skipped {result['skipped']} item(s).")

                with st.expander("Employee delete details"):
                    for msg in result["messages"]:
                        st.write(msg)

                refresh()

    with tab_list:
        st.subheader("Employee List")

        show_inactive_workers = st.checkbox(
            "Show inactive workers",
            value=False,
            key="show_inactive_workers_employee_list"
        )

        if show_inactive_workers:
            df = df_query("""
                SELECT id AS 'ID',
                       name AS 'Employee',
                       role AS 'Role',
                       phone AS 'Phone',
                       base_hourly_rate AS 'Base Rate',
                       rate_plus_10 AS 'Rate + 10%',
                       status AS 'Status',
                       notes AS 'Notes'
                FROM employees
                ORDER BY status, name
            """)
        else:
            df = df_query("""
                SELECT id AS 'ID',
                       name AS 'Employee',
                       role AS 'Role',
                       phone AS 'Phone',
                       base_hourly_rate AS 'Base Rate',
                       rate_plus_10 AS 'Rate + 10%',
                       status AS 'Status',
                       notes AS 'Notes'
                FROM employees
                WHERE status = 'Active'
                ORDER BY name
            """)

        if df.empty:
            if show_inactive_workers:
                st.info("No employees found.")
            else:
                st.info("No active employees found. Tick 'Show inactive workers' to view inactive records.")
        else:
            st.dataframe(df, width="stretch", hide_index=True)

            st.markdown("### Remove Multiple Employees")
            st.warning(
                "This deletes the selected employee and linked user login account where safe. "
                "If an employee has wages or timesheets, the linked login will be deleted and the employee will be marked Inactive instead."
            )

            employee_delete_options = {
                f"{row['Employee']} | {row['Role'] or 'No Role'} | {row['Status']} | ID {row['ID']}": int(row["ID"])
                for _, row in df.iterrows()
            }

            selected_employee_labels = st.multiselect(
                "Select employees to delete or deactivate",
                list(employee_delete_options.keys()),
                key="bulk_employee_delete_multiselect"
            )

            selected_employee_ids = [employee_delete_options[label] for label in selected_employee_labels]

            if selected_employee_ids:
                selected_preview = df[df["ID"].astype(int).isin(selected_employee_ids)]
                st.markdown("Selected employees:")
                st.dataframe(selected_preview, width="stretch", hide_index=True)

            employee_bulk_confirm = st.text_input(
                "To delete/deactivate the selected employees, type: DELETE EMPLOYEES",
                key="bulk_employee_delete_confirm"
            )

            if st.button("Delete / Deactivate Selected Employees", key="bulk_employee_delete_button"):
                if not selected_employee_ids:
                    st.error("Select at least one employee first.")
                elif employee_bulk_confirm.strip().upper() != "DELETE EMPLOYEES":
                    st.error("Type DELETE EMPLOYEES exactly before continuing.")
                else:
                    result = delete_or_deactivate_selected_employees(selected_employee_ids)

                    if result["deleted_users"]:
                        st.success(f"Deleted {result['deleted_users']} linked user login account(s).")

                    if result["deleted_employee"]:
                        st.success(f"Deleted {result['deleted_employee']} employee record(s).")

                    if result["deactivated_employee"]:
                        st.info(f"Marked {result['deactivated_employee']} employee(s) as Inactive because they had job history or protected linked records.")

                    if result["skipped"]:
                        st.warning(f"Skipped {result['skipped']} item(s).")

                    with st.expander("Employee delete/deactivate details"):
                        for msg in result["messages"]:
                            st.write(msg)

                    refresh()


# =============================
# PRODUCTS
# =============================
elif menu == "Products":
    st.header("Products")

    with st.expander("Add / Update Product", expanded=True):
        with st.form("product_form"):
            col1, col2 = st.columns(2)
            code = col1.text_input("Product Code")
            product_name = col2.text_input("Product Name")
            col3, col4, col5 = st.columns(3)
            supplier = col3.text_input("Supplier")
            unit = col4.text_input("Unit")
            price = col5.number_input("Price Ex GST", min_value=0.0, step=1.0)
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Product")

            if submitted and code:
                execute("""
                    INSERT OR REPLACE INTO products
                    (product_code, product_name, supplier, unit, price_ex_gst, notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (code, product_name, supplier, unit, price, notes))
                st.success(f"Saved product {code}")
                refresh()

    df = df_query("""
        SELECT product_code AS 'Product Code',
               product_name AS 'Product Name',
               supplier AS 'Supplier',
               unit AS 'Unit',
               price_ex_gst AS 'Price Ex GST',
               notes AS 'Notes'
        FROM products
        ORDER BY product_code
    """)
    st.dataframe(df, width="stretch", hide_index=True)


# =============================
# MATERIAL COSTS
# =============================
elif menu == "Material Costs":
    st.header("Material Costs")
    st.caption("Select a product code or product name and see exactly what it matches before saving it to the job.")

    job_options = get_job_options()
    product_code_options = get_product_options()
    product_name_options = get_product_name_options()

    if not job_options or not product_code_options:
        st.info("Create jobs and products first.")
    else:
        with st.expander("Add Material Entry", expanded=True):
            job_label = st.selectbox("Job", list(job_options.keys()), key="material_job_select")

            product_search_type = st.radio(
                "Select product by",
                ["Product Code", "Product Name"],
                horizontal=True,
                key="material_product_search_type",
            )

            if product_search_type == "Product Code":
                selected_product = st.selectbox(
                    "Product Code",
                    list(product_code_options.keys()),
                    key="material_product_code_select",
                )
                product_id = product_code_options[selected_product]
            else:
                selected_product = st.selectbox(
                    "Product Name",
                    list(product_name_options.keys()),
                    key="material_product_name_select",
                )
                product_id = product_name_options[selected_product]

            product = df_query("""
                SELECT id, product_code, product_name, supplier, unit, price_ex_gst, notes
                FROM products
                WHERE id = ?
            """, (product_id,))

            matched_code = ""
            matched_name = ""
            matched_supplier = ""
            matched_unit = ""
            matched_price = 0.0
            matched_notes = ""

            if not product.empty:
                product_row = product.iloc[0]
                matched_code = str(product_row["product_code"] or "")
                matched_name = str(product_row["product_name"] or "")
                matched_supplier = str(product_row["supplier"] or "")
                matched_unit = str(product_row["unit"] or "")
                matched_price = float(product_row["price_ex_gst"] or 0)
                matched_notes = str(product_row["notes"] or "")

                st.success(f"Selected product matches: {matched_code} — {matched_name}")

                match_cols = st.columns(5)
                match_cols[0].metric("Code", matched_code)
                match_cols[1].metric("Product", matched_name[:28] + ("..." if len(matched_name) > 28 else ""))
                match_cols[2].metric("Supplier", matched_supplier[:18] + ("..." if len(matched_supplier) > 18 else ""))
                match_cols[3].metric("Unit", matched_unit)
                match_cols[4].metric("Unit Ex GST", f"${matched_price:,.2f}")

                with st.expander("View full matched product details"):
                    st.write({
                        "Product Code": matched_code,
                        "Product Name": matched_name,
                        "Supplier": matched_supplier,
                        "Unit": matched_unit,
                        "Price Ex GST": f"${matched_price:,.2f}",
                        "Notes": matched_notes,
                    })

            with st.form("material_form"):
                st.markdown("#### Save Material Entry")
                st.caption(f"This entry will be saved against **{job_label}** using **{matched_code} — {matched_name}**.")

                col1, col2, col3 = st.columns(3)
                qty_required = col1.number_input("Qty Required", min_value=0.0, step=1.0)
                qty_received = col2.number_input("Qty Received", min_value=0.0, step=1.0)
                date_ordered = col3.text_input("Date Ordered", value=str(date.today()))

                estimated_total = float(qty_required or 0) * float(matched_price or 0)
                st.info(f"Estimated material cost ex GST: ${estimated_total:,.2f}")

                supplier = st.text_input("Supplier Override", value=matched_supplier)
                notes = st.text_area("Notes")
                submitted = st.form_submit_button("Save Material Entry")

                if submitted:
                    execute("""
                        INSERT INTO material_entries
                        (job_id, product_id, qty_required, qty_received, date_ordered, supplier, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (job_options[job_label], product_id, qty_required, qty_received, date_ordered, supplier, notes))
                    st.success("Material entry saved.")
                    refresh()

    df = df_query("""
        SELECT m.id AS 'ID',
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               p.product_code AS 'Product Code',
               p.product_name AS 'Product Name',
               p.supplier AS 'Supplier',
               p.price_ex_gst AS 'Unit Price',
               m.qty_required AS 'Qty Required',
               m.qty_received AS 'Qty Received',
               ROUND(p.price_ex_gst * m.qty_required, 2) AS 'Total Cost',
               m.date_ordered AS 'Date Ordered',
               m.notes AS 'Notes'
        FROM material_entries m
        JOIN jobs j ON j.id = m.job_id
        JOIN products p ON p.id = m.product_id
        ORDER BY m.id DESC
    """)
    st.dataframe(df, width="stretch", hide_index=True)

    st.markdown("### Delete Material Cost Entries")
    st.caption("Use this for wrong, duplicate or accidental material cost entries. This deletes saved material cost rows only; it does not delete the product from the product list.")

    if df.empty:
        st.info("No material cost entries to delete.")
    else:
        material_options = {
            f"ID {row['ID']} | {row['Job No']} - {row['Job Name']} | {row['Product Code']} | {row['Product Name']} | Qty {row['Qty Required']} | ${float(row['Total Cost'] or 0):,.2f}": int(row["ID"])
            for _, row in df.iterrows()
        }

        selected_material_labels = st.multiselect(
            "Select material cost entries to delete",
            list(material_options.keys()),
            key="delete_material_entries_select"
        )
        selected_material_ids = [material_options[label] for label in selected_material_labels]

        delete_materials_confirm = st.text_input(
            "To delete selected material cost entries, type: DELETE MATERIALS",
            key="delete_material_entries_confirm"
        )

        if st.button("Delete Selected Material Cost Entries", key="delete_material_entries_button"):
            if not selected_material_ids:
                st.error("Select at least one material cost entry first.")
            elif delete_materials_confirm.strip().upper() != "DELETE MATERIALS":
                st.error("Type DELETE MATERIALS exactly before deleting material entries.")
            else:
                for material_id in selected_material_ids:
                    execute("DELETE FROM material_entries WHERE id = ?", (int(material_id),))
                st.success(f"Deleted {len(selected_material_ids)} material cost entr{'y' if len(selected_material_ids) == 1 else 'ies'}.")
                refresh()

    st.divider()

    imported_df = df_query("""
        SELECT im.id AS 'ID',
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               im.product AS 'Product',
               im.colour AS 'Colour',
               im.qty_required AS 'Qty Required',
               im.qty_loaded AS 'Qty Loaded',
               im.source_file AS 'Source File',
               im.imported_at AS 'Imported At',
               im.notes AS 'Notes'
        FROM imported_material_entries im
        JOIN jobs j ON j.id = im.job_id
        ORDER BY im.id DESC
    """)

    st.markdown("### Imported PDF Checklist Material Lines")
    if imported_df.empty:
        st.info("No imported PDF material lines saved.")
    else:
        st.dataframe(imported_df, width="stretch", hide_index=True)

        st.markdown("### Delete Imported PDF Material Lines")
        st.caption("Use this for wrongly imported PDF checklist material lines.")

        imported_options = {
            f"ID {row['ID']} | {row['Job No']} - {row['Job Name']} | {row['Product']} | Colour {row['Colour']} | Qty {row['Qty Required']}": int(row["ID"])
            for _, row in imported_df.iterrows()
        }

        selected_imported_labels = st.multiselect(
            "Select imported PDF material lines to delete",
            list(imported_options.keys()),
            key="delete_imported_material_entries_select"
        )
        selected_imported_ids = [imported_options[label] for label in selected_imported_labels]

        delete_imported_confirm = st.text_input(
            "To delete selected imported PDF material lines, type: DELETE IMPORTED MATERIALS",
            key="delete_imported_material_entries_confirm"
        )

        if st.button("Delete Selected Imported PDF Material Lines", key="delete_imported_material_entries_button"):
            if not selected_imported_ids:
                st.error("Select at least one imported PDF material line first.")
            elif delete_imported_confirm.strip().upper() != "DELETE IMPORTED MATERIALS":
                st.error("Type DELETE IMPORTED MATERIALS exactly before deleting imported material lines.")
            else:
                for imported_id in selected_imported_ids:
                    execute("DELETE FROM imported_material_entries WHERE id = ?", (int(imported_id),))
                st.success(f"Deleted {len(selected_imported_ids)} imported PDF material line{'s' if len(selected_imported_ids) != 1 else ''}.")
                refresh()


# =============================
# WAGES
# =============================
elif menu == "Wages":
    st.header("Wages")

    job_options = get_job_options()
    employee_options = get_employee_options(active_only=True)

    if not job_options or not employee_options:
        st.info("Create jobs and active employees first.")
    else:
        with st.expander("Add Wage Entry", expanded=True):
            with st.form("wage_form"):
                job_label = st.selectbox("Job", list(job_options.keys()))
                employee_name = st.selectbox("Employee", list(employee_options.keys()))
                employee_id = employee_options[employee_name]

                employee = df_query("SELECT base_hourly_rate, rate_plus_10 FROM employees WHERE id = ?", (employee_id,))
                if not employee.empty:
                    st.info(
                        f"Base Rate: ${float(employee.iloc[0]['base_hourly_rate'] or 0):.2f} | "
                        f"Rate + 10%: ${float(employee.iloc[0]['rate_plus_10'] or 0):.2f}"
                    )

                col1, col2 = st.columns(2)
                work_date = col1.text_input("Date", value=str(date.today()))
                hours = col2.number_input("Hours", min_value=0.0, step=0.5)
                notes = st.text_area("Notes")
                submitted = st.form_submit_button("Save Wage Entry")

                if submitted:
                    execute("""
                        INSERT INTO wage_entries
                        (job_id, employee_id, work_date, hours, notes)
                        VALUES (?, ?, ?, ?, ?)
                    """, (job_options[job_label], employee_id, work_date, hours, notes))
                    st.success("Wage entry saved.")
                    refresh()

    df = df_query("""
        SELECT w.id AS 'ID',
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               e.name AS 'Employee',
               w.work_date AS 'Date',
               w.hours AS 'Hours',
               e.base_hourly_rate AS 'Base Rate',
               e.rate_plus_10 AS 'Rate + 10%',
               ROUND(w.hours * e.rate_plus_10, 2) AS 'Total Wage Cost',
               w.notes AS 'Notes'
        FROM wage_entries w
        JOIN jobs j ON j.id = w.job_id
        JOIN employees e ON e.id = w.employee_id
        ORDER BY w.work_date DESC, w.id DESC
    """)
    st.dataframe(df, width="stretch", hide_index=True)

    st.markdown("### Delete Wage Entries")
    st.caption("Use this for wrong duplicate or accidental wage entries. This deletes wage rows only; it does not delete any timesheet record.")
    if df.empty:
        st.info("No wage entries to delete.")
    else:
        wage_options = {
            f"ID {row['ID']} | {row['Date']} | {row['Employee']} | {row['Job No']} - {row['Job Name']} | {row['Hours']} hrs | ${float(row['Total Wage Cost'] or 0):,.2f}": int(row["ID"])
            for _, row in df.iterrows()
        }
        selected_wage_labels = st.multiselect(
            "Select wage entries to delete",
            list(wage_options.keys()),
            key="delete_wage_entries_select"
        )
        selected_wage_ids = [wage_options[label] for label in selected_wage_labels]

        delete_wages_confirm = st.text_input(
            "To delete the selected wage entries, type: DELETE WAGES",
            key="delete_wage_entries_confirm"
        )

        if st.button("Delete Selected Wage Entries", key="delete_wage_entries_button"):
            if not selected_wage_ids:
                st.error("Select at least one wage entry first.")
            elif delete_wages_confirm.strip().upper() != "DELETE WAGES":
                st.error("Type DELETE WAGES exactly before deleting wage entries.")
            else:
                for wage_id in selected_wage_ids:
                    execute("DELETE FROM wage_entries WHERE id = ?", (int(wage_id),))
                st.success(f"Deleted {len(selected_wage_ids)} wage entr{'y' if len(selected_wage_ids) == 1 else 'ies'}.")
                refresh()


# =============================
# EQUIPMENT CHECKLIST
# =============================
elif menu == "Timesheets":
    timesheets_page(employee_restricted=False)


elif menu == "Equipment":
    st.header("Equipment")

    job_options = get_job_options()

    tab_import, tab_checklist, tab_master, tab_saved, tab_items = st.tabs(
        ["Import Filled PDF Checklist", "Job Equipment Checklist", "Job Equipment Master List", "All Saved Equipment", "Manage Checklist Items"]
    )

    with tab_import:
        st.subheader("Import Filled Master Site Checklist PDF")
        st.caption("Upload the completed fillable PDF checklist and assign it to the correct job. Imported quantities will save to that selected job only.")

        if not job_options:
            st.info("Create a job first, then import the checklist.")
        else:
            uploaded_checklist = st.file_uploader("Upload completed Master Site Checklist PDF", type=["pdf"])

            if uploaded_checklist is not None:
                try:
                    job_info, import_equipment_df, import_materials_df = parse_master_checklist_pdf(uploaded_checklist)

                    st.markdown("### Details found in PDF")
                    preview_details = pd.DataFrame([job_info])
                    st.dataframe(preview_details, width="stretch", hide_index=True)

                    suggested_job = None
                    if job_info.get("job_number"):
                        for label in job_options:
                            if label.startswith(job_info["job_number"]):
                                suggested_job = label
                                break
                    if suggested_job is None and job_info.get("job_name"):
                        for label in job_options:
                            if job_info["job_name"].lower() in label.lower():
                                suggested_job = label
                                break

                    job_labels = list(job_options.keys())
                    default_index = job_labels.index(suggested_job) if suggested_job in job_labels else 0

                    selected_import_job = st.selectbox(
                        "Import this checklist against job",
                        job_labels,
                        index=default_index,
                        key="pdf_import_job_select"
                    )

                    update_job = st.checkbox("Update job details from the PDF where provided", value=True)
                    replace_materials = st.checkbox("Replace existing imported PDF material lines for this job", value=True)

                    st.markdown("### Equipment / Consumables found")
                    if import_equipment_df.empty:
                        st.info("No equipment or consumable quantities found in the PDF.")
                    else:
                        st.dataframe(import_equipment_df, width="stretch", hide_index=True)

                    st.markdown("### Paint & Materials Register found")
                    if import_materials_df.empty:
                        st.info("No paint/material register lines found in the PDF.")
                    else:
                        st.dataframe(import_materials_df, width="stretch", hide_index=True)

                    if st.button("Import Checklist Into Selected Job"):
                        equipment_count, material_count = import_master_checklist_to_job(
                            job_id=job_options[selected_import_job],
                            job_info=job_info,
                            equipment_df=import_equipment_df,
                            materials_df=import_materials_df,
                            source_file=uploaded_checklist.name,
                            update_job=update_job,
                            replace_imported_materials=replace_materials,
                        )

                        st.success(
                            f"Imported checklist into {selected_import_job}. "
                            f"Equipment/consumable lines saved: {equipment_count}. "
                            f"Paint/material lines saved: {material_count}."
                        )
                        st.info("You can now view this under Job Equipment Master List and Reports / Export > Job Pack by Job.")
                        refresh()

                except Exception as e:
                    st.error(f"Could not import this PDF checklist: {e}")


    with tab_checklist:
        st.subheader("Fill Out Equipment Checklist")
        if not job_options:
            st.info("Create a job first.")
        else:
            selected_job_label = st.selectbox("Select Job", list(job_options.keys()), key="equipment_job")
            selected_job_id = job_options[selected_job_label]

            items_df = df_query("""
                SELECT id, category, item_name, default_qty, notes
                FROM equipment_checklist_items
                ORDER BY category, item_name
            """)

            existing_df = df_query("""
                SELECT *
                FROM equipment_checklist_records
                WHERE job_id = ?
            """, (selected_job_id,))

            existing_by_item = {}
            if not existing_df.empty:
                existing_by_item = {int(row["checklist_item_id"]): row for _, row in existing_df.iterrows()}

            st.caption("This checklist saves directly against the selected job. The Job Equipment Master List totals everything for that same job.")

            with st.form("equipment_checklist_form"):
                save_rows = []

                categories = list(items_df["category"].dropna().unique())

                for category in categories:
                    st.markdown(f"### {category}")
                    category_items = items_df[items_df["category"] == category]

                    for _, item in category_items.iterrows():
                        item_id = int(item["id"])
                        existing = existing_by_item.get(item_id)

                        item_name = str(item["item_name"])
                        default_qty = float(item["default_qty"] or 0)

                        req_default = bool(existing["is_required"]) if existing is not None else False
                        packed_default = bool(existing["is_packed"]) if existing is not None else False
                        returned_default = bool(existing["is_returned"]) if existing is not None else False
                        qty_req_default = float(existing["qty_required"] or default_qty) if existing is not None else default_qty
                        qty_taken_default = float(existing["qty_taken"] or 0) if existing is not None else 0.0
                        qty_returned_default = float(existing["qty_returned"] or 0) if existing is not None else 0.0

                        cols = st.columns([3, 1, 1, 1, 1, 1])
                        required = cols[0].checkbox(item_name, value=req_default, key=f"required_{selected_job_id}_{item_id}")
                        qty_required = cols[1].number_input("Req", min_value=0.0, value=qty_req_default, step=1.0, key=f"qty_required_{selected_job_id}_{item_id}")
                        qty_taken = cols[2].number_input("Out", min_value=0.0, value=qty_taken_default, step=1.0, key=f"qty_taken_{selected_job_id}_{item_id}")
                        qty_returned = cols[3].number_input("Back", min_value=0.0, value=qty_returned_default, step=1.0, key=f"qty_returned_{selected_job_id}_{item_id}")
                        packed = cols[4].checkbox("Packed", value=packed_default, key=f"packed_{selected_job_id}_{item_id}")
                        returned = cols[5].checkbox("Returned", value=returned_default, key=f"returned_{selected_job_id}_{item_id}")

                        save_rows.append({
                            "job_id": selected_job_id,
                            "item_id": item_id,
                            "qty_required": qty_required,
                            "qty_taken": qty_taken,
                            "qty_returned": qty_returned,
                            "is_required": 1 if required else 0,
                            "is_packed": 1 if packed else 0,
                            "is_returned": 1 if returned else 0,
                        })

                st.markdown("### Sign Out / Return Details")
                col_a, col_b, col_c, col_d = st.columns(4)
                date_out = col_a.text_input("Date Out", value=str(date.today()))
                date_in = col_b.text_input("Date In")
                taken_by = col_c.text_input("Taken By")
                returned_by = col_d.text_input("Returned By")

                col_e, col_f = st.columns(2)
                condition_out = col_e.text_input("Condition Out")
                condition_in = col_f.text_input("Condition In")
                notes = st.text_area("Notes")

                submitted = st.form_submit_button("Save Equipment Checklist to Job")

                if submitted:
                    for row in save_rows:
                        should_save = (
                            row["is_required"] == 1
                            or row["is_packed"] == 1
                            or row["is_returned"] == 1
                            or row["qty_taken"] > 0
                            or row["qty_returned"] > 0
                        )

                        existing = df_query("""
                            SELECT id FROM equipment_checklist_records
                            WHERE job_id = ? AND checklist_item_id = ?
                            ORDER BY id ASC
                        """, (row["job_id"], row["item_id"]))

                        if should_save:
                            if existing.empty:
                                execute("""
                                    INSERT INTO equipment_checklist_records
                                    (job_id, checklist_item_id, qty_required, qty_taken, qty_returned,
                                     is_required, is_packed, is_returned, date_out, date_in, taken_by, returned_by,
                                     condition_out, condition_in, notes)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """, (
                                    row["job_id"], row["item_id"], row["qty_required"], row["qty_taken"], row["qty_returned"],
                                    row["is_required"], row["is_packed"], row["is_returned"], date_out, date_in, taken_by, returned_by,
                                    condition_out, condition_in, notes
                                ))
                            else:
                                keep_id = int(existing.iloc[0]["id"])
                                execute("""
                                    UPDATE equipment_checklist_records
                                    SET qty_required = ?, qty_taken = ?, qty_returned = ?,
                                        is_required = ?, is_packed = ?, is_returned = ?,
                                        date_out = ?, date_in = ?, taken_by = ?, returned_by = ?,
                                        condition_out = ?, condition_in = ?, notes = ?
                                    WHERE id = ?
                                """, (
                                    row["qty_required"], row["qty_taken"], row["qty_returned"],
                                    row["is_required"], row["is_packed"], row["is_returned"],
                                    date_out, date_in, taken_by, returned_by,
                                    condition_out, condition_in, notes, keep_id
                                ))

                                # Remove duplicates if an older database allowed them
                                for dup_id in list(existing["id"])[1:]:
                                    execute("DELETE FROM equipment_checklist_records WHERE id = ?", (int(dup_id),))
                        else:
                            if not existing.empty:
                                for old_id in list(existing["id"]):
                                    execute("DELETE FROM equipment_checklist_records WHERE id = ?", (int(old_id),))

                    st.success("Equipment checklist saved to the selected job.")
                    refresh()

    with tab_master:
        st.subheader("Job Equipment Master List")
        if not job_options:
            st.info("Create a job first.")
        else:
            selected_job_label = st.selectbox("Select Job for Master List", list(job_options.keys()), key="equipment_master_job")
            selected_job_id = job_options[selected_job_label]

            master_df = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       i.category AS 'Category',
                       i.item_name AS 'Equipment Item',
                       COALESCE(SUM(r.qty_required), 0) AS 'Total Required',
                       COALESCE(SUM(r.qty_taken), 0) AS 'Total Taken',
                       COALESCE(SUM(r.qty_returned), 0) AS 'Total Returned',
                       COALESCE(SUM(r.qty_taken - r.qty_returned), 0) AS 'Still Out',
                       COALESCE(MAX(r.date_out), '') AS 'Last Date Out',
                       COALESCE(MAX(r.date_in), '') AS 'Last Date In',
                       COALESCE(MAX(r.taken_by), '') AS 'Taken By',
                       COALESCE(MAX(r.returned_by), '') AS 'Returned By',
                       COALESCE(MAX(r.notes), '') AS 'Notes'
                FROM equipment_checklist_items i
                CROSS JOIN jobs j
                LEFT JOIN equipment_checklist_records r
                    ON r.checklist_item_id = i.id
                   AND r.job_id = j.id
                WHERE j.id = ?
                GROUP BY j.job_no, j.job_name, i.category, i.item_name
                ORDER BY i.category, i.item_name
            """, (selected_job_id,))

            if master_df.empty:
                st.info("No equipment checklist has been saved for this job yet.")
            else:
                st.dataframe(master_df, width="stretch", hide_index=True)

                total_taken = float(master_df["Total Taken"].fillna(0).sum())
                total_returned = float(master_df["Total Returned"].fillna(0).sum())
                still_out = float(master_df["Still Out"].fillna(0).sum())

                c1, c2, c3 = st.columns(3)
                c1.metric("Total Items Taken", total_taken)
                c2.metric("Total Items Returned", total_returned)
                c3.metric("Total Still Out", still_out)

                st.download_button(
                    "Download this Job Equipment Master List CSV",
                    data=master_df.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"equipment_master_list_{selected_job_label.split(' - ')[0]}.csv",
                    mime="text/csv",
                )

    with tab_saved:
        st.subheader("All Saved Equipment Checklist Records")
        all_df = df_query("""
            SELECT r.id AS 'Record ID',
                   j.job_no AS 'Job No',
                   j.job_name AS 'Job Name',
                   i.category AS 'Category',
                   i.item_name AS 'Equipment Item',
                   r.qty_required AS 'Qty Required',
                   r.qty_taken AS 'Qty Taken',
                   r.qty_returned AS 'Qty Returned',
                   CASE WHEN r.is_required = 1 THEN 'Yes' ELSE '' END AS 'Required',
                   CASE WHEN r.is_packed = 1 THEN 'Yes' ELSE '' END AS 'Packed',
                   CASE WHEN r.is_returned = 1 THEN 'Yes' ELSE '' END AS 'Returned',
                   r.date_out AS 'Date Out',
                   r.date_in AS 'Date In',
                   r.taken_by AS 'Taken By',
                   r.returned_by AS 'Returned By',
                   r.condition_out AS 'Condition Out',
                   r.condition_in AS 'Condition In',
                   r.notes AS 'Notes'
            FROM equipment_checklist_records r
            JOIN jobs j ON j.id = r.job_id
            JOIN equipment_checklist_items i ON i.id = r.checklist_item_id
            ORDER BY j.job_no, i.category, i.item_name
        """)
        if all_df.empty:
            st.info("No saved equipment records yet.")
        else:
            st.dataframe(all_df.drop(columns=["Record ID"]), width="stretch", hide_index=True)

            with st.expander("Delete Saved Equipment Line"):
                delete_map = {
                    f"{row['Job No']} - {row['Equipment Item']}": int(row["Record ID"])
                    for _, row in all_df.iterrows()
                }
                selected = st.selectbox("Select line to delete", list(delete_map.keys()))
                if st.button("Delete Selected Equipment Line"):
                    execute("DELETE FROM equipment_checklist_records WHERE id = ?", (delete_map[selected],))
                    st.success("Equipment line deleted.")
                    refresh()

    with tab_items:
        st.subheader("Manage Checklist Items")
        with st.form("add_equipment_item_form"):
            col1, col2, col3 = st.columns(3)
            category = col1.text_input("Category")
            item_name = col2.text_input("Equipment Item")
            default_qty = col3.number_input("Default Qty", min_value=0.0, step=1.0, value=0.0)
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Checklist Item")

            if submitted and item_name:
                execute("""
                    INSERT OR REPLACE INTO equipment_checklist_items
                    (category, item_name, default_qty, notes)
                    VALUES (?, ?, ?, ?)
                """, (category, item_name, default_qty, notes))
                st.success(f"Saved checklist item: {item_name}")
                refresh()

        items_df = df_query("""
            SELECT id,
                   category AS 'Category',
                   item_name AS 'Equipment Item',
                   default_qty AS 'Default Qty',
                   notes AS 'Notes'
            FROM equipment_checklist_items
            ORDER BY category, item_name
        """)
        st.dataframe(items_df.drop(columns=["id"]) if not items_df.empty else items_df, width="stretch", hide_index=True)


# =============================
# REPORTS
# =============================
elif menu == "Job Photos":
    job_photos_page(employee_restricted=False)


elif menu == "Reports / Export":
    st.header("Reports / Export")

    tab_job_pack, tab_reports = st.tabs(["Job Pack by Job", "General Reports"])

    with tab_job_pack:
        st.subheader("Produce Full Job Pack")

        job_options = get_job_options()

        if not job_options:
            st.info("No jobs found. Create a job first.")
        else:
            selected_job_label = st.selectbox(
                "Select Job Number / Job Name",
                list(job_options.keys()),
                key="job_pack_selector"
            )
            selected_job_id = job_options[selected_job_label]

            job_details = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       bc.name AS 'Builder / Client',
                       bc.contact_name AS 'Contact',
                       bc.phone AS 'Phone',
                       bc.email AS 'Email',
                       bc.terms AS 'Terms',
                       bc.qbcc AS 'Builder QBCC',
                       bc.abn AS 'Builder ABN',
                       j.site_address AS 'Site Address',
                       j.status AS 'Status',
                       j.leading_hand AS 'Leading Hand',
                       j.start_date AS 'Start Date',
                       j.end_date AS 'End Date',
                       j.contract_value AS 'Contract Value',
                       j.notes AS 'Notes'
                FROM jobs j
                LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
                WHERE j.id = ?
            """, (selected_job_id,))

            material_details = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       p.product_code AS 'Product Code',
                       p.product_name AS 'Product Name',
                       p.supplier AS 'Supplier',
                       p.unit AS 'Unit',
                       p.price_ex_gst AS 'Unit Price Ex GST',
                       m.qty_required AS 'Qty Required',
                       m.qty_received AS 'Qty Received',
                       ROUND(p.price_ex_gst * m.qty_required, 2) AS 'Total Cost Ex GST',
                       m.date_ordered AS 'Date Ordered',
                       m.supplier AS 'Supplier Override',
                       m.notes AS 'Notes'
                FROM material_entries m
                JOIN jobs j ON j.id = m.job_id
                JOIN products p ON p.id = m.product_id
                WHERE j.id = ?
                ORDER BY m.id ASC
            """, (selected_job_id,))

            estimate_summary = df_query("""
                SELECT e.estimate_no AS 'Estimate No',
                       e.revision AS 'Revision',
                       e.estimate_date AS 'Date',
                       e.status AS 'Status',
                       e.labour_hours AS 'Labour Hours',
                       e.labour_rate AS 'Labour Rate',
                       e.material_allowance AS 'Material Allowance',
                       e.access_equipment_allowance AS 'Access / Equipment',
                       e.subcontractor_allowance AS 'Subcontractor',
                       e.sundries_allowance AS 'Sundries',
                       e.margin_percent AS 'Margin %',
                       e.contingency_percent AS 'Contingency %',
                       e.total_ex_gst AS 'Total Ex GST',
                       e.gst_amount AS 'GST',
                       e.total_inc_gst AS 'Total Inc GST',
                       e.notes AS 'Notes'
                FROM estimate_working_sheets e
                WHERE e.job_id = ?
                ORDER BY e.id DESC
            """, (selected_job_id,))

            estimate_lines = df_query("""
                SELECT e.estimate_no AS 'Estimate No',
                       l.section AS 'Section',
                       l.item_description AS 'Description',
                       l.qty AS 'Qty',
                       l.unit AS 'Unit',
                       l.unit_rate AS 'Unit Rate',
                       l.line_total AS 'Line Total',
                       l.notes AS 'Notes'
                FROM estimate_line_items l
                JOIN estimate_working_sheets e ON e.id = l.estimate_id
                WHERE e.job_id = ?
                ORDER BY e.id DESC, l.id ASC
            """, (selected_job_id,))

            timesheet_details = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       e.name AS 'Employee',
                       t.work_date AS 'Date',
                       t.start_time AS 'Start',
                       t.finish_time AS 'Finish',
                       t.break_minutes AS 'Break Minutes',
                       t.total_hours AS 'Hours',
                       t.work_type AS 'Work Type',
                       t.status AS 'Status',
                       t.notes AS 'Notes'
                FROM timesheet_entries t
                JOIN jobs j ON j.id = t.job_id
                JOIN employees e ON e.id = t.employee_id
                WHERE j.id = ?
                ORDER BY t.work_date ASC, e.name ASC
            """, (selected_job_id,))

            wage_details = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       e.name AS 'Employee',
                       w.work_date AS 'Date',
                       w.hours AS 'Hours',
                       e.base_hourly_rate AS 'Base Rate',
                       e.rate_plus_10 AS 'Rate + 10%',
                       ROUND(w.hours * e.rate_plus_10, 2) AS 'Total Wage Cost',
                       w.notes AS 'Notes'
                FROM wage_entries w
                JOIN jobs j ON j.id = w.job_id
                JOIN employees e ON e.id = w.employee_id
                WHERE j.id = ?
                ORDER BY w.work_date ASC, e.name ASC
            """, (selected_job_id,))

            timesheet_details = df_query("""
                SELECT j.job_no AS "Job No",
                       j.job_name AS "Job Name",
                       e.name AS "Employee",
                       t.work_date AS "Date",
                       t.start_time AS "Start",
                       t.finish_time AS "Finish",
                       t.break_minutes AS "Break Minutes",
                       t.total_hours AS "Hours",
                       t.work_type AS "Work Type",
                       t.status AS "Status",
                       t.submitted_by AS "Submitted By",
                       t.submitted_at AS "Submitted At",
                       t.notes AS "Notes"
                FROM timesheet_entries t
                JOIN jobs j ON j.id = t.job_id
                JOIN employees e ON e.id = t.employee_id
                WHERE j.id = ?
                ORDER BY t.work_date ASC, e.name ASC
            """, (selected_job_id,))

            equipment_master = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       i.category AS 'Category',
                       i.item_name AS 'Equipment Item',
                       COALESCE(SUM(r.qty_required), 0) AS 'Total Required',
                       COALESCE(SUM(r.qty_taken), 0) AS 'Total Taken',
                       COALESCE(SUM(r.qty_returned), 0) AS 'Total Returned',
                       COALESCE(SUM(r.qty_taken - r.qty_returned), 0) AS 'Still Out',
                       COALESCE(MAX(r.date_out), '') AS 'Last Date Out',
                       COALESCE(MAX(r.date_in), '') AS 'Last Date In',
                       COALESCE(MAX(r.taken_by), '') AS 'Taken By',
                       COALESCE(MAX(r.returned_by), '') AS 'Returned By',
                       COALESCE(MAX(r.condition_out), '') AS 'Condition Out',
                       COALESCE(MAX(r.condition_in), '') AS 'Condition In',
                       COALESCE(MAX(r.notes), '') AS 'Notes'
                FROM equipment_checklist_items i
                CROSS JOIN jobs j
                LEFT JOIN equipment_checklist_records r
                    ON r.checklist_item_id = i.id
                   AND r.job_id = j.id
                WHERE j.id = ?
                GROUP BY j.job_no, j.job_name, i.category, i.item_name
                ORDER BY i.category, i.item_name
            """, (selected_job_id,))

            equipment_detail = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       i.category AS 'Category',
                       i.item_name AS 'Equipment Item',
                       r.qty_required AS 'Qty Required',
                       r.qty_taken AS 'Qty Taken',
                       r.qty_returned AS 'Qty Returned',
                       CASE WHEN r.is_required = 1 THEN 'Yes' ELSE '' END AS 'Required',
                       CASE WHEN r.is_packed = 1 THEN 'Yes' ELSE '' END AS 'Packed',
                       CASE WHEN r.is_returned = 1 THEN 'Yes' ELSE '' END AS 'Returned',
                       r.date_out AS 'Date Out',
                       r.date_in AS 'Date In',
                       r.taken_by AS 'Taken By',
                       r.returned_by AS 'Returned By',
                       r.condition_out AS 'Condition Out',
                       r.condition_in AS 'Condition In',
                       r.notes AS 'Notes'
                FROM equipment_checklist_records r
                JOIN jobs j ON j.id = r.job_id
                JOIN equipment_checklist_items i ON i.id = r.checklist_item_id
                WHERE j.id = ?
                ORDER BY i.category, i.item_name
            """, (selected_job_id,))

            imported_materials = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       im.product AS 'Product',
                       im.colour AS 'Colour',
                       im.qty_required AS 'Qty Required',
                       im.qty_loaded AS 'Qty Loaded',
                       im.source_file AS 'Source File',
                       im.imported_at AS 'Imported At',
                       im.notes AS 'Notes'
                FROM imported_material_entries im
                JOIN jobs j ON j.id = im.job_id
                WHERE j.id = ?
                ORDER BY im.id ASC
            """, (selected_job_id,))

            job_photos_meta = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       jp.id AS 'Photo ID',
                       jp.photo_name AS 'Photo Name',
                       jp.category AS 'Category',
                       jp.caption AS 'Caption',
                       jp.uploaded_by AS 'Uploaded By',
                       jp.uploaded_at AS 'Uploaded At',
                       jp.notes AS 'Notes'
                FROM job_photos jp
                JOIN jobs j ON j.id = jp.job_id
                WHERE j.id = ?
                ORDER BY jp.uploaded_at DESC, jp.id DESC
            """, (selected_job_id,))

            job_photos_full = df_query("""
                SELECT id, photo_name, photo_type, photo_data, category, caption, uploaded_by, uploaded_at, notes
                FROM job_photos
                WHERE job_id = ?
                ORDER BY uploaded_at DESC, id DESC
            """, (selected_job_id,))

            material_total = float(material_details["Total Cost Ex GST"].fillna(0).sum()) if not material_details.empty else 0.0
            wage_total = float(wage_details["Total Wage Cost"].fillna(0).sum()) if not wage_details.empty else 0.0
            equipment_still_out = float(equipment_master["Still Out"].fillna(0).sum()) if not equipment_master.empty else 0.0

            col1, col2, col3 = st.columns(3)
            col1.metric("Material Cost Ex GST", f"${material_total:,.2f}")
            col2.metric("Wage Cost", f"${wage_total:,.2f}")
            col3.metric("Equipment Still Out", f"{equipment_still_out:g}")

            st.markdown("### Job Details")
            st.dataframe(job_details, width="stretch", hide_index=True)

            st.markdown("### Estimate Working Sheets for this Job")
            if estimate_summary.empty:
                st.info("No estimate working sheets saved for this job.")
            else:
                st.dataframe(estimate_summary, width="stretch", hide_index=True)

            st.markdown("### Estimate Line Items for this Job")
            if estimate_lines.empty:
                st.info("No estimate line items saved for this job.")
            else:
                st.dataframe(estimate_lines, width="stretch", hide_index=True)

            st.markdown("### Timesheets for this Job")
            if timesheet_details.empty:
                st.info("No timesheets saved for this job.")
            else:
                st.metric("Total Timesheet Hours", f"{float(timesheet_details['Hours'].fillna(0).sum()):.2f}")
                st.dataframe(timesheet_details, width="stretch", hide_index=True)

            st.markdown("### Material Costs for this Job")
            if material_details.empty:
                st.info("No material cost entries saved for this job.")
            else:
                st.dataframe(material_details, width="stretch", hide_index=True)

            st.markdown("### Imported Checklist Paint & Materials for this Job")
            if imported_materials.empty:
                st.info("No imported checklist paint/material lines saved for this job.")
            else:
                st.dataframe(imported_materials, width="stretch", hide_index=True)

            st.markdown("### Wages for this Job")
            if wage_details.empty:
                st.info("No wage entries saved for this job.")
            else:
                st.dataframe(wage_details, width="stretch", hide_index=True)

            st.markdown("### Timesheets for this Job")
            if timesheet_details.empty:
                st.info("No timesheets saved for this job.")
            else:
                st.metric("Total Timesheet Hours", f"{float(timesheet_details['Hours'].fillna(0).sum()):.2f}")
                st.dataframe(timesheet_details, width="stretch", hide_index=True)

            st.markdown("### Equipment Master List for this Job")
            if equipment_master.empty:
                st.info("No equipment checklist entries saved for this job.")
            else:
                st.dataframe(equipment_master, width="stretch", hide_index=True)

            st.markdown("### Equipment Checklist Detail for this Job")
            if equipment_detail.empty:
                st.info("No equipment checklist detail saved for this job.")
            else:
                st.dataframe(equipment_detail, width="stretch", hide_index=True)

            st.markdown("### Job Photos for this Job")
            if job_photos_meta.empty:
                st.info("No photos saved for this job.")
            else:
                st.dataframe(job_photos_meta, width="stretch", hide_index=True)

                with st.expander("View Photo Gallery"):
                    for _, photo_row in job_photos_full.iterrows():
                        title_parts = [
                            str(photo_row["category"] or ""),
                            str(photo_row["caption"] or photo_row["photo_name"] or ""),
                        ]
                        st.markdown("#### " + " - ".join([p for p in title_parts if p]))
                        try:
                            st.image(photo_data_to_bytes(photo_row["photo_data"]), width="stretch")
                        except Exception:
                            st.warning("Could not display photo.")
                        st.caption(f"Uploaded: {photo_row['uploaded_at']} by {photo_row['uploaded_by']}")

            # Create a full Excel job pack with one sheet per document/report
            output = BytesIO()
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                job_details.to_excel(writer, index=False, sheet_name="Job Details")
                material_details.to_excel(writer, index=False, sheet_name="Materials")
                imported_materials.to_excel(writer, index=False, sheet_name="Imported Materials")
                job_photos_meta.to_excel(writer, index=False, sheet_name="Job Photos")
                timesheet_details.to_excel(writer, index=False, sheet_name="Timesheets")
                wage_details.to_excel(writer, index=False, sheet_name="Wages")
                equipment_master.to_excel(writer, index=False, sheet_name="Equipment Master")
                equipment_detail.to_excel(writer, index=False, sheet_name="Equipment Detail")

                summary_df = pd.DataFrame([
                    ["Estimate Total Ex GST", float(estimate_summary["Total Ex GST"].fillna(0).sum()) if not estimate_summary.empty else 0],
                    ["Estimate Total Inc GST", float(estimate_summary["Total Inc GST"].fillna(0).sum()) if not estimate_summary.empty else 0],
                    ["Timesheet Hours", float(timesheet_details["Hours"].fillna(0).sum()) if not timesheet_details.empty else 0],
                    ["Material Cost Ex GST", material_total],
                    ["Wage Cost", wage_total],
                    ["Equipment Still Out", equipment_still_out],
                ], columns=["Summary Item", "Value"])
                summary_df.to_excel(writer, index=False, sheet_name="Summary")

                # Basic column width clean-up
                for ws in writer.book.worksheets:
                    for column_cells in ws.columns:
                        max_len = 0
                        col_letter = column_cells[0].column_letter
                        for cell in column_cells:
                            value = "" if cell.value is None else str(cell.value)
                            max_len = max(max_len, len(value))
                        ws.column_dimensions[col_letter].width = min(max(max_len + 2, 12), 45)

            output.seek(0)

            clean_job_no = "job_pack"
            if not job_details.empty:
                clean_job_no = str(job_details.iloc[0]["Job No"]).replace("/", "-").replace("\\", "-")

            st.download_button(
                label="Download Full Job Pack Excel",
                data=output.getvalue(),
                file_name=f"{clean_job_no}_Job_Pack.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

            # Individual CSV downloads
            st.markdown("### Individual Downloads")
            d1, d2, d3, d4, d5 = st.columns(5)
            d1.download_button(
                "Materials CSV",
                data=material_details.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"{clean_job_no}_materials.csv",
                mime="text/csv",
            )
            d2.download_button(
                "Wages CSV",
                data=wage_details.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"{clean_job_no}_wages.csv",
                mime="text/csv",
            )
            d3.download_button(
                "Equipment CSV",
                data=equipment_master.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"{clean_job_no}_equipment_master.csv",
                mime="text/csv",
            )
            d4.download_button(
                "Job Details CSV",
                data=job_details.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"{clean_job_no}_job_details.csv",
                mime="text/csv",
            )
            d5.download_button(
                "Imported Materials CSV",
                data=imported_materials.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"{clean_job_no}_imported_materials.csv",
                mime="text/csv",
            )
            st.download_button(
                "Job Photos Register CSV",
                data=job_photos_meta.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"{clean_job_no}_job_photos.csv",
                mime="text/csv",
            )

    with tab_reports:
        st.subheader("General Reports")

        reports = {
            "Estimate Working Sheets": """
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       e.estimate_no AS 'Estimate No',
                       e.revision AS 'Revision',
                       e.estimate_date AS 'Date',
                       e.status AS 'Status',
                       e.total_ex_gst AS 'Total Ex GST',
                       e.gst_amount AS 'GST',
                       e.total_inc_gst AS 'Total Inc GST',
                       e.notes AS 'Notes'
                FROM estimate_working_sheets e
                JOIN jobs j ON j.id = e.job_id
                ORDER BY j.job_no, e.id DESC
            """,
            "Estimate Line Items": """
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       e.estimate_no AS 'Estimate No',
                       l.section AS 'Section',
                       l.item_description AS 'Description',
                       l.qty AS 'Qty',
                       l.unit AS 'Unit',
                       l.unit_rate AS 'Unit Rate',
                       l.line_total AS 'Line Total',
                       l.notes AS 'Notes'
                FROM estimate_line_items l
                JOIN estimate_working_sheets e ON e.id = l.estimate_id
                JOIN jobs j ON j.id = e.job_id
                ORDER BY j.job_no, e.id DESC, l.id ASC
            """,
            "Timesheets": """
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       e.name AS 'Employee',
                       t.work_date AS 'Date',
                       t.start_time AS 'Start',
                       t.finish_time AS 'Finish',
                       t.break_minutes AS 'Break Minutes',
                       t.total_hours AS 'Hours',
                       t.work_type AS 'Work Type',
                       t.status AS 'Status',
                       t.notes AS 'Notes'
                FROM timesheet_entries t
                JOIN jobs j ON j.id = t.job_id
                JOIN employees e ON e.id = t.employee_id
                ORDER BY t.work_date DESC, j.job_no, e.name
            """,
            "Archived Jobs": """
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       bc.name AS 'Builder / Client',
                       bc.contact_name AS 'Contact',
                       bc.phone AS 'Phone',
                       bc.email AS 'Email',
                       j.site_address AS 'Site Address',
                       j.status AS 'Status',
                       j.leading_hand AS 'Leading Hand',
                       j.start_date AS 'Start Date',
                       j.end_date AS 'End Date',
                       j.contract_value AS 'Contract Value',
                       j.notes AS 'Notes'
                FROM jobs j
                LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
                WHERE j.status = 'Archived'
                ORDER BY j.job_no
            """,
            "Job Register": """
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       bc.name AS 'Builder / Client',
                       bc.contact_name AS 'Contact',
                       bc.phone AS 'Phone',
                       bc.email AS 'Email',
                       j.site_address AS 'Site Address',
                       j.status AS 'Status',
                       j.leading_hand AS 'Leading Hand',
                       j.start_date AS 'Start Date',
                       j.end_date AS 'End Date',
                       j.contract_value AS 'Contract Value',
                       j.notes AS 'Notes'
                FROM jobs j
                LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
                ORDER BY j.job_no
            """,
            "Builders & Clients": "SELECT * FROM builders_clients ORDER BY name",
            "Employees": "SELECT * FROM employees ORDER BY name",
            "Products": "SELECT * FROM products ORDER BY product_code",
            "Material Costs": """
                SELECT j.job_no,
                       j.job_name,
                       p.product_code,
                       p.product_name,
                       p.price_ex_gst,
                       m.qty_required,
                       m.qty_received,
                       ROUND(p.price_ex_gst * m.qty_required, 2) AS total_cost,
                       m.date_ordered,
                       m.notes
                FROM material_entries m
                JOIN jobs j ON j.id = m.job_id
                JOIN products p ON p.id = m.product_id
                ORDER BY m.id DESC
            """,
            "Wages": """
                SELECT j.job_no,
                       j.job_name,
                       e.name AS employee,
                       w.work_date,
                       w.hours,
                       e.rate_plus_10,
                       ROUND(w.hours * e.rate_plus_10, 2) AS total_cost,
                       w.notes
                FROM wage_entries w
                JOIN jobs j ON j.id = w.job_id
                JOIN employees e ON e.id = w.employee_id
                ORDER BY w.work_date DESC
            """,
            "Equipment Master List": """
                SELECT j.job_no,
                       j.job_name,
                       i.category,
                       i.item_name,
                       COALESCE(SUM(r.qty_required), 0) AS total_required,
                       COALESCE(SUM(r.qty_taken), 0) AS total_taken,
                       COALESCE(SUM(r.qty_returned), 0) AS total_returned,
                       COALESCE(SUM(r.qty_taken - r.qty_returned), 0) AS still_out,
                       COALESCE(MAX(r.date_out), '') AS last_date_out,
                       COALESCE(MAX(r.date_in), '') AS last_date_in,
                       COALESCE(MAX(r.taken_by), '') AS taken_by,
                       COALESCE(MAX(r.returned_by), '') AS returned_by,
                       COALESCE(MAX(r.notes), '') AS notes
                FROM jobs j
                CROSS JOIN equipment_checklist_items i
                LEFT JOIN equipment_checklist_records r
                    ON r.job_id = j.id
                   AND r.checklist_item_id = i.id
                GROUP BY j.job_no, j.job_name, i.category, i.item_name
                ORDER BY j.job_no, i.category, i.item_name
            """,
            "Imported Checklist Materials": """
                SELECT j.job_no,
                       j.job_name,
                       im.product,
                       im.colour,
                       im.qty_required,
                       im.qty_loaded,
                       im.source_file,
                       im.imported_at,
                       im.notes
                FROM imported_material_entries im
                JOIN jobs j ON j.id = im.job_id
                ORDER BY j.job_no, im.id
            """,
        }

        report_name = st.selectbox("Select report", list(reports.keys()))
        report_df = df_query(reports[report_name])
        st.dataframe(report_df, width="stretch", hide_index=True)

        st.download_button(
            f"Download {report_name} CSV",
            data=report_df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{report_name.replace(' ', '_').lower()}.csv",
            mime="text/csv",
        )
