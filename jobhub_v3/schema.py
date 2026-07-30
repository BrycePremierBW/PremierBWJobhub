"""Restart-safe V3 accounting and commercial-control schema."""

XERO_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS xero_connections (
        tenant_id TEXT PRIMARY KEY,
        tenant_name TEXT,
        encrypted_access_token TEXT NOT NULL,
        encrypted_refresh_token TEXT NOT NULL,
        token_expires_at TEXT NOT NULL,
        scopes TEXT,
        connected_by TEXT,
        connected_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS xero_oauth_nonces (
        nonce_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        consumed_at TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS xero_sync_events (
        id INTEGER PRIMARY KEY,
        entity_type TEXT NOT NULL,
        entity_id INTEGER NOT NULL,
        direction TEXT NOT NULL,
        operation TEXT NOT NULL,
        xero_id TEXT,
        idempotency_key TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'pending',
        request_json TEXT,
        response_json TEXT,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS commercial_events (
        id INTEGER PRIMARY KEY,
        job_id INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        reference TEXT,
        description TEXT,
        amount NUMERIC NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'draft',
        submitted_date TEXT,
        approved_date TEXT,
        due_date TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS xero_entity_links (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id INTEGER NOT NULL,
        xero_contact_id TEXT NOT NULL,
        last_synced_at TEXT,
        UNIQUE(tenant_id, entity_type, entity_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS xero_invoice_links (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id INTEGER NOT NULL,
        xero_invoice_id TEXT NOT NULL,
        xero_status TEXT,
        amount_due NUMERIC NOT NULL DEFAULT 0,
        amount_paid NUMERIC NOT NULL DEFAULT 0,
        last_synced_at TEXT,
        UNIQUE(tenant_id, entity_type, entity_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS progress_claims (
        id TEXT PRIMARY KEY,
        job_id INTEGER NOT NULL,
        claim_number TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft',
        work_complete_percent NUMERIC NOT NULL DEFAULT 0,
        gross_claim_ex_gst NUMERIC NOT NULL DEFAULT 0,
        retention_ex_gst NUMERIC NOT NULL DEFAULT 0,
        net_claim_ex_gst NUMERIC NOT NULL DEFAULT 0,
        due_date TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(job_id, claim_number)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS supplier_bills (
        id TEXT PRIMARY KEY,
        job_id INTEGER NOT NULL,
        supplier_name TEXT NOT NULL,
        supplier_invoice_number TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft',
        amount_ex_gst NUMERIC NOT NULL DEFAULT 0,
        invoice_date TEXT,
        due_date TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(supplier_name, supplier_invoice_number)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS retention_ledger (
        id TEXT PRIMARY KEY,
        job_id INTEGER NOT NULL,
        progress_claim_id TEXT,
        event_type TEXT NOT NULL,
        amount_ex_gst NUMERIC NOT NULL,
        event_date TEXT NOT NULL,
        notes TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS extension_of_time (
        id TEXT PRIMARY KEY,
        job_id INTEGER NOT NULL,
        reference TEXT NOT NULL,
        cause TEXT NOT NULL,
        notice_date TEXT,
        requested_days INTEGER NOT NULL DEFAULT 0,
        approved_days INTEGER NOT NULL DEFAULT 0,
        concurrent_delay_days INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'draft',
        decision_notes TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(job_id, reference)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_xero_links_entity ON xero_entity_links(entity_type, entity_id)",
    "CREATE INDEX IF NOT EXISTS idx_xero_invoice_status ON xero_invoice_links(xero_status, last_synced_at)",
    "CREATE INDEX IF NOT EXISTS idx_progress_claims_job ON progress_claims(job_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_supplier_bills_job ON supplier_bills(job_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_retention_job ON retention_ledger(job_id, event_date)",
    "CREATE INDEX IF NOT EXISTS idx_eot_job ON extension_of_time(job_id, status)",
)


def ensure_xero_schema(connection_or_factory) -> None:
    """Create Xero tables using either a connection or connection factory."""
    owns_connection = callable(connection_or_factory)
    connection = (
        connection_or_factory()
        if owns_connection
        else connection_or_factory
    )
    cursor = connection.cursor()
    try:
        for statement in XERO_SCHEMA_STATEMENTS:
            cursor.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        if owns_connection:
            connection.close()
