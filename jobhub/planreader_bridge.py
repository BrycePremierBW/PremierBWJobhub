"""Shared bridge between PB PlanReader and JobHub.

PlanReader writes take-off rows, colour schedules and generated documents into
the same database JobHub uses (SQLite locally, Supabase/Postgres when
DATABASE_URL is set).  JobHub pages read those tables live, so anything edited
in PlanReader appears in JobHub on the next rerun and vice versa.

This module is intentionally self-contained: it does NOT import the jobhub
package, so PB PlanReader can load it by file path without triggering the
JobHub startup guards (which patch Streamlit and must only run in JobHub).
Both apps resolve the same shared database from the same environment variables
(DATA_DIR / DATABASE_URL), which is what makes the two apps "talk".
"""
from __future__ import annotations

import base64
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

TAKEOFF_COLUMNS = [
    "internal_external", "area_location", "substrate", "labour_category",
    "qty_m2", "lineal_m", "count", "coats", "rate_ex_gst",
    "labour_hours", "paint_litres", "value_ex_gst", "source_note", "confidence",
]

COLOUR_SCHEDULE_COLUMNS = [
    "area_location", "surface", "colour", "finish", "product", "notes", "hex",
]


# ---------------------------------------------------------------- environment
def resolve_data_dir() -> str:
    # Match pb_jobhub_app.py exactly so both apps resolve the same database.
    return os.getenv("DATA_DIR", "/var/data")


def resolve_database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    try:
        import streamlit as st  # noqa: PLC0415

        if "DATABASE_URL" in st.secrets:
            url = str(st.secrets["DATABASE_URL"] or "").strip()
    except Exception:
        pass
    return url


DATA_DIR = resolve_data_dir()
DATABASE_URL = resolve_database_url()
USE_POSTGRES = bool(DATABASE_URL)
DB_PATH = os.path.join(DATA_DIR, "jobhub.db")


def connection_status() -> str:
    if USE_POSTGRES:
        return "Connected to the shared JobHub database (PostgreSQL / Supabase)."
    return f"Connected to the shared local JobHub database (SQLite at {DB_PATH})."


# ----------------------------------------------------------------- db helpers
def _adapt_for_postgres(sql: str) -> str:
    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    sql = sql.replace("?", "%s")
    sql = re.sub(r"%(?!s)", "%%", sql)
    return sql


def connect():
    if USE_POSTGRES:
        import psycopg2  # noqa: PLC0415

        conn = psycopg2.connect(DATABASE_URL, sslmode="require")
        return conn, True
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn, False


def df_query(sql: str, params: tuple = ()):
    conn, is_postgres = connect()
    try:
        if is_postgres:
            sql = _adapt_for_postgres(sql)
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description] if cur.description else []
        import pandas as pd  # noqa: PLC0415

        return pd.DataFrame(rows, columns=columns)
    finally:
        conn.close()


def execute(sql: str, params: tuple = ()):
    conn, is_postgres = connect()
    try:
        if is_postgres:
            sql = _adapt_for_postgres(sql)
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


def execute_many(sql: str, rows: Iterable[tuple]):
    conn, is_postgres = connect()
    try:
        if is_postgres:
            sql = _adapt_for_postgres(sql)
        cur = conn.cursor()
        cur.executemany(sql, list(rows))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------- schema
_SCHEMA_STATEMENTS = [
    # Core jobs table so PlanReader can link a job before JobHub has ever run.
    # Matches JobHub's jobs DDL; JobHub's startup migrations add any newer
    # columns (row_version, archived_at, ...) on its next run.
    """
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
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS job_takeoff_rows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        internal_external TEXT DEFAULT 'Internal',
        area_location TEXT NOT NULL,
        substrate TEXT NOT NULL,
        labour_category TEXT,
        qty_m2 REAL DEFAULT 0,
        lineal_m REAL DEFAULT 0,
        count REAL DEFAULT 0,
        coats REAL DEFAULT 1,
        rate_ex_gst REAL DEFAULT 0,
        labour_hours REAL DEFAULT 0,
        paint_litres REAL DEFAULT 0,
        value_ex_gst REAL DEFAULT 0,
        source_note TEXT,
        confidence TEXT,
        updated_at TEXT NOT NULL,
        UNIQUE(job_id, area_location, substrate)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_job_takeoff_job
    ON job_takeoff_rows (job_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS job_colour_schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        area_location TEXT NOT NULL,
        surface TEXT NOT NULL,
        colour TEXT,
        finish TEXT,
        product TEXT,
        notes TEXT,
        hex TEXT,
        updated_at TEXT NOT NULL,
        UNIQUE(job_id, area_location, surface)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_job_colour_sch_job
    ON job_colour_schedules (job_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS job_document_blobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        file_name TEXT NOT NULL,
        mime_type TEXT,
        doc_type TEXT,
        notes TEXT,
        blob_data TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(job_id, file_name)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_job_doc_blobs_job
    ON job_document_blobs (job_id)
    """,
]


