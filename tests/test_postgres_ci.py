"""PostgreSQL integration smoke test for JobHub CI.

This test deliberately uses a real PostgreSQL service rather than SQLite. It
checks the production startup ordering contract: core relational tables exist
before the enterprise schema is applied, enterprise DDL is idempotent, normal
read/write/update transactions work, and a fresh physical connection can resume
work after the first connection is closed.

The test is isolated inside a temporary PostgreSQL schema and removes that
schema when complete. It never connects to production data.
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jobhub_enterprise import ensure_enterprise_schema


CI_URL_ENV = "JOBHUB_POSTGRES_CI_URL"


def _adapt_sql(sql: str) -> str:
    statement = str(sql)
    statement = statement.replace(
        "INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY"
    )
    statement = re.sub(r"AS '([^']+)'", r'AS "\1"', statement)
    statement = statement.replace("%", "%%")
    statement = statement.replace("?", "%s")
    return statement


class CursorAdapter:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql, params=()):
        return self._cursor.execute(_adapt_sql(sql), params)

    def executemany(self, sql, rows):
        return self._cursor.executemany(_adapt_sql(sql), rows)

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

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class ConnectionAdapter:
    def __init__(self, connection, schema: str):
        self._connection = connection
        self._schema = schema
        self._closed = False
        with self._connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{schema}", public')
        self._connection.commit()

    def cursor(self):
        return CursorAdapter(self._connection.cursor())

    def commit(self):
        return self._connection.commit()

    def rollback(self):
        return self._connection.rollback()

    def close(self):
        if not self._closed:
            self._closed = True
            self._connection.close()

    def __getattr__(self, name):
        return getattr(self._connection, name)


def _connect(url: str, schema: str) -> ConnectionAdapter:
    raw = psycopg2.connect(url, connect_timeout=5)
    return ConnectionAdapter(raw, schema)


def _ensure_core_prerequisites(connect) -> None:
    """Create only the core tables enterprise DDL legitimately references.

    Production calls ``init_db()`` before ``ensure_enterprise_schema()``. The
    enterprise schema therefore has foreign keys to these already-existing core
    tables. Keeping this explicit in the integration test makes a clean Postgres
    database exercise the same ordering contract without importing/running the
    Streamlit application itself.
    """
    conn = connect()
    try:
        cursor = conn.cursor()
        statements = (
            """
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_no TEXT UNIQUE,
                job_name TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_code TEXT UNIQUE,
                product_name TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS material_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER,
                product_id INTEGER,
                FOREIGN KEY(job_id) REFERENCES jobs(id),
                FOREIGN KEY(product_id) REFERENCES products(id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS timesheet_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER,
                employee_id INTEGER,
                work_date TEXT,
                hours REAL,
                FOREIGN KEY(job_id) REFERENCES jobs(id),
                FOREIGN KEY(employee_id) REFERENCES employees(id)
            )
            """,
        )
        for statement in statements:
            cursor.execute(statement)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    url = str(os.getenv(CI_URL_ENV, "") or "").strip()
    if not url:
        print(f"SKIP: {CI_URL_ENV} is not configured")
        return 0

    schema = f"jobhub_ci_{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(url, connect_timeout=5)
    admin.autocommit = True
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE SCHEMA "{schema}"')

        def connect():
            return _connect(url, schema)

        # Match production startup: core schema first, enterprise schema second.
        _ensure_core_prerequisites(connect)
        _ensure_core_prerequisites(connect)

        # The enterprise schema bootstrap must be safe to run repeatedly.
        assert ensure_enterprise_schema(connect) is True
        assert ensure_enterprise_schema(connect) is True

        first = connect()
        try:
            cursor = first.cursor()
            cursor.execute(
                """
                INSERT INTO app_error_events
                    (username, area, error_type, message, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "ci",
                    "postgres-smoke",
                    "OperationalError",
                    "simulated transient connection event",
                    "2026-08-10 00:00:00",
                ),
            )
            first.commit()
            cursor.execute(
                "SELECT COUNT(*) FROM app_error_events WHERE COALESCE(resolved_at, '') = ''"
            )
            assert int(cursor.fetchone()[0]) == 1
        finally:
            first.close()

        # A completely fresh physical connection must continue normally.
        second = connect()
        try:
            cursor = second.cursor()
            cursor.execute(
                """
                UPDATE app_error_events
                SET resolved_at = ?, resolved_by = ?, resolution_notes = ?
                WHERE COALESCE(resolved_at, '') = ''
                """,
                (
                    "2026-08-10 00:01:00",
                    "ci",
                    "Resolved by PostgreSQL integration smoke test",
                ),
            )
            second.commit()
            cursor.execute(
                "SELECT COUNT(*) FROM app_error_events WHERE COALESCE(resolved_at, '') = ''"
            )
            assert int(cursor.fetchone()[0]) == 0

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.tables
                WHERE table_schema = ?
                  AND table_name IN (
                      'employees', 'jobs', 'products', 'material_entries',
                      'timesheet_entries', 'app_error_events', 'backup_runs',
                      'field_forms'
                  )
                """,
                (schema,),
            )
            assert int(cursor.fetchone()[0]) == 8
        finally:
            second.close()

        print("PostgreSQL integration smoke test passed")
        return 0
    finally:
        try:
            with admin.cursor() as cursor:
                cursor.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            admin.close()


if __name__ == "__main__":
    raise SystemExit(main())
