import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

INTEGRATION_SCRIPT = r"""
import json, os
os.environ["DATA_DIR"] = os.environ["INTEGRATION_DATA_DIR"]
import pb_jobhub_app as app
app.USE_POSTGRES = False
app.init_db()
app.apply_schema_migrations()
app.init_linked_schema()
app.connect().close()

today = app.jobhub_today()
from datetime import timedelta, date
leave_day = (today - timedelta(days=2))
covered_day = (today - timedelta(days=3))

conn = app.connect()
cur = conn.cursor()
cur.execute(
    "INSERT INTO employees (name, role, status) VALUES (?, ?, ?)",
    ("Missy", "Painter", "Active"),
)
employee_id = int(cur.lastrowid)
cur.execute(
    "INSERT INTO jobs (job_no, job_name, site_address, status) VALUES (?, ?, ?, ?)",
    ("PB-MISS-1", "Missed Shift Job", "9 Test Rd", "Active"),
)
job_id = int(cur.lastrowid)
cur.execute(
    "INSERT INTO job_stages (job_id, stage_name, sequence_order) VALUES (?, ?, ?)",
    (job_id, "External", 1),
)
stage_id = int(cur.lastrowid)
conn.commit()
conn.close()

from datetime import time
import pb_jobhub_visual_scheduler as scheduler
scheduler.USE_POSTGRES = False
scheduler.init_linked_schema()

# Scheduled shift with no timesheet -> should be detected as missed.
created, _ = scheduler.add_assignment(
    job_id, stage_id, employee_id, today, time(7), time(15), 8,
    "Painting", "Missed day one", "admin",
)
assert created, "expected first assignment to be created"

# Second scheduled day covered by an approved leave request -> not missed.
created, _ = scheduler.add_assignment(
    job_id, stage_id, employee_id, leave_day, time(7), time(15), 8,
    "Painting", "On leave", "admin",
)
assert created, "expected second assignment to be created"
conn = app.connect()
cur = conn.cursor()
cur.execute(
    "INSERT INTO staff_leave_requests (employee_id, start_date, end_date, leave_type, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
    (employee_id, leave_day.isoformat(), leave_day.isoformat(), "Annual Leave", "Approved", app.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
)
conn.commit()
conn.close()

# Third scheduled day already has a timesheet -> not missed.
created, _ = scheduler.add_assignment(
    job_id, stage_id, employee_id, covered_day, time(7), time(15), 8,
    "Painting", "Already logged", "admin",
)
assert created, "expected third assignment to be created"
conn = app.connect()
cur = conn.cursor()
cur.execute(
    "INSERT INTO timesheet_entries (job_id, job_stage_id, employee_id, work_date, start_time, finish_time, break_minutes, total_hours, work_type, submitted_by, submitted_at, status, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    (job_id, stage_id, employee_id, covered_day.isoformat(), "07:00", "15:00", 0, 8, "Painting", "tester", app.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Submitted", ""),
)
conn.commit()
conn.close()

missed = app.missed_timesheet_days(employee_id, lookback_days=10)
print("MISSED_COUNT:" + str(len(missed)))
for item in missed:
    print("MISSED_ROW:" + json.dumps(
        {k: v for k, v in item.items() if isinstance(v, (int, float, str, bool))},
        default=str,
    ))
"""


class MissedTimesheetIntegrationTest(unittest.TestCase):
    def test_missed_timesheet_detection(self):
        temp_dir = tempfile.mkdtemp(prefix="jobhub_missed_it_")
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        data_dir = os.path.join(temp_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        env = dict(os.environ)
        env["DATA_DIR"] = data_dir
        env["INTEGRATION_DATA_DIR"] = data_dir
        completed = subprocess.run(
            [sys.executable, "-c", INTEGRATION_SCRIPT],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        output = completed.stdout or ""
        if completed.returncode != 0:
            self.fail("subprocess failed:\n" + (completed.stderr or "") + "\n" + output)
        lines = output.strip().splitlines()
        count_line = next((ln for ln in lines if ln.startswith("MISSED_COUNT:")), None)
        self.assertIsNotNone(count_line, "no count line in output:\n" + output)
        self.assertEqual(int(count_line[len("MISSED_COUNT:"):]), 1)
        row_lines = [ln for ln in lines if ln.startswith("MISSED_ROW:")]
        self.assertEqual(len(row_lines), 1)
        missed = json.loads(row_lines[0][len("MISSED_ROW:"):])
        self.assertEqual(missed["job_no"], "PB-MISS-1")
        self.assertEqual(missed["start_time"], "07:00")
        self.assertEqual(missed["finish_time"], "15:00")
        self.assertEqual(missed["planned_hours"], 8)
        self.assertEqual(missed["stage_name"], "External")


if __name__ == "__main__":
    unittest.main()
