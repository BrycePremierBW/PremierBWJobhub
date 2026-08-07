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

from datetime import date, timedelta

# --- Friday week start checks (weeks run Friday to Thursday) ---
assert app._week_start_friday(date(2026, 8, 7)) == date(2026, 8, 7), "Friday starts the week"
assert app._week_start_friday(date(2026, 8, 10)) == date(2026, 8, 7), "Monday -> previous Friday"
assert app._week_start_friday(date(2026, 8, 6)) == date(2026, 7, 31), "Thursday -> previous Friday"
assert app._week_start_friday(date(2026, 8, 1)) == date(2026, 7, 31), "Saturday -> same pay week"

# --- Seed employees: Ada (active), Bea (active), Dee (active, no hours), Cyd (inactive) ---
conn = app.connect()
cur = conn.cursor()
employees = {}
for name, status in (("Ada", "Active"), ("Bea", "Active"), ("Dee", "Active"), ("Cyd", "Inactive")):
    cur.execute("INSERT INTO employees (name, role, status) VALUES (?, ?, ?)", (name, "Painter", status))
    employees[name] = int(cur.lastrowid)
cur.execute(
    "INSERT INTO jobs (job_no, job_name, site_address, status) VALUES (?, ?, ?, ?)",
    ("PB-SUM-1", "Summary Job", "5 Site St", "Active"),
)
job_id = int(cur.lastrowid)
cur.execute(
    "INSERT INTO job_stages (job_id, stage_name, sequence_order) VALUES (?, ?, ?)",
    (job_id, "Internal", 1),
)
stage_id = int(cur.lastrowid)
conn.commit()

today = app.jobhub_today()
from datetime import time

rows = [
    (employees["Ada"], today, 8, "Painting"),
    (employees["Ada"], today - timedelta(days=1), 8, "Painting"),
    (employees["Bea"], today, 5, "Caulking"),
]
for emp_id, work_date, hours, work_type in rows:
    cur.execute(
        "INSERT INTO timesheet_entries (job_id, job_stage_id, employee_id, work_date, "
        "start_time, finish_time, break_minutes, total_hours, work_type, "
        "submitted_by, submitted_at, status, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, stage_id, emp_id, work_date.isoformat(), "07:00", "15:00", 0, hours,
         work_type, "tester", app.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Submitted", ""),
    )
conn.commit()
conn.close()

import pb_jobhub_visual_scheduler as scheduler
scheduler.USE_POSTGRES = False
scheduler.init_linked_schema()

# Roster: Ada scheduled today + yesterday (both submitted -> complete),
# Bea scheduled today (submitted) + tomorrow (not submitted -> missing 1).
schedule_plan = [
    (employees["Ada"], today, 8),
    (employees["Ada"], today - timedelta(days=1), 8),
    (employees["Bea"], today, 8),
    (employees["Bea"], today + timedelta(days=1), 8),
]
for emp_id, sched_day, planned in schedule_plan:
    created, msg = scheduler.add_assignment(
        employee_id=emp_id, job_id=job_id, job_stage_id=stage_id,
        work_date=sched_day, start_value=time(7, 0), finish_value=time(15, 0),
        planned_hours=planned, site_role="Painting", notes="", created_by="tester",
        linked_to_job_dates=False,
    )
    assert created, msg

df = app.df_query(
    "SELECT t.id, e.name AS 'Employee', t.work_date AS 'Date', "
    "j.job_no AS 'Job No', j.job_name AS 'Job Name', "
    "COALESCE(js.stage_name, 'Whole Job') AS 'Stage', "
    "COALESCE(t.site_location, j.site_address) AS 'Site Location', "
    "t.total_hours AS 'Hours', t.work_type AS 'Work Type', "
    "COALESCE(t.status, 'Submitted') AS 'Status', t.notes AS 'Notes' "
    "FROM timesheet_entries t "
    "JOIN employees e ON e.id = t.employee_id "
    "JOIN jobs j ON j.id = t.job_id "
    "LEFT JOIN job_stages js ON js.id = t.job_stage_id "
    "WHERE t.work_date BETWEEN ? AND ?",
    ((today - timedelta(days=3)).isoformat(), (today + timedelta(days=3)).isoformat()))

all_summary = app._build_hours_summary(df, True)
by_name = {str(r["Employee"]): r for _, r in all_summary.iterrows()}
assert set(by_name.keys()) == {"Ada", "Bea", "Dee"}, sorted(by_name.keys())
assert by_name["Ada"]["Shifts"] == 2 and by_name["Ada"]["TotalHours"] == 16.0, by_name["Ada"]
assert by_name["Bea"]["Shifts"] == 1 and by_name["Bea"]["TotalHours"] == 5.0, by_name["Bea"]
assert by_name["Dee"]["Shifts"] == 0 and by_name["Dee"]["TotalHours"] == 0.0, by_name["Dee"]

