"""Restart-safe schema for offline sync and critical notification delivery."""

from __future__ import annotations


V2_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS offline_sync_events (
        id TEXT PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE,
        event_type TEXT NOT NULL,
        employee_id INTEGER NOT NULL,
        job_id INTEGER NOT NULL,
        occurred_at TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        attempt_count INTEGER NOT NULL DEFAULT 0,
        result_json TEXT,
        last_error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS critical_email_outbox (
        id TEXT PRIMARY KEY,
        message_key TEXT NOT NULL UNIQUE,
        event_type TEXT NOT NULL,
        recipient_json TEXT NOT NULL,
        subject TEXT NOT NULL,
        text_body TEXT NOT NULL,
        html_body TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        attempt_count INTEGER NOT NULL DEFAULT 0,
        provider_message_id TEXT,
        last_error TEXT,
        next_attempt_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        sent_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_offline_sync_status ON offline_sync_events(status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_email_outbox_status ON critical_email_outbox(status, next_attempt_at)",
)


def ensure_v2_schema(connection_factory) -> None:
    connection = connection_factory()
    cursor = connection.cursor()
    try:
        for statement in V2_SCHEMA_STATEMENTS:
            cursor.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()
