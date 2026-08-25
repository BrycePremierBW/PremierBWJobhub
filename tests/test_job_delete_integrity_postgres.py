"""Real PostgreSQL regression for JobHub permanent job deletion guards."""
from __future__ import annotations

import os
import uuid

import psycopg2

from jobhub_delete_integrity import ensure_job_delete_integrity


CI_URL_ENV = "JOBHUB_POSTGRES_CI_URL"


class PostgresConnectionAdapter:
    """Small production-shaped adapter used only by this integration test."""

    def __init__(self, raw, schema: str):
        self.conn = raw
        self._closed = False
        with raw.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}", public')
        raw.commit()

    def cursor(self):
        return self.conn.cursor()

    def commit(self):
        return self.conn.commit()

    def rollback(self):
        return self.conn.rollback()

    def close(self):
        if not self._closed:
            self._closed = True
            self.conn.close()


def _create_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE jobs(id INTEGER PRIMARY KEY);
            CREATE TABLE job_comments(
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id),
                comment TEXT NOT NULL
            );
            CREATE TABLE timesheet_entries(
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id)
            );
            CREATE TABLE field_clock_entries(
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id),
                submitted_timesheet_id INTEGER REFERENCES timesheet_entries(id)
            );

            CREATE TABLE estimate_working_sheets(
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id)
            );
            CREATE TABLE estimate_line_items(
                id INTEGER PRIMARY KEY,
                estimate_id INTEGER NOT NULL REFERENCES estimate_working_sheets(id)
            );
            CREATE TABLE job_progress_settings(
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id),
                linked_estimate_id INTEGER REFERENCES estimate_working_sheets(id)
            );
            CREATE TABLE job_dwelling_progress(
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id)
            );
            CREATE TABLE job_external_progress(
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id),
                estimate_line_id INTEGER REFERENCES estimate_line_items(id)
            );

            CREATE TABLE material_entries(
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id)
            );
            CREATE TABLE purchase_orders(
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id)
            );
            CREATE TABLE purchase_order_lines(
                id INTEGER PRIMARY KEY,
                purchase_order_id INTEGER NOT NULL REFERENCES purchase_orders(id),
                material_entry_id INTEGER REFERENCES material_entries(id)
            );
            CREATE TABLE supplier_invoices(
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id),
                purchase_order_id INTEGER REFERENCES purchase_orders(id)
            );
            CREATE TABLE supplier_invoice_lines(
                id INTEGER PRIMARY KEY,
                supplier_invoice_id INTEGER NOT NULL REFERENCES supplier_invoices(id),
                matched_po_line_id INTEGER REFERENCES purchase_order_lines(id)
            );

            CREATE TABLE job_progress_snapshots(
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id)
            );
            CREATE TABLE field_forms(
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id)
            );
            CREATE TABLE offline_sync_events(id TEXT PRIMARY KEY, job_id INTEGER NOT NULL);
            CREATE TABLE paint_systems(id TEXT PRIMARY KEY, job_id INTEGER NOT NULL);
            CREATE TABLE colour_approvals(id TEXT PRIMARY KEY, job_id INTEGER NOT NULL);
            CREATE TABLE plan_evidence(id TEXT PRIMARY KEY, job_id INTEGER NOT NULL);
            CREATE TABLE drawing_revisions(id TEXT PRIMARY KEY, job_id INTEGER NOT NULL);
            CREATE TABLE variation_suggestions(id TEXT PRIMARY KEY, job_id INTEGER NOT NULL);
            CREATE TABLE handover_packs(id TEXT PRIMARY KEY, job_id INTEGER NOT NULL);
            """
        )
    conn.commit()


def _seed(conn, job_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO jobs(id) VALUES(%s)", (job_id,))
        cur.execute(
            "INSERT INTO job_comments(id,job_id,comment) VALUES(%s,%s,%s)",
            (job_id * 10 + 15, job_id, "Permanent delete regression"),
        )
        cur.execute("INSERT INTO timesheet_entries(id,job_id) VALUES(%s,%s)", (job_id * 10 + 1, job_id))
        cur.execute(
            "INSERT INTO field_clock_entries(id,job_id,submitted_timesheet_id) VALUES(%s,%s,%s)",
            (job_id * 10 + 2, job_id, job_id * 10 + 1),
        )
        cur.execute("INSERT INTO estimate_working_sheets(id,job_id) VALUES(%s,%s)", (job_id * 10 + 3, job_id))
        cur.execute("INSERT INTO estimate_line_items(id,estimate_id) VALUES(%s,%s)", (job_id * 10 + 4, job_id * 10 + 3))
        cur.execute(
            "INSERT INTO job_progress_settings(id,job_id,linked_estimate_id) VALUES(%s,%s,%s)",
            (job_id * 10 + 5, job_id, job_id * 10 + 3),
        )
        cur.execute("INSERT INTO job_dwelling_progress(id,job_id) VALUES(%s,%s)", (job_id * 10 + 6, job_id))
        cur.execute(
            "INSERT INTO job_external_progress(id,job_id,estimate_line_id) VALUES(%s,%s,%s)",
            (job_id * 10 + 7, job_id, job_id * 10 + 4),
        )
        cur.execute("INSERT INTO material_entries(id,job_id) VALUES(%s,%s)", (job_id * 10 + 8, job_id))
        cur.execute("INSERT INTO purchase_orders(id,job_id) VALUES(%s,%s)", (job_id * 10 + 9, job_id))
        cur.execute(
            "INSERT INTO purchase_order_lines(id,purchase_order_id,material_entry_id) VALUES(%s,%s,%s)",
            (job_id * 10 + 10, job_id * 10 + 9, job_id * 10 + 8),
        )
        cur.execute(
            "INSERT INTO supplier_invoices(id,job_id,purchase_order_id) VALUES(%s,%s,%s)",
            (job_id * 10 + 11, job_id, job_id * 10 + 9),
        )
        cur.execute(
            "INSERT INTO supplier_invoice_lines(id,supplier_invoice_id,matched_po_line_id) VALUES(%s,%s,%s)",
            (job_id * 10 + 12, job_id * 10 + 11, job_id * 10 + 10),
        )
        cur.execute("INSERT INTO job_progress_snapshots(id,job_id) VALUES(%s,%s)", (job_id * 10 + 13, job_id))
        cur.execute("INSERT INTO field_forms(id,job_id) VALUES(%s,%s)", (job_id * 10 + 14, job_id))
        for table, row_id in (
            ("offline_sync_events", f"sync-{job_id}"),
            ("paint_systems", f"paint-{job_id}"),
            ("colour_approvals", f"colour-{job_id}"),
            ("plan_evidence", f"evidence-{job_id}"),
            ("drawing_revisions", f"revision-{job_id}"),
            ("variation_suggestions", f"variation-{job_id}"),
            ("handover_packs", f"handover-{job_id}"),
        ):
            cur.execute(f"INSERT INTO {table}(id,job_id) VALUES(%s,%s)", (row_id, job_id))
    conn.commit()


def main() -> int:
    url = str(os.getenv(CI_URL_ENV, "") or "").strip()
    if not url:
        print(f"SKIP: {CI_URL_ENV} is not configured")
        return 0

    schema = f"jobhub_delete_ci_{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(url, connect_timeout=5)
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{schema}"')

        def connect():
            raw = psycopg2.connect(url, connect_timeout=5)
            return PostgresConnectionAdapter(raw, schema)

        setup = connect()
        try:
            _create_schema(setup)
        finally:
            setup.close()

        assert ensure_job_delete_integrity(connect) is True
        assert ensure_job_delete_integrity(connect) is True

        conn = connect()
        try:
            _seed(conn, 7)
            with conn.cursor() as cur:
                # Standalone timesheet deletion preserves the original clock row.
                cur.execute("DELETE FROM timesheet_entries WHERE id=%s", (71,))
                cur.execute("SELECT submitted_timesheet_id FROM field_clock_entries WHERE id=%s", (72,))
                assert cur.fetchone()[0] is None
            conn.commit()

            # Recreate the timesheet link so the full permanent-delete sequence
            # exercises the original production failure as well.
            with conn.cursor() as cur:
                cur.execute("INSERT INTO timesheet_entries(id,job_id) VALUES(%s,%s)", (71, 7))
                cur.execute("UPDATE field_clock_entries SET submitted_timesheet_id=%s WHERE id=%s", (71, 72))
                cur.execute(
                    "DELETE FROM estimate_line_items WHERE estimate_id IN (SELECT id FROM estimate_working_sheets WHERE job_id=%s)",
                    (7,),
                )
                cur.execute("SELECT estimate_line_id FROM job_external_progress WHERE job_id=%s", (7,))
                assert cur.fetchone()[0] is None
                cur.execute("DELETE FROM estimate_working_sheets WHERE job_id=%s", (7,))
                cur.execute("SELECT linked_estimate_id FROM job_progress_settings WHERE job_id=%s", (7,))
                assert cur.fetchone()[0] is None
                cur.execute("DELETE FROM timesheet_entries WHERE job_id=%s", (7,))
                cur.execute("SELECT submitted_timesheet_id FROM field_clock_entries WHERE job_id=%s", (7,))
                assert cur.fetchone()[0] is None
                cur.execute("DELETE FROM material_entries WHERE job_id=%s", (7,))
                cur.execute("SELECT material_entry_id FROM purchase_order_lines WHERE purchase_order_id=%s", (79,))
                assert cur.fetchone()[0] is None
                # This direct jobs delete is blocked by job_comments without the
                # BEFORE DELETE cleanup trigger, matching the production failure.
                cur.execute("DELETE FROM jobs WHERE id=%s", (7,))
            conn.commit()

            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM jobs WHERE id=%s", (7,))
                assert int(cur.fetchone()[0]) == 0
                for table in (
                    "job_comments",
                    "field_clock_entries",
                    "job_progress_settings",
                    "job_dwelling_progress",
                    "job_external_progress",
                    "purchase_orders",
                    "purchase_order_lines",
                    "supplier_invoices",
                    "supplier_invoice_lines",
                    "job_progress_snapshots",
                    "field_forms",
                    "offline_sync_events",
                    "paint_systems",
                    "colour_approvals",
                    "plan_evidence",
                    "drawing_revisions",
                    "variation_suggestions",
                    "handover_packs",
                ):
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    assert int(cur.fetchone()[0]) == 0, table
        finally:
            conn.close()

        print("PostgreSQL job delete integrity regression passed")
        return 0
    finally:
        try:
            with admin.cursor() as cur:
                cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            admin.close()


if __name__ == "__main__":
    raise SystemExit(main())