only_with_hours = app._build_hours_summary(df, False)
only_names = set(str(r["Employee"]) for _, r in only_with_hours.iterrows())
assert only_names == {"Ada", "Bea"}, only_names

submission = app._employee_submission_status(
    today - timedelta(days=3), today + timedelta(days=3)
)
sub_by_name = {str(r["Employee"]): r for _, r in submission.iterrows()}
assert set(sub_by_name.keys()) == {"Ada", "Bea", "Dee"}, sorted(sub_by_name.keys())
assert int(sub_by_name["Ada"]["Scheduled"]) == 2 and int(sub_by_name["Ada"]["Missing"]) == 0, sub_by_name["Ada"]
assert int(sub_by_name["Bea"]["Scheduled"]) == 2 and int(sub_by_name["Bea"]["Missing"]) == 1, sub_by_name["Bea"]
assert int(sub_by_name["Dee"]["Scheduled"]) == 0 and int(sub_by_name["Dee"]["Missing"]) == 0, sub_by_name["Dee"]

annotated = app._annotate_submission(app._build_hours_summary(df, True), submission)
ann_by_name = {str(r["Employee"]): r for _, r in annotated.iterrows()}
assert str(ann_by_name["Ada"]["Status"]) == "Complete", ann_by_name["Ada"]
assert str(ann_by_name["Bea"]["Status"]) == "Missing 1", ann_by_name["Bea"]
assert str(ann_by_name["Dee"]["Status"]) == "Not rostered", ann_by_name["Dee"]

missing_view = annotated[annotated["Missing"] > 0]
assert set(str(r["Employee"]) for _, r in missing_view.iterrows()) == {"Bea"}, missing_view

print("WEEK_START_FRIDAY_OK:1")
print("SUBMISSION_OK:1")
print("ALL_EMPLOYEES:" + json.dumps({k: {"Shifts": int(v["Shifts"]), "Hours": float(v["TotalHours"])} for k, v in by_name.items()}, sort_keys=True))
print("SUBMISSION:" + json.dumps({k: {"Scheduled": int(v["Scheduled"]), "Missing": int(v["Missing"])} for k, v in sub_by_name.items()}, sort_keys=True))
print("STATUS:" + json.dumps({k: str(v["Status"]) for k, v in ann_by_name.items()}, sort_keys=True))
"""


class HoursSummaryIntegrationTest(unittest.TestCase):
    def test_hours_summary_friday_week_and_all_employees(self):
        temp_dir = tempfile.mkdtemp(prefix="jobhub_summary_it_")
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
        self.assertTrue(any(ln == "WEEK_START_FRIDAY_OK:1" for ln in lines), output)
        self.assertTrue(any(ln == "SUBMISSION_OK:1" for ln in lines), output)
        summary_line = next((ln for ln in lines if ln.startswith("ALL_EMPLOYEES:")), None)
        self.assertIsNotNone(summary_line, output)
        data = json.loads(summary_line[len("ALL_EMPLOYEES:"):])
        self.assertEqual(data["Ada"]["Shifts"], 2)
        self.assertEqual(data["Ada"]["Hours"], 16.0)
        self.assertEqual(data["Bea"]["Hours"], 5.0)
        self.assertEqual(data["Dee"]["Shifts"], 0)
        self.assertEqual(data["Dee"]["Hours"], 0.0)
        submission_line = next((ln for ln in lines if ln.startswith("SUBMISSION:")), None)
        self.assertIsNotNone(submission_line, output)
        sub = json.loads(submission_line[len("SUBMISSION:"):])
        self.assertEqual(sub["Ada"], {"Scheduled": 2, "Missing": 0})
        self.assertEqual(sub["Bea"], {"Scheduled": 2, "Missing": 1})
        self.assertEqual(sub["Dee"], {"Scheduled": 0, "Missing": 0})
        status_line = next((ln for ln in lines if ln.startswith("STATUS:")), None)
        self.assertIsNotNone(status_line, output)
        status = json.loads(status_line[len("STATUS:"):])
        self.assertEqual(status["Ada"], "Complete")
        self.assertEqual(status["Bea"], "Missing 1")
        self.assertEqual(status["Dee"], "Not rostered")


if __name__ == "__main__":
    unittest.main()