def ensure_bridge_schema() -> None:
    """Create the shared PlanReader <-> JobHub tables. Idempotent and restart-safe."""
    for statement in _SCHEMA_STATEMENTS:
        execute(statement)


# -------------------------------------------------------------------- helpers
def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _f(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def jobs_with_sync_frame():
    """Jobs that have any PlanReader-synced data, with the latest sync time."""
    return df_query("""
        SELECT j.id AS "Job ID",
               j.job_no AS "Job No",
               COALESCE(j.job_name, '') AS "Job Name",
               COALESCE(j.status, '') AS "Status",
               COALESCE(
                   (
                       SELECT MAX(m.updated_at) FROM (
                           SELECT updated_at FROM job_takeoff_rows WHERE job_id = j.id
                           UNION ALL
                           SELECT updated_at FROM job_colour_schedules WHERE job_id = j.id
                       ) m
                   ),
                   ''
               ) AS "Last Synced"
        FROM jobs j
        WHERE EXISTS (SELECT 1 FROM job_takeoff_rows WHERE job_id = j.id)
           OR EXISTS (SELECT 1 FROM job_colour_schedules WHERE job_id = j.id)
           OR EXISTS (SELECT 1 FROM job_document_blobs WHERE job_id = j.id)
        ORDER BY j.job_no
    """)


def link_job_by_no(job_no: str) -> Optional[int]:
    """Return the JobHub job id for a job number, or None."""
    if not job_no:
        return None
    df = df_query("SELECT id FROM jobs WHERE job_no = ?", (str(job_no).strip(),))
    if df.empty:
        return None
    return int(df.iloc[0]["id"])


def create_linked_job(
    job_no: str,
    job_name: str = "",
    site_address: str = "",
    status: str = "Active",
) -> Optional[int]:
    """Insert a minimal JobHub job row if the job number does not exist yet."""
    existing = link_job_by_no(job_no)
    if existing is not None:
        return existing
    now = _now()
    execute("""
        INSERT INTO jobs (job_no, job_name, site_address, status, notes, start_date, end_date)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        str(job_no).strip(),
        str(job_name or ""),
        str(site_address or ""),
        str(status or "Active"),
        "Created by PB PlanReader sync.",
        now[:10],
        "",
    ))
    return link_job_by_no(job_no)


# --------------------------------------------------------------------- writers
def sync_takeoff_rows(job_id: int, rows: Iterable[Dict[str, Any]]) -> int:
    """Upsert take-off rows for a job. Returns the number of rows written."""
    job_id = int(job_id)
    now = _now()
    payloads: List[tuple] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        area_location = str(row.get("area_location") or "").strip()
        substrate = str(row.get("substrate") or "").strip()
        if not area_location or not substrate:
            continue
        coats = row.get("coats")
        payloads.append((
            job_id,
            str(row.get("internal_external") or "Internal"),
            area_location,
            substrate,
            str(row.get("labour_category") or ""),
            _f(row.get("qty_m2")),
            _f(row.get("lineal_m")),
            _f(row.get("count")),
            _f(coats) if coats is not None else 1.0,
            _f(row.get("rate_ex_gst")),
            _f(row.get("labour_hours")),
            _f(row.get("paint_litres")),
            _f(row.get("value_ex_gst")),
            str(row.get("source_note") or ""),
            str(row.get("confidence") or ""),
            now,
        ))
    if not payloads:
        return 0
    execute_many("""
        INSERT INTO job_takeoff_rows (
            job_id, internal_external, area_location, substrate, labour_category,
            qty_m2, lineal_m, count, coats, rate_ex_gst, labour_hours, paint_litres,
            value_ex_gst, source_note, confidence, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (job_id, area_location, substrate) DO UPDATE SET
            internal_external = excluded.internal_external,
            labour_category = excluded.labour_category,
            qty_m2 = excluded.qty_m2,
            lineal_m = excluded.lineal_m,
            count = excluded.count,
            coats = excluded.coats,
            rate_ex_gst = excluded.rate_ex_gst,
            labour_hours = excluded.labour_hours,
            paint_litres = excluded.paint_litres,
            value_ex_gst = excluded.value_ex_gst,
            source_note = excluded.source_note,
            confidence = excluded.confidence,
            updated_at = excluded.updated_at
    """, payloads)
    return len(payloads)


def sync_colour_schedule(job_id: int, rows: Iterable[Dict[str, Any]]) -> int:
    """Upsert colour schedule rows for a job. Returns the number of rows written."""
    job_id = int(job_id)
    now = _now()
    payloads: List[tuple] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        area_location = str(row.get("area_location") or "").strip()
        surface = str(row.get("surface") or "").strip()
        if not area_location or not surface:
            continue
        payloads.append((
            job_id,
            area_location,
            surface,
            str(row.get("colour") or ""),
            str(row.get("finish") or ""),
            str(row.get("product") or ""),
            str(row.get("notes") or ""),
            str(row.get("hex") or ""),
            now,
        ))
    if not payloads:
        return 0
    execute_many("""
        INSERT INTO job_colour_schedules (
            job_id, area_location, surface, colour, finish, product, notes, hex, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (job_id, area_location, surface) DO UPDATE SET
            colour = excluded.colour,
            finish = excluded.finish,
            product = excluded.product,
            notes = excluded.notes,
            hex = excluded.hex,
            updated_at = excluded.updated_at
    """, payloads)
    return len(payloads)


def upsert_document_blob(
    job_id: int,
    file_name: str,
    blob_bytes: bytes,
    mime_type: str = "application/octet-stream",
    doc_type: str = "PlanReader",
    notes: str = "",
) -> bool:
    """Store a generated file (colour markup PNG, schedule XLSX/PDF) in the shared DB."""
    job_id = int(job_id)
    file_name = str(file_name or "").strip()
    if not file_name or blob_bytes is None:
        return False
    encoded = base64.b64encode(blob_bytes).decode("ascii")
    execute("""
        INSERT INTO job_document_blobs (job_id, file_name, mime_type, doc_type, notes, blob_data, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (job_id, file_name) DO UPDATE SET
            mime_type = excluded.mime_type,
            doc_type = excluded.doc_type,
            notes = excluded.notes,
            blob_data = excluded.blob_data,
            created_at = excluded.created_at
    """, (
        job_id,
        file_name,
        str(mime_type or "application/octet-stream"),
        str(doc_type or "PlanReader"),
        str(notes or ""),
        encoded,
        _now(),
    ))
    return True


def decode_document_blob(row: Dict[str, Any]) -> bytes:
    """Decode a job_document_blobs row back into raw bytes."""
    try:
        return base64.b64decode(str(row.get("blob_data") or ""))
    except Exception:
        return b""


# --------------------------------------------------------------------- readers
def job_takeoff_frame(job_id: int):
    return df_query("""
        SELECT area_location AS "Area / Location",
               substrate AS "Substrate",
               internal_external AS "Internal / External",
               labour_category AS "Labour Category",
               qty_m2 AS "Qty (m²)",
               lineal_m AS "Lineal m",
               count AS "Count",
               coats AS "Coats",
               rate_ex_gst AS "Rate Ex GST",
               labour_hours AS "Labour Hours",
               paint_litres AS "Paint Litres",
               value_ex_gst AS "Value Ex GST",
               source_note AS "Source",
               confidence AS "Confidence",
               updated_at AS "Updated"
        FROM job_takeoff_rows
        WHERE job_id = ?
        ORDER BY area_location, substrate
    """, (int(job_id),))


def job_colour_schedule_frame(job_id: int):
    return df_query("""
        SELECT area_location AS "Area / Location",
               surface AS "Surface",
               colour AS "Colour",
               finish AS "Finish",
               product AS "Product",
               notes AS "Notes",
               hex AS "Hex",
               updated_at AS "Updated"
        FROM job_colour_schedules
        WHERE job_id = ?
        ORDER BY area_location, surface
    """, (int(job_id),))


def job_document_blobs_frame(job_id: int):
    return df_query("""
        SELECT id AS "ID",
               file_name AS "File Name",
               mime_type AS "Mime Type",
               doc_type AS "Doc Type",
               notes AS "Notes",
               created_at AS "Created At",
               blob_data AS "blob_data"
        FROM job_document_blobs
        WHERE job_id = ?
        ORDER BY id DESC
    """, (int(job_id),))
