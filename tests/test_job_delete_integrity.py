from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from jobhub_delete_integrity import _ensure_postgres, _is_postgres_connection, ensure_job_delete_integrity


class JobDeleteIntegrityRoutingTests(unittest.TestCase):
    def test_sqlite_is_not_classified_as_postgres(self):
        conn = sqlite3.connect(":memory:")
        try:
            self.assertFalse(_is_postgres_connection(conn))
        finally:
            conn.close()

    def test_sqlite_partial_schema_is_left_untouched(self):
        """V4/local tests may intentionally create only a subset of JobHub tables."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jobhub.sqlite"

            def connect():
                return sqlite3.connect(path)

            conn = connect()
            try:
                conn.execute("CREATE TABLE paint_systems(id TEXT PRIMARY KEY, job_id INTEGER NOT NULL)")
                conn.commit()
            finally:
                conn.close()

            self.assertTrue(ensure_job_delete_integrity(connect))

            conn = connect()
            try:
                triggers = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'pb_jobhub_%'"
                ).fetchall()
                self.assertEqual(triggers, [])
                conn.execute("INSERT INTO paint_systems(id,job_id) VALUES('p1',1)")
                conn.commit()
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM paint_systems").fetchone()[0], 1)
            finally:
                conn.close()

    def test_postgres_job_cleanup_includes_takeoff_pack_imports(self):
        """Permanent job delete must remove take-off pack import rows before jobs."""

        class RecordingCursor:
            def __init__(self):
                self.statements: list[str] = []

            def execute(self, statement, params=None):
                self.statements.append(str(statement))

        cur = RecordingCursor()
        _ensure_postgres(cur)
        sql = "\n".join(cur.statements)
        self.assertIn("to_regclass('takeoff_pack_imports')", sql)
        self.assertIn("DELETE FROM takeoff_pack_imports WHERE job_id = $1", sql)


if __name__ == "__main__":
    unittest.main()
