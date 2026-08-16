"""GPS capture for field clocking: schema, migration, ingest, and display.

Field Mode must record where staff were when they clocked in and clocked out.
This suite guards the schema contract (four REAL columns), the migration that
adds them to older databases, the browser-GPS ingest that pre-fills the inputs,
and the display helpers. The heavy monolith is imported only for its small,
self-contained GPS helpers; nothing here touches a live database beyond an
in-memory SQLite file.
"""
from __future__ import annotations

import ast
import sqlite3
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import jobhub_enterprise as enterprise

MODULE_PATH = ROOT / "jobhub_enterprise.py"
GPS_COLUMNS = ("clock_in_lat", "clock_in_lng", "clock_out_lat", "clock_out_lng")


class FieldClockGpsSchemaTests(unittest.TestCase):
    def test_source_parses(self):
        ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))

    def test_ddl_creates_all_gps_columns_as_real(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        block = src[
            src.index("CREATE TABLE IF NOT EXISTS field_clock_entries"):src.index(
                "CREATE TABLE IF NOT EXISTS job_progress_snapshots"
            )
        ]
        for column in GPS_COLUMNS:
            self.assertIn(f"{column} REAL,", block)

    def test_migration_is_wired_into_ensure_enterprise_schema(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("def _ensure_field_clock_gps_columns(conn: Any)", src)
        self.assertIn("_ensure_field_clock_gps_columns(conn)", src)

    def test_clock_in_insert_records_gps_fix(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        insert = src[src.index("INSERT INTO field_clock_entries"):]
        insert = insert[: insert.index(")", insert.index("VALUES")) + 1]
        self.assertIn("clock_in_lat, clock_in_lng", insert)
        self.assertIn("VALUES (?, ?, ?, ?, ?, 'Active', ?, ?, ?, ?)", insert)

    def test_clock_off_update_records_gps_fix(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        update = src[src.index("UPDATE field_clock_entries"):]
        update = update[: update.index(")", update.index("SET")) + 1]
        self.assertIn("clock_out_lat = ?, clock_out_lng = ?", update)
        self.assertIn("WHERE id = ?", update)

    def test_history_query_and_display_include_locations(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        history = src[src.index("#### My clock history"):]
        self.assertIn('c.clock_in_lat AS "Clock-in Lat"', history)
        self.assertIn('c.clock_out_lng AS "Clock-out Lng"', history)
        self.assertIn('history["Clock-in location"] = history.apply(', history)
        self.assertIn('history["Clock-out location"] = history.apply(', history)


class FieldClockGpsMigrationTests(unittest.TestCase):
    def _legacy_table(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE field_clock_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                job_id INTEGER NOT NULL,
                clock_in TEXT NOT NULL,
                clock_out TEXT,
                status TEXT DEFAULT 'Active'
            )
            """
        )
        return conn

    def _column_types(self, conn):
        return {
            row[1]: row[2]
            for row in conn.execute("PRAGMA table_info(field_clock_entries)").fetchall()
        }

    def test_migration_adds_gps_columns_to_existing_table(self):
        conn = self._legacy_table()
        enterprise._ensure_field_clock_gps_columns(conn)
        columns = self._column_types(conn)
        for column in GPS_COLUMNS:
            self.assertEqual(columns[column], "REAL")
        conn.close()

    def test_migration_is_idempotent(self):
        conn = self._legacy_table()
        enterprise._ensure_field_clock_gps_columns(conn)
        enterprise._ensure_field_clock_gps_columns(conn)
        self.assertEqual(set(GPS_COLUMNS), set(self._column_types(conn)) - {
            "id", "employee_id", "job_id", "clock_in", "clock_out", "status"
        })
        conn.close()

    def test_migration_preserves_existing_rows(self):
        conn = self._legacy_table()
        conn.execute(
            "INSERT INTO field_clock_entries (employee_id, job_id, clock_in) VALUES (1, 1, 'x')"
        )
        enterprise._ensure_field_clock_gps_columns(conn)
        row = conn.execute(
            "SELECT clock_in_lat, clock_in_lng, clock_out_lat, clock_out_lng "
            "FROM field_clock_entries WHERE id = 1"
        ).fetchone()
        self.assertEqual(row, (None, None, None, None))
        conn.close()


class FieldClockGpsHelperTests(unittest.TestCase):
    def test_gps_display_formats_and_handles_missing(self):
        self.assertEqual(enterprise._gps_display(-26.531111, 152.954444), "-26.531111, 152.954444")
        self.assertEqual(enterprise._gps_display(None, 152.954444), "")
        self.assertEqual(enterprise._gps_display(float("nan"), 152.954444), "")
        self.assertEqual(enterprise._gps_display("oops", 152.954444), "")

    def test_capture_html_injects_prefix_and_param_and_guards_geolocation(self):
        html = enterprise._gps_capture_html("field_mode_clock_in")
        self.assertIn("field_mode_clock_in:", html)
        self.assertIn(enterprise._GPS_QUERY_PARAM, html)
        self.assertIn("try { hasGeo = !!navigator.geolocation; } catch", html)
        self.assertIn('deliver("unavailable")', html)
        self.assertIn("enableHighAccuracy: true", html)

    def test_ingest_query_param_prefills_session_state(self):
        class FakeQueryParams(dict):
            def get(self, key, default=None):
                return dict.get(self, key, default)

        query_params = FakeQueryParams(
            {enterprise._GPS_QUERY_PARAM: "field_mode_clock_in:-26.531111,152.954444"}
        )
        session_state = {}
        fake_st = types.SimpleNamespace(query_params=query_params, session_state=session_state)
        with mock.patch.object(enterprise, "st", fake_st):
            enterprise._ingest_gps_query_param()
        self.assertEqual(session_state["field_mode_clock_in_lat"], -26.531111)
        self.assertEqual(session_state["field_mode_clock_in_lng"], 152.954444)
        self.assertEqual(session_state["field_mode_clock_in_gps_status"], "located")
        self.assertNotIn(enterprise._GPS_QUERY_PARAM, query_params)

    def test_ingest_query_param_flags_unavailable_fix(self):
        class FakeQueryParams(dict):
            def get(self, key, default=None):
                return dict.get(self, key, default)

        query_params = FakeQueryParams(
            {enterprise._GPS_QUERY_PARAM: "field_mode_clock_in:unavailable"}
        )
        session_state = {}
        fake_st = types.SimpleNamespace(query_params=query_params, session_state=session_state)
        with mock.patch.object(enterprise, "st", fake_st):
            enterprise._ingest_gps_query_param()
        self.assertEqual(session_state["field_mode_clock_in_gps_status"], "unavailable")
        self.assertNotIn("field_mode_clock_in_lat", session_state)

    def test_render_field_mode_wires_gps_capture_into_both_actions(self):
        src = MODULE_PATH.read_text(encoding="utf-8")
        field_mode = src[src.index("def render_field_mode"):src.index("def _database_table_names")]
        self.assertIn('_gps_capture("field_mode_clock_in", "clock on")', field_mode)
        self.assertIn('_gps_capture("field_mode_clock_off", "clock off")', field_mode)
        self.assertIn('st.session_state.get("field_mode_clock_off_lat")', field_mode)
        self.assertIn('st.session_state.get("field_mode_clock_off_lng")', field_mode)

    def test_pandas_is_imported_for_gps_display(self):
        self.assertTrue(hasattr(pd, "isna"))


if __name__ == "__main__":
    unittest.main()
