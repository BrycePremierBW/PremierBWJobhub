"""Scheduler -> Dashboard sync.

An assignment saved by the visual scheduler must appear in the operational
dashboard's "Today's Staff" widget. Both sides read the shared staff_schedule
table, so this test writes through the scheduler and runs the exact dashboard
query, plus guards the timezone default of the legacy schedule forms.
"""
import importlib
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, time
from pathlib import Path

from jobhub_time import jobhub_today


class SchedulerDashboardSyncTests(unittest.TestCase):
    def setUp(self):
        streamlit_module = sys.modules.get("streamlit")
        if streamlit_module is not None and not hasattr(streamlit_module, "dialog"):
            del sys.modules["streamlit"]
        scheduler = importlib.import_module("pb_jobhub_visual_scheduler")
        self.scheduler = scheduler
        self.temp_dir = tempfile.TemporaryDirectory(prefix="jobhub_sched_dash_test_")
        self.original_path = scheduler.SQLITE_PATH
        self.original_postgres = scheduler.USE_POSTGRES
        scheduler.SQLITE_PATH = Path(self.temp_dir.name) / "jobhub.db"
        scheduler.USE_POSTGRES = False
        scheduler.init_linked_schema.clear()

        connection = sqlite3.connect(scheduler.SQLITE_PATH)
        connection.executescript(
            """
            CREATE TABLE builders_clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT
            );
            CREATE TABLE employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                role TEXT,
                phone TEXT,
                status TEXT
            );
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_no TEXT,
                job_name TEXT,
                builder_client_id INTEGER,
                site_address TEXT,
                status TEXT,
                leading_hand TEXT,
                start_date TEXT,
                end_date TEXT
            );
            CREATE TABLE job_stages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER,
                stage_name TEXT,
                sequence_order INTEGER
            );
            CREATE TABLE app_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                password_hash TEXT,
                role TEXT,
                employee_id INTEGER,
                active INTEGER
            );
            """
        )
        connection.execute("INSERT INTO builders_clients(name) VALUES ('Runtime Builder')")
        connection.execute("INSERT INTO employees(name,role,status) VALUES ('River','Painter','Active')")
        connection.execute(
            "INSERT INTO jobs(job_no,job_name,builder_client_id,status,start_date,end_date) "
            "VALUES ('PB001','First Job',1,'Active','2026-08-03','2026-08-31')"
        )
        connection.execute("INSERT INTO job_stages(job_id,stage_name,sequence_order) VALUES (1,'Internal 1',1)")
        connection.commit()
        connection.close()
        scheduler.init_linked_schema()

    def tearDown(self):
        scheduler = self.scheduler
        scheduler.init_linked_schema.clear()
        scheduler.SQLITE_PATH = self.original_path
        scheduler.USE_POSTGRES = self.original_postgres
        self.temp_dir.cleanup()

    def _dashboard_today_query(self, today_text):
        """The exact SQL used by render_operational_dashboard 'Today's Staff'."""
        connection = sqlite3.connect(self.scheduler.SQLITE_PATH)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT e.name AS "Employee", j.job_no AS "Job",
                   s.start_time AS "Start", s.finish_time AS "Finish"
            FROM staff_schedule s JOIN employees e ON e.id=s.employee_id
            LEFT JOIN jobs j ON j.id=s.job_id
            LEFT JOIN job_stages js ON js.id=s.job_stage_id
            WHERE s.schedule_date=? OR (? BETWEEN COALESCE(s.period_start,'') AND COALESCE(s.period_end,''))
            ORDER BY e.name,s.start_time
            """,
            (today_text, today_text),
        ).fetchall()
        connection.close()
        return [dict(r) for r in rows]

    def test_scheduled_staff_appear_on_dashboard_today(self):
        scheduler = self.scheduler
        today = jobhub_today()
        created, _ = scheduler.add_assignment(
            1, 1, 1, today, time(7), time(15), 8,
            "Leading Hand", "Scheduled today", "admin",
        )
        self.assertTrue(created)

        rows = self._dashboard_today_query(today.isoformat())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Employee"], "River")
        self.assertEqual(rows[0]["Job"], "PB001")

        # An assignment on a different day must not leak into today's widget.
        other = today.toordinal() - 1
        other_day = date.fromordinal(other)
        created, _ = scheduler.add_assignment(
            1, 1, 1, other_day, time(8), time(16), 8,
            "Leading Hand", "Scheduled yesterday", "admin",
        )
        self.assertTrue(created)
        rows = self._dashboard_today_query(today.isoformat())
        self.assertEqual(len(rows), 1)

    def test_legacy_schedule_forms_default_to_business_today(self):
        # UTC date.today() can be a day behind Brisbane. All schedule "today"
        # defaults must use jobhub_today() so entries reach the dashboard widget.
        root = Path(__file__).resolve().parents[1]
        app_source = (root / "pb_jobhub_app.py").read_text(encoding="utf-8")
        control_centre = (root / "jobhub" / "control_centre.py").read_text(encoding="utf-8")
        enterprise = (root / "jobhub_enterprise.py").read_text(encoding="utf-8")

        self.assertIn("today_text = jobhub_today().isoformat()", app_source)
        self.assertIn("default_from = jobhub_today() - timedelta(days=jobhub_today().weekday())", app_source)
        self.assertIn("value=jobhub_today(), key=\"schedule_single_day\"", control_centre)
        self.assertIn("default_week_end = jobhub_today()", control_centre)
        self.assertIn("value=jobhub_today(), key=\"schedule_filter_from\"", control_centre)
        self.assertIn("value=jobhub_today() + timedelta(days=7)", control_centre)
        self.assertIn("return jobhub_today().isoformat()", enterprise)


if __name__ == "__main__":
    unittest.main()
