"""Database connections, schema, queries, cached lookups and starter data.

Generated from the previously working JobHub monolith.
"""

from __future__ import annotations

from .runtime import *


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
    conn = None
    try:
        conn = connect()
        cur = conn.cursor()
        cur.execute("SELECT setting_value FROM app_settings WHERE setting_key = ?", (key,))
        row = cur.fetchone()
        if row:
            return row[0]
    except Exception:
        return default
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return default

def set_app_setting(key, value):
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO app_settings (setting_key, setting_value)
            VALUES (?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value
        """, (key, value))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
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

    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn

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
    def ensure_column(table, column, definition):
        if USE_POSTGRES:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
        else:
            cur.execute(f"PRAGMA table_info({table})")
            existing_columns = [row[1] for row in cur.fetchall()]
            if column not in existing_columns:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    ensure_column("material_entries", "custom_product_code", "TEXT")
    ensure_column("material_entries", "custom_product_name", "TEXT")
    ensure_column("material_entries", "custom_supplier", "TEXT")
    ensure_column("material_entries", "custom_unit", "TEXT")
    ensure_column("material_entries", "custom_unit_price", "REAL")
    ensure_column("material_entries", "custom_colour", "TEXT")
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
    ensure_column("wage_entries", "period_type", "TEXT")
    ensure_column("wage_entries", "period_start", "TEXT")
    ensure_column("wage_entries", "period_end", "TEXT")


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
    ensure_column("timesheet_entries", "period_type", "TEXT")
    ensure_column("timesheet_entries", "period_start", "TEXT")
    ensure_column("timesheet_entries", "period_end", "TEXT")





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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS painting_takeoff_packages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        takeoff_no TEXT,
        takeoff_date TEXT,
        status TEXT DEFAULT 'Draft',
        source_documents TEXT,
        interior_total_m2 REAL DEFAULT 0,
        exterior_total_m2 REAL DEFAULT 0,
        wall_labour_hours REAL DEFAULT 0,
        ceiling_labour_hours REAL DEFAULT 0,
        woodwork_labour_hours REAL DEFAULT 0,
        feature_labour_hours REAL DEFAULT 0,
        exterior_labour_hours REAL DEFAULT 0,
        total_labour_hours REAL DEFAULT 0,
        total_paint_litres REAL DEFAULT 0,
        standard_paint_litres REAL DEFAULT 0,
        gloss_paint_litres REAL DEFAULT 0,
        generated_method TEXT,
        assumptions TEXT,
        ai_notes TEXT,
        created_by TEXT,
        created_at TEXT,
        updated_at TEXT,
        notes TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS painting_takeoff_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        package_id INTEGER NOT NULL,
        area_type TEXT,
        location_area TEXT,
        substrate TEXT,
        labour_category TEXT,
        m2 REAL DEFAULT 0,
        coats REAL DEFAULT 2,
        productivity_m2_per_hour REAL DEFAULT 8,
        labour_hours REAL DEFAULT 0,
        finish_type TEXT DEFAULT 'Standard Paint',
        element_count REAL DEFAULT 0,
        lineal_metres REAL DEFAULT 0,
        paint_litres REAL DEFAULT 0,
        flags TEXT,
        notes TEXT,
        created_at TEXT,
        FOREIGN KEY(package_id) REFERENCES painting_takeoff_packages(id)
    )
    """)

    ensure_column("painting_takeoff_packages", "wall_labour_hours", "REAL DEFAULT 0")
    ensure_column("painting_takeoff_packages", "ceiling_labour_hours", "REAL DEFAULT 0")
    ensure_column("painting_takeoff_packages", "woodwork_labour_hours", "REAL DEFAULT 0")
    ensure_column("painting_takeoff_packages", "feature_labour_hours", "REAL DEFAULT 0")
    ensure_column("painting_takeoff_packages", "exterior_labour_hours", "REAL DEFAULT 0")
    ensure_column("painting_takeoff_packages", "total_labour_hours", "REAL DEFAULT 0")
    ensure_column("painting_takeoff_packages", "source_documents", "TEXT")
    ensure_column("painting_takeoff_packages", "audit_score", "REAL DEFAULT 0")
    ensure_column("painting_takeoff_packages", "audit_notes", "TEXT")
    ensure_column("painting_takeoff_packages", "audit_at", "TEXT")
    ensure_column("painting_takeoff_packages", "total_paint_litres", "REAL DEFAULT 0")
    ensure_column("painting_takeoff_packages", "standard_paint_litres", "REAL DEFAULT 0")
    ensure_column("painting_takeoff_packages", "gloss_paint_litres", "REAL DEFAULT 0")
    ensure_column("painting_takeoff_lines", "productivity_m2_per_hour", "REAL DEFAULT 8")
    ensure_column("painting_takeoff_lines", "labour_hours", "REAL DEFAULT 0")
    ensure_column("painting_takeoff_lines", "finish_type", "TEXT DEFAULT 'Standard Paint'")
    ensure_column("painting_takeoff_lines", "element_count", "REAL DEFAULT 0")
    ensure_column("painting_takeoff_lines", "lineal_metres", "REAL DEFAULT 0")
    ensure_column("painting_takeoff_lines", "paint_litres", "REAL DEFAULT 0")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS painting_progress_sections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        package_id INTEGER,
        takeoff_line_id INTEGER,
        section_code TEXT,
        area_type TEXT,
        location_area TEXT,
        substrate TEXT,
        labour_category TEXT,
        total_m2 REAL DEFAULT 0,
        allocated_value_ex_gst REAL DEFAULT 0,
        completed_m2 REAL DEFAULT 0,
        completed_percent REAL DEFAULT 0,
        status TEXT DEFAULT 'Not Started',
        notes TEXT,
        updated_by TEXT,
        updated_at TEXT,
        created_at TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id),
        FOREIGN KEY(package_id) REFERENCES painting_takeoff_packages(id),
        FOREIGN KEY(takeoff_line_id) REFERENCES painting_takeoff_lines(id)
    )
    """)
    ensure_column("painting_progress_sections", "allocated_value_ex_gst", "REAL DEFAULT 0")
    ensure_column("painting_progress_sections", "completed_m2", "REAL DEFAULT 0")
    ensure_column("painting_progress_sections", "completed_percent", "REAL DEFAULT 0")
    ensure_column("painting_progress_sections", "status", "TEXT DEFAULT 'Not Started'")
    ensure_column("painting_progress_sections", "updated_by", "TEXT")
    ensure_column("painting_progress_sections", "updated_at", "TEXT")
    ensure_column("painting_progress_sections", "created_at", "TEXT")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_progress_sections_job_id ON painting_progress_sections(job_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_progress_sections_package_id ON painting_progress_sections(package_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_progress_sections_line_id ON painting_progress_sections(takeoff_line_id)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS building_model_surfaces (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        package_id INTEGER,
        progress_section_id INTEGER,
        takeoff_line_id INTEGER,
        section_code TEXT,
        surface_name TEXT,
        surface_type TEXT,
        elevation TEXT,
        level_name TEXT,
        x_pos REAL DEFAULT 0,
        y_pos REAL DEFAULT 0,
        z_pos REAL DEFAULT 0,
        width REAL DEFAULT 1,
        height REAL DEFAULT 1,
        depth REAL DEFAULT 0.1,
        rotation_y REAL DEFAULT 0,
        colour_hex TEXT,
        notes TEXT,
        created_at TEXT,
        updated_at TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id),
        FOREIGN KEY(package_id) REFERENCES painting_takeoff_packages(id),
        FOREIGN KEY(takeoff_line_id) REFERENCES painting_takeoff_lines(id)
    )
    """)
    ensure_column("building_model_surfaces", "job_id", "INTEGER")
    ensure_column("building_model_surfaces", "package_id", "INTEGER")
    ensure_column("building_model_surfaces", "progress_section_id", "INTEGER")
    ensure_column("building_model_surfaces", "takeoff_line_id", "INTEGER")
    ensure_column("building_model_surfaces", "section_code", "TEXT")
    ensure_column("building_model_surfaces", "surface_name", "TEXT")
    ensure_column("building_model_surfaces", "surface_type", "TEXT")
    ensure_column("building_model_surfaces", "elevation", "TEXT")
    ensure_column("building_model_surfaces", "level_name", "TEXT")
    ensure_column("building_model_surfaces", "x_pos", "REAL DEFAULT 0")
    ensure_column("building_model_surfaces", "y_pos", "REAL DEFAULT 0")
    ensure_column("building_model_surfaces", "z_pos", "REAL DEFAULT 0")
    ensure_column("building_model_surfaces", "width", "REAL DEFAULT 1")
    ensure_column("building_model_surfaces", "height", "REAL DEFAULT 1")
    ensure_column("building_model_surfaces", "depth", "REAL DEFAULT 0.1")
    ensure_column("building_model_surfaces", "rotation_y", "REAL DEFAULT 0")
    ensure_column("building_model_surfaces", "colour_hex", "TEXT")
    ensure_column("building_model_surfaces", "notes", "TEXT")
    ensure_column("building_model_surfaces", "created_at", "TEXT")
    ensure_column("building_model_surfaces", "updated_at", "TEXT")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_building_surfaces_job_id ON building_model_surfaces(job_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_building_surfaces_package_id ON building_model_surfaces(package_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_building_surfaces_progress_id ON building_model_surfaces(progress_section_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_building_surfaces_takeoff_line_id ON building_model_surfaces(takeoff_line_id)")

    # PostgreSQL requires referenced tables to exist before a foreign key is declared.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS job_documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        document_type TEXT,
        file_name TEXT,
        file_path TEXT,
        created_at TEXT,
        notes TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS drawing_progress_zones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        package_id INTEGER,
        document_id INTEGER,
        progress_section_id INTEGER,
        takeoff_line_id INTEGER,
        view_name TEXT,
        zone_name TEXT,
        x_percent REAL DEFAULT 5,
        y_percent REAL DEFAULT 5,
        width_percent REAL DEFAULT 15,
        height_percent REAL DEFAULT 10,
        colour_hex TEXT,
        notes TEXT,
        created_at TEXT,
        updated_at TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id),
        FOREIGN KEY(package_id) REFERENCES painting_takeoff_packages(id),
        FOREIGN KEY(document_id) REFERENCES job_documents(id),
        FOREIGN KEY(progress_section_id) REFERENCES painting_progress_sections(id),
        FOREIGN KEY(takeoff_line_id) REFERENCES painting_takeoff_lines(id)
    )
    """)
    ensure_column("drawing_progress_zones", "job_id", "INTEGER")
    ensure_column("drawing_progress_zones", "package_id", "INTEGER")
    ensure_column("drawing_progress_zones", "document_id", "INTEGER")
    ensure_column("drawing_progress_zones", "progress_section_id", "INTEGER")
    ensure_column("drawing_progress_zones", "takeoff_line_id", "INTEGER")
    ensure_column("drawing_progress_zones", "view_name", "TEXT")
    ensure_column("drawing_progress_zones", "zone_name", "TEXT")
    ensure_column("drawing_progress_zones", "x_percent", "REAL DEFAULT 5")
    ensure_column("drawing_progress_zones", "y_percent", "REAL DEFAULT 5")
    ensure_column("drawing_progress_zones", "width_percent", "REAL DEFAULT 15")
    ensure_column("drawing_progress_zones", "height_percent", "REAL DEFAULT 10")
    ensure_column("drawing_progress_zones", "colour_hex", "TEXT")
    ensure_column("drawing_progress_zones", "notes", "TEXT")
    ensure_column("drawing_progress_zones", "created_at", "TEXT")
    ensure_column("drawing_progress_zones", "updated_at", "TEXT")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_drawing_zones_job_id ON drawing_progress_zones(job_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_drawing_zones_package_id ON drawing_progress_zones(package_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_drawing_zones_document_id ON drawing_progress_zones(document_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_drawing_zones_progress_id ON drawing_progress_zones(progress_section_id)")


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
    ensure_column("staff_schedule", "period_type", "TEXT")
    ensure_column("staff_schedule", "period_start", "TEXT")
    ensure_column("staff_schedule", "period_end", "TEXT")
    ensure_column("staff_schedule", "planned_hours", "REAL")


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
        INSERT INTO app_settings (setting_key, setting_value)
        VALUES (?, ?)
        ON CONFLICT(setting_key) DO UPDATE SET
            setting_value = excluded.setting_value
    """, ("starter_data_seeded", "yes"))

    conn.commit()
    conn.close()

