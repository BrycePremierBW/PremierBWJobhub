from __future__ import annotations

from datetime import datetime

from .db import Database


def _pk(db: Database) -> str:
    return "SERIAL PRIMARY KEY" if db.postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"


def ensure_schema(db: Database) -> None:
    """Create only the stable core tables; never rebuild or delete live data."""
    pk = _pk(db)
    statements = [
        f"""
        CREATE TABLE IF NOT EXISTS app_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS builders_clients (
            id {pk}, type TEXT, name TEXT UNIQUE, contact_name TEXT, phone TEXT,
            email TEXT, address TEXT, qbcc TEXT, abn TEXT, terms TEXT, notes TEXT
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS employees (
            id {pk}, name TEXT UNIQUE, role TEXT, phone TEXT, email TEXT,
            base_hourly_rate REAL DEFAULT 0, rate_plus_10 REAL DEFAULT 0,
            status TEXT DEFAULT 'Active', notes TEXT
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS jobs (
            id {pk}, job_no TEXT UNIQUE, job_name TEXT, builder_client_id INTEGER,
            site_address TEXT, status TEXT, leading_hand TEXT, start_date TEXT,
            end_date TEXT, contract_value REAL DEFAULT 0, notes TEXT,
            archived_at TEXT, archived_by TEXT, row_version INTEGER DEFAULT 1
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS products (
            id {pk}, product_code TEXT UNIQUE, product_name TEXT, supplier TEXT,
            unit TEXT, price_ex_gst REAL DEFAULT 0, notes TEXT
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS app_users (
            id {pk}, username TEXT UNIQUE, password_hash TEXT, role TEXT DEFAULT 'employee',
            employee_id INTEGER, active INTEGER DEFAULT 1, must_change_password INTEGER DEFAULT 0,
            notes TEXT, failed_login_count INTEGER DEFAULT 0, locked_until TEXT,
            password_changed_at TEXT
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS job_purchase_orders (
            id {pk}, job_id INTEGER NOT NULL, po_number TEXT NOT NULL,
            description TEXT, amount_ex_gst REAL DEFAULT 0, status TEXT DEFAULT 'Active',
            received_date TEXT, notes TEXT, created_at TEXT, updated_at TEXT,
            UNIQUE(job_id, po_number)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS job_stages (
            id {pk}, job_id INTEGER NOT NULL, purchase_order_id INTEGER,
            stage_name TEXT NOT NULL, sequence_order INTEGER DEFAULT 1,
            job_percent REAL DEFAULT 0, status TEXT DEFAULT 'Planned',
            start_date TEXT, end_date TEXT, budget_hours REAL DEFAULT 0,
            notes TEXT, created_at TEXT, updated_at TEXT
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS timesheet_entries (
            id {pk}, job_id INTEGER NOT NULL, job_stage_id INTEGER, employee_id INTEGER NOT NULL,
            work_date TEXT, start_time TEXT, finish_time TEXT, break_minutes REAL DEFAULT 0,
            total_hours REAL DEFAULT 0, work_type TEXT, submitted_by TEXT, submitted_at TEXT,
            status TEXT DEFAULT 'Submitted', approved_by TEXT, approved_at TEXT, notes TEXT
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS wage_entries (
            id {pk}, job_id INTEGER, employee_id INTEGER, work_date TEXT,
            hours REAL DEFAULT 0, hourly_rate REAL DEFAULT 0,
            hourly_rate_snapshot REAL DEFAULT 0, timesheet_id INTEGER,
            source TEXT, notes TEXT
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS material_entries (
            id {pk}, job_id INTEGER, product_id INTEGER,
            qty_required REAL DEFAULT 0, qty_received REAL DEFAULT 0,
            date_ordered TEXT, supplier TEXT, notes TEXT,
            custom_product_code TEXT, custom_product_name TEXT, custom_supplier TEXT,
            custom_unit TEXT, custom_unit_price REAL DEFAULT 0, custom_colour TEXT
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS equipment_checklist_items (
            id {pk}, item_name TEXT UNIQUE, category TEXT, unit TEXT,
            default_qty REAL DEFAULT 0, notes TEXT
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS equipment_checklist_records (
            id {pk}, job_id INTEGER NOT NULL, checklist_item_id INTEGER NOT NULL,
            qty_required REAL DEFAULT 0, qty_taken REAL DEFAULT 0,
            qty_returned REAL DEFAULT 0, is_required INTEGER DEFAULT 1,
            is_packed INTEGER DEFAULT 0, is_returned INTEGER DEFAULT 0,
            date_out TEXT, date_in TEXT, taken_by TEXT, returned_by TEXT,
            condition_out TEXT, condition_in TEXT, notes TEXT,
            UNIQUE(job_id, checklist_item_id)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS job_documents (
            id {pk}, job_id INTEGER, document_type TEXT, file_name TEXT,
            file_path TEXT, created_at TEXT, notes TEXT, mime_type TEXT
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS job_photos (
            id {pk}, job_id INTEGER, photo_name TEXT, photo_type TEXT,
            photo_data TEXT, category TEXT, caption TEXT,
            uploaded_by TEXT, uploaded_at TEXT, notes TEXT
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS estimate_working_sheets (
            id {pk}, job_id INTEGER NOT NULL, estimate_no TEXT, estimate_date TEXT,
            revision TEXT, status TEXT DEFAULT 'Draft', labour_hours REAL DEFAULT 0,
            labour_rate REAL DEFAULT 0, material_allowance REAL DEFAULT 0,
            access_equipment_allowance REAL DEFAULT 0, subcontractor_allowance REAL DEFAULT 0,
            sundries_allowance REAL DEFAULT 0, margin_percent REAL DEFAULT 0,
            contingency_percent REAL DEFAULT 0, gst_percent REAL DEFAULT 10,
            total_ex_gst REAL DEFAULT 0, gst_amount REAL DEFAULT 0, total_inc_gst REAL DEFAULT 0,
            pricing_method TEXT DEFAULT 'Markup', archived INTEGER DEFAULT 0,
            archived_at TEXT, archived_by TEXT, production_day_hours REAL DEFAULT 8,
            production_value_low REAL DEFAULT 800, production_value_target REAL DEFAULT 1000,
            production_value_high REAL DEFAULT 1000, created_at TEXT, updated_at TEXT, notes TEXT
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS estimate_line_items (
            id {pk}, estimate_id INTEGER NOT NULL, job_stage_id INTEGER,
            production_tracking_enabled INTEGER DEFAULT 1, section TEXT, item_description TEXT,
            qty REAL DEFAULT 0, unit TEXT, unit_rate REAL DEFAULT 0, line_total REAL DEFAULT 0,
            estimated_labour_hours REAL DEFAULT 0, material_allowance REAL DEFAULT 0,
            substrate TEXT, work_location TEXT, coating_system TEXT, colour_finish TEXT,
            source_pack TEXT, notes TEXT
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS audit_events (
            id {pk}, user_id INTEGER, username TEXT, action TEXT,
            entity_type TEXT, entity_id TEXT, details TEXT, created_at TEXT
        )
        """,
    ]
    for statement in statements:
        db.execute(statement)

    additions = {
        "jobs": {
            "archived_at": "TEXT",
            "archived_by": "TEXT",
            "row_version": "INTEGER DEFAULT 1",
        },
        "employees": {
            "email": "TEXT",
            "base_hourly_rate": "REAL DEFAULT 0",
            "rate_plus_10": "REAL DEFAULT 0",
            "status": "TEXT DEFAULT 'Active'",
        },
        "app_users": {
            "active": "INTEGER DEFAULT 1",
            "must_change_password": "INTEGER DEFAULT 0",
            "failed_login_count": "INTEGER DEFAULT 0",
            "locked_until": "TEXT",
            "password_changed_at": "TEXT",
        },
        "timesheet_entries": {
            "job_stage_id": "INTEGER",
            "break_minutes": "REAL DEFAULT 0",
            "total_hours": "REAL DEFAULT 0",
            "work_type": "TEXT",
            "submitted_by": "TEXT",
            "submitted_at": "TEXT",
            "status": "TEXT DEFAULT 'Submitted'",
            "approved_by": "TEXT",
            "approved_at": "TEXT",
        },
        "wage_entries": {
            "hourly_rate": "REAL DEFAULT 0",
            "hourly_rate_snapshot": "REAL DEFAULT 0",
            "timesheet_id": "INTEGER",
            "source": "TEXT",
        },
        "job_documents": {
            "mime_type": "TEXT",
            "storage_key": "TEXT",
        },
        "job_photos": {
            "job_stage_id": "INTEGER",
            "stage_progress_update_id": "INTEGER",
        },
        "material_entries": {
            "custom_product_code": "TEXT",
            "custom_product_name": "TEXT",
            "custom_supplier": "TEXT",
            "custom_unit": "TEXT",
            "custom_unit_price": "REAL",
            "custom_colour": "TEXT",
        },
        "equipment_checklist_items": {"unit": "TEXT"},
        "equipment_checklist_records": {
            "date_out": "TEXT", "date_in": "TEXT", "taken_by": "TEXT", "returned_by": "TEXT",
            "condition_out": "TEXT", "condition_in": "TEXT",
        },
        "estimate_working_sheets": {
            "archived": "INTEGER DEFAULT 0", "archived_at": "TEXT", "archived_by": "TEXT",
            "pricing_method": "TEXT DEFAULT 'Markup'", "gst_amount": "REAL DEFAULT 0",
        },
    }
    for table, columns in additions.items():
        for name, definition in columns.items():
            db.ensure_column(table, name, definition)

    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)",
        "CREATE INDEX IF NOT EXISTS idx_jobs_builder ON jobs(builder_client_id)",
        "CREATE INDEX IF NOT EXISTS idx_timesheets_date ON timesheet_entries(work_date)",
        "CREATE INDEX IF NOT EXISTS idx_timesheets_job ON timesheet_entries(job_id)",
        "CREATE INDEX IF NOT EXISTS idx_materials_job ON material_entries(job_id)",
        "CREATE INDEX IF NOT EXISTS idx_stages_job ON job_stages(job_id, sequence_order)",
        "CREATE INDEX IF NOT EXISTS idx_po_job ON job_purchase_orders(job_id)",
        "CREATE INDEX IF NOT EXISTS idx_documents_job ON job_documents(job_id)",
    ]
    for statement in indexes:
        db.execute(statement)

    db.execute(
        """
        INSERT INTO app_settings(setting_key, setting_value)
        VALUES (?, ?)
        ON CONFLICT(setting_key) DO NOTHING
        """,
        ("lean_schema_ready", datetime.now().isoformat(timespec="seconds")),
    )
