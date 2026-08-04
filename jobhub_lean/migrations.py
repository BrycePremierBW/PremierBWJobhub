from __future__ import annotations

from .db import Database


def _create_stage_control_tables(db: Database, pk: str) -> None:
    """Create the lightweight staged-production tables formerly made by the monolith."""
    statements = [
        f"""
        CREATE TABLE IF NOT EXISTS estimate_baselines (
            id {pk},
            job_id INTEGER NOT NULL,
            estimate_id INTEGER NOT NULL,
            baseline_name TEXT NOT NULL,
            estimate_no TEXT,
            revision TEXT,
            total_ex_gst REAL DEFAULT 0,
            total_inc_gst REAL DEFAULT 0,
            labour_hours REAL DEFAULT 0,
            production_day_hours REAL DEFAULT 8,
            production_value_target REAL DEFAULT 1000,
            active INTEGER NOT NULL DEFAULT 1,
            locked_at TEXT NOT NULL,
            locked_by TEXT,
            notes TEXT
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS estimate_baseline_lines (
            id {pk},
            baseline_id INTEGER NOT NULL,
            source_line_id INTEGER,
            job_stage_id INTEGER,
            stage_name TEXT,
            section TEXT,
            item_description TEXT,
            qty REAL DEFAULT 0,
            unit TEXT,
            unit_rate REAL DEFAULT 0,
            line_total REAL DEFAULT 0,
            estimated_labour_hours REAL DEFAULT 0,
            substrate TEXT,
            work_location TEXT,
            coating_system TEXT,
            colour_finish TEXT,
            source_pack TEXT,
            production_tracking_enabled INTEGER DEFAULT 1,
            notes TEXT
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS stage_progress_updates (
            id {pk},
            job_id INTEGER NOT NULL,
            job_stage_id INTEGER,
            estimate_line_item_id INTEGER,
            update_date TEXT NOT NULL,
            reported_by TEXT,
            completed_quantity REAL DEFAULT 0,
            unit TEXT,
            unit_rate_snapshot REAL DEFAULT 0,
            manual_progress_percent REAL,
            crew_hours REAL DEFAULT 0,
            crew_names TEXT,
            work_type TEXT,
            substrate TEXT,
            coating_system TEXT,
            blocker_type TEXT,
            blocker_status TEXT DEFAULT 'None',
            blocker_notes TEXT,
            ready_for_inspection INTEGER DEFAULT 0,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS job_alert_acknowledgements (
            id {pk},
            job_id INTEGER NOT NULL,
            job_stage_id INTEGER,
            alert_key TEXT NOT NULL,
            acknowledged_at TEXT NOT NULL,
            acknowledged_by TEXT,
            notes TEXT,
            UNIQUE(job_id,job_stage_id,alert_key)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS app_notifications (
            id {pk},
            recipient_user_id INTEGER NOT NULL,
            event_type TEXT,
            title TEXT,
            message TEXT,
            job_id INTEGER,
            entity_type TEXT,
            entity_id TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL,
            read_at TEXT
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS staff_requests (
            id {pk},
            requested_by_user_id INTEGER,
            employee_id INTEGER NOT NULL,
            job_id INTEGER,
            job_stage_id INTEGER,
            request_type TEXT NOT NULL,
            title TEXT NOT NULL,
            instructions TEXT,
            priority TEXT NOT NULL DEFAULT 'Normal',
            due_at TEXT,
            status TEXT NOT NULL DEFAULT 'Requested',
            response_notes TEXT,
            response_entity_type TEXT,
            response_entity_id TEXT,
            requested_at TEXT NOT NULL,
            completed_at TEXT,
            completed_by TEXT
        )
        """,
    ]
    for statement in statements:
        db.execute(statement)

    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_estimate_baselines_job ON estimate_baselines(job_id,active,id)",
        "CREATE INDEX IF NOT EXISTS idx_estimate_baseline_lines_stage ON estimate_baseline_lines(baseline_id,job_stage_id,id)",
        "CREATE INDEX IF NOT EXISTS idx_stage_progress_job_stage ON stage_progress_updates(job_id,job_stage_id,update_date)",
        "CREATE INDEX IF NOT EXISTS idx_stage_progress_line ON stage_progress_updates(estimate_line_item_id,update_date)",
        "CREATE INDEX IF NOT EXISTS idx_app_notifications_recipient_unread ON app_notifications(recipient_user_id,read_at,created_at)",
        "CREATE INDEX IF NOT EXISTS idx_staff_requests_employee_status ON staff_requests(employee_id,status,due_at)",
        "CREATE INDEX IF NOT EXISTS idx_staff_requests_job_stage ON staff_requests(job_id,job_stage_id,status)",
    ]
    for statement in indexes:
        db.execute(statement)


def ensure_compatibility_schema(db: Database) -> None:
    """Add columns and compact legacy tables needed by modular JobHub pages."""
    pk = "SERIAL PRIMARY KEY" if db.postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS takeoff_pack_imports (
            id {pk},job_id INTEGER NOT NULL,pack_id TEXT NOT NULL,revision TEXT NOT NULL,
            source_file TEXT,imported_at TEXT NOT NULL,imported_by TEXT,estimate_id INTEGER,
            line_count INTEGER DEFAULT 0,material_count INTEGER DEFAULT 0,
            document_count INTEGER DEFAULT 0,stage_count INTEGER DEFAULT 0,
            purchase_order_count INTEGER DEFAULT 0,manifest_json TEXT,
            UNIQUE(job_id,pack_id,revision)
        )
        """
    )
    for column, definition in {
        "estimate_id": "INTEGER",
        "line_count": "INTEGER DEFAULT 0",
        "material_count": "INTEGER DEFAULT 0",
        "document_count": "INTEGER DEFAULT 0",
        "stage_count": "INTEGER DEFAULT 0",
        "purchase_order_count": "INTEGER DEFAULT 0",
        "manifest_json": "TEXT",
    }.items():
        db.ensure_column("takeoff_pack_imports", column, definition)

    for column, definition in {
        "job_stage_id": "INTEGER",
        "po_value_ex_gst": "REAL DEFAULT 0",
        "file_name": "TEXT",
        "file_path": "TEXT",
        "uploaded_at": "TEXT",
        "uploaded_by": "TEXT",
        "po_scope_label": "TEXT",
        "po_scope_base_ex_gst": "REAL DEFAULT 0",
        "po_scope_percent": "REAL DEFAULT 0",
        "po_percent_of_job": "REAL DEFAULT 0",
        "po_calculation_mode": "TEXT",
    }.items():
        db.ensure_column("job_purchase_orders", column, definition)

    for column, definition in {
        "uploaded_at": "TEXT",
        "uploaded_by": "TEXT",
        "mime_type": "TEXT",
        "storage_key": "TEXT",
    }.items():
        db.ensure_column("job_documents", column, definition)

    _create_stage_control_tables(db, pk)

    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_takeoff_pack_imports_job ON takeoff_pack_imports(job_id,imported_at)"
    )
