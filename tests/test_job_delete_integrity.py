from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from jobhub_delete_integrity import ensure_job_delete_integrity


def _connect(path: Path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _create_schema(path: Path) -> None:
    conn = _connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE jobs(id INTEGER PRIMARY KEY);
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
    finally:
        conn.close()


class JobDeleteIntegrityTests(unittest.TestCase):
    def test_deleting_timesheet_unlinks_but_keeps_field_clock_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jobhub.sqlite"
            _create_schema(path)
            ensure_job_delete_integrity(lambda: _connect(path))

            conn = _connect(path)
            try:
                conn.execute("INSERT INTO jobs(id) VALUES(1)")
                conn.execute("INSERT INTO timesheet_entries(id,job_id) VALUES(10,1)")
                conn.execute(
                    "INSERT INTO field_clock_entries(id,job_id,submitted_timesheet_id) VALUES(20,1,10)"
                )
                conn.commit()

                conn.execute("DELETE FROM timesheet_entries WHERE id=10")
                conn.commit()
                row = conn.execute(
                    "SELECT job_id,submitted_timesheet_id FROM field_clock_entries WHERE id=20"
                ).fetchone()
                self.assertEqual(row, (1, None))
            finally:
                conn.close()

    def test_permanent_job_delete_releases_references_then_removes_auxiliary_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jobhub.sqlite"
            _create_schema(path)
            ensure_job_delete_integrity(lambda: _connect(path))

            conn = _connect(path)
            try:
                conn.execute("INSERT INTO jobs(id) VALUES(2)")
                conn.execute("INSERT INTO timesheet_entries(id,job_id) VALUES(21,2)")
                conn.execute(
                    "INSERT INTO field_clock_entries(id,job_id,submitted_timesheet_id) VALUES(22,2,21)"
                )

                conn.execute("INSERT INTO estimate_working_sheets(id,job_id) VALUES(30,2)")
                conn.execute("INSERT INTO estimate_line_items(id,estimate_id) VALUES(31,30)")
                conn.execute(
                    "INSERT INTO job_progress_settings(id,job_id,linked_estimate_id) VALUES(32,2,30)"
                )
                conn.execute("INSERT INTO job_dwelling_progress(id,job_id) VALUES(33,2)")
                conn.execute(
                    "INSERT INTO job_external_progress(id,job_id,estimate_line_id) VALUES(34,2,31)"
                )

                conn.execute("INSERT INTO material_entries(id,job_id) VALUES(40,2)")
                conn.execute("INSERT INTO purchase_orders(id,job_id) VALUES(41,2)")
                conn.execute(
                    "INSERT INTO purchase_order_lines(id,purchase_order_id,material_entry_id) VALUES(42,41,40)"
                )
                conn.execute(
                    "INSERT INTO supplier_invoices(id,job_id,purchase_order_id) VALUES(43,2,41)"
                )
                conn.execute(
                    "INSERT INTO supplier_invoice_lines(id,supplier_invoice_id,matched_po_line_id) VALUES(44,43,42)"
                )

                conn.execute("INSERT INTO job_progress_snapshots(id,job_id) VALUES(50,2)")
                conn.execute("INSERT INTO field_forms(id,job_id) VALUES(51,2)")
                conn.execute("INSERT INTO offline_sync_events(id,job_id) VALUES('sync',2)")
                conn.execute("INSERT INTO paint_systems(id,job_id) VALUES('paint',2)")
                conn.execute("INSERT INTO colour_approvals(id,job_id) VALUES('colour',2)")
                conn.execute("INSERT INTO plan_evidence(id,job_id) VALUES('evidence',2)")
                conn.execute("INSERT INTO drawing_revisions(id,job_id) VALUES('revision',2)")
                conn.execute("INSERT INTO variation_suggestions(id,job_id) VALUES('variation',2)")
                conn.execute("INSERT INTO handover_packs(id,job_id) VALUES('handover',2)")
                conn.commit()

                # Mirror the important parent-first portions of the current
                # pb_jobhub_app._delete_job_rows sequence. These used to fail.
                conn.execute(
                    "DELETE FROM estimate_line_items WHERE estimate_id IN (SELECT id FROM estimate_working_sheets WHERE job_id=2)"
                )
                self.assertIsNone(
                    conn.execute("SELECT estimate_line_id FROM job_external_progress WHERE id=34").fetchone()[0]
                )
                conn.execute("DELETE FROM estimate_working_sheets WHERE job_id=2")
                self.assertIsNone(
                    conn.execute("SELECT linked_estimate_id FROM job_progress_settings WHERE id=32").fetchone()[0]
                )

                conn.execute("DELETE FROM timesheet_entries WHERE job_id=2")
                self.assertIsNone(
                    conn.execute("SELECT submitted_timesheet_id FROM field_clock_entries WHERE id=22").fetchone()[0]
                )

                conn.execute("DELETE FROM material_entries WHERE job_id=2")
                self.assertIsNone(
                    conn.execute("SELECT material_entry_id FROM purchase_order_lines WHERE id=42").fetchone()[0]
                )

                # The final jobs delete must clean every newer job-owned table
                # before SQLite enforces their restrictive job_id foreign keys.
                conn.execute("DELETE FROM jobs WHERE id=2")
                conn.commit()

                self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs WHERE id=2").fetchone()[0], 0)
                for table in (
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
                    count = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE job_id=2").fetchone()[0] if table not in {
                        "purchase_order_lines", "supplier_invoice_lines"
                    } else conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    self.assertEqual(count, 0, table)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
