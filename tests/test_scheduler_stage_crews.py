import importlib
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, time
from pathlib import Path

class SchedulerStageCrewTests(unittest.TestCase):
    def setUp(self):
        streamlit_module = sys.modules.get("streamlit")
        if streamlit_module is not None and not hasattr(streamlit_module, "dialog"):
            del sys.modules["streamlit"]
        scheduler = importlib.import_module("pb_jobhub_visual_scheduler")
        self.scheduler = scheduler
        self.temp_dir = tempfile.TemporaryDirectory(prefix="jobhub_scheduler_test_")
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
        connection.executemany(
            "INSERT INTO employees(name,role,status) VALUES (?,?,?)",
            [
                ("Ian", "Leading Hand", "Active"),
                ("River", "Painter", "Active"),
                ("Salty", "Painter", "Active"),
            ],
        )
        connection.executemany(
            """
            INSERT INTO jobs
            (job_no,job_name,builder_client_id,status,start_date,end_date)
            VALUES (?,?,?,?,?,?)
            """,
            [
                ("PB001", "First Job", 1, "Active", "2026-08-03", "2026-08-31"),
                ("PB002", "Second Job", 1, "Active", "2026-08-03", "2026-08-31"),
            ],
        )
        connection.executemany(
            "INSERT INTO job_stages(job_id,stage_name,sequence_order) VALUES (?,?,?)",
            [(1, "External Upper", 1), (2, "Internal 1", 1)],
        )
        connection.commit()
        connection.close()
        scheduler.init_linked_schema()

    def tearDown(self):
        scheduler = self.scheduler
        scheduler.init_linked_schema.clear()
        scheduler.SQLITE_PATH = self.original_path
        scheduler.USE_POSTGRES = self.original_postgres
        self.temp_dir.cleanup()

    def test_saved_crew_includes_lead_and_members(self):
        scheduler = self.scheduler
        crew_id = scheduler.save_scheduler_crew(
            None,
            "Ian's Crew",
            1,
            [2, 3],
            "Standard crew",
        )

        crew = scheduler.crew_for_lead(1)

        self.assertEqual(crew["id"], crew_id)
        self.assertEqual(set(crew["member_ids"]), {1, 2, 3})
        self.assertIn("River", crew["member_names"])
        self.assertIn("Salty", crew["member_names"])

    def test_clash_can_keep_new_booking_and_remove_reviewed_booking(self):
        scheduler = self.scheduler
        work_date = date(2026, 8, 4)
        created, _ = scheduler.add_assignment(
            1, 1, 1, work_date, time(7), time(15), 8,
            "Leading Hand", "First booking", "admin",
        )
        self.assertTrue(created)
        conflict = scheduler.overlapping_assignment_rows(
            1, work_date, time(7), time(15),
        )
        conflict_id = int(conflict.iloc[0]["id"])

        created, message = scheduler.add_assignment(
            1, 2, 2, work_date, time(7), time(15), 8,
            "Leading Hand", "Second booking", "admin",
        )
        self.assertFalse(created)
        self.assertIn("overlapping", message)

        replaced, message = scheduler.replace_conflicting_assignments(
            [conflict_id], 1, 2, 2, work_date, time(7), time(15), 8,
            "Leading Hand", "Second booking", "admin",
        )

        self.assertTrue(replaced)
        self.assertIn("Replaced 1", message)
        kept = scheduler.assignment_rows(work_date, work_date, employee_id=1)
        self.assertEqual(len(kept), 1)
        self.assertEqual(int(kept.iloc[0]["job_id"]), 2)
        self.assertEqual(int(kept.iloc[0]["job_stage_id"]), 2)


if __name__ == "__main__":
    unittest.main()
