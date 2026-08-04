from __future__ import annotations

from .db import Database


def ensure_compatibility_schema(db: Database) -> None:
    """Add columns needed by modular pages to older JobHub databases."""
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

    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_takeoff_pack_imports_job ON takeoff_pack_imports(job_id,imported_at)"
    )
