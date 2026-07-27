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
)


def ensure_xero_schema(connection) -> None:
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
