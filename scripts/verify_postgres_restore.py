"""Read-only verification of a restored JobHub PostgreSQL database.

Usage:
    SOURCE_DATABASE_URL=... RESTORE_DATABASE_URL=... \
        python scripts/verify_postgres_restore.py

The script never prints either connection string and never mutates either
database. It compares table presence, row counts and deterministic hashes of
selected non-secret fields. Exit code 0 means the checked source/restore data
match; exit code 1 means one or more checks differ.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from typing import Any

import psycopg2


SOURCE_ENV = "SOURCE_DATABASE_URL"
RESTORE_ENV = "RESTORE_DATABASE_URL"

TABLE_CHECKS: dict[str, tuple[str, ...]] = {
    "jobs": (
        "id", "job_no", "job_name", "builder_client_id", "site_address",
        "status", "leading_hand", "start_date", "end_date", "contract_value",
    ),
    "employees": (
        "id", "name", "role", "phone", "status",
    ),
    "builders_clients": (
        "id", "type", "name", "contact_name", "phone", "email",
    ),
    "document_library": (
        "id", "library_category", "document_type", "entity_type", "entity_id",
        "job_id", "file_name", "file_sha256", "revision", "document_date",
        "source_app", "created_at",
    ),
    "job_documents": (
        "id", "job_id", "document_type", "file_name", "created_at",
    ),
    "app_error_events": (
        "id", "username", "area", "error_type", "created_at", "resolved_at",
    ),
    "backup_runs": (
        "id", "backup_type", "size_bytes", "status", "created_at",
    ),
}


def _connect(url: str):
    return psycopg2.connect(url, connect_timeout=8, sslmode="require")


def _table_exists(conn: Any, table: str) -> bool:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND table_name = %s
            )
            """,
            (table,),
        )
        return bool(cursor.fetchone()[0])


def _columns(conn: Any, table: str) -> set[str]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = %s
            """,
            (table,),
        )
        return {str(row[0]) for row in cursor.fetchall()}


def _count(conn: Any, table: str) -> int:
    with conn.cursor() as cursor:
        cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
        return int(cursor.fetchone()[0])


def _digest_rows(conn: Any, table: str, wanted_columns: tuple[str, ...]) -> tuple[str, list[str]]:
    available = _columns(conn, table)
    columns = [name for name in wanted_columns if name in available]
    if not columns:
        return hashlib.sha256(b"").hexdigest(), []

    quoted = ", ".join(f'"{name}"' for name in columns)
    order_by = '"id"' if "id" in available else quoted
    digest = hashlib.sha256()
    with conn.cursor(name=f"verify_{table}") as cursor:
        cursor.itersize = 1000
        cursor.execute(f'SELECT {quoted} FROM "{table}" ORDER BY {order_by}')
        for row in cursor:
            payload = json.dumps(row, default=str, ensure_ascii=False, separators=(",", ":"))
            digest.update(payload.encode("utf-8"))
            digest.update(b"\n")
    conn.rollback()
    return digest.hexdigest(), columns


def _snapshot(conn: Any) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for table, wanted_columns in TABLE_CHECKS.items():
        exists = _table_exists(conn, table)
        item: dict[str, Any] = {"exists": exists}
        if exists:
            item["count"] = _count(conn, table)
            digest, columns = _digest_rows(conn, table, wanted_columns)
            item["digest"] = digest
            item["columns"] = columns
        snapshot[table] = item
    return snapshot


def _safe_database_name(conn: Any) -> str:
    with conn.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        return str(cursor.fetchone()[0] or "")


def main() -> int:
    source_url = str(os.getenv(SOURCE_ENV, "") or "").strip()
    restore_url = str(os.getenv(RESTORE_ENV, "") or "").strip()
    if not source_url or not restore_url:
        print(f"Set both {SOURCE_ENV} and {RESTORE_ENV}; connection strings are never printed.")
        return 2

    source = _connect(source_url)
    restore = _connect(restore_url)
    try:
        source.set_session(readonly=True, autocommit=False)
        restore.set_session(readonly=True, autocommit=False)
        source_name = _safe_database_name(source)
        restore_name = _safe_database_name(restore)
        source_snapshot = _snapshot(source)
        restore_snapshot = _snapshot(restore)
    finally:
        source.close()
        restore.close()

    failures: list[str] = []
    for table in TABLE_CHECKS:
        left = source_snapshot[table]
        right = restore_snapshot[table]
        if left.get("exists") != right.get("exists"):
            failures.append(f"{table}: table presence differs")
            continue
        if not left.get("exists"):
            continue
        if left.get("count") != right.get("count"):
            failures.append(
                f"{table}: row count differs ({left.get('count')} vs {right.get('count')})"
            )
        if left.get("columns") != right.get("columns"):
            failures.append(f"{table}: checked column set differs")
        if left.get("digest") != right.get("digest"):
            failures.append(f"{table}: checked row digest differs")

    print(f"Source database: {source_name}")
    print(f"Restore database: {restore_name}")
    if failures:
        print("RESTORE VERIFICATION FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1

    checked = sum(1 for value in source_snapshot.values() if value.get("exists"))
    print(f"RESTORE VERIFIED: {checked} checked table(s) match source counts and digests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
