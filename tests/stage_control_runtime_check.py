"""Populated Streamlit smoke check for staged production and staff requests."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA_DIR = tempfile.mkdtemp(prefix="jobhub_stage_control_")
ADMIN_PASSWORD = "JH$StageControl#84Zp"
EMPLOYEE_PASSWORD = "JH$FieldCheck#63Qv"
os.environ["DATA_DIR"] = DATA_DIR
os.environ["JOBHUB_BOOTSTRAP_ADMIN_PASSWORD"] = ADMIN_PASSWORD
os.environ.pop("DATABASE_URL", None)

from jobhub_core import hash_password
from streamlit.testing.v1 import AppTest


def login(username: str, password: str) -> AppTest:
    app = AppTest.from_file(str(ROOT / "pb_jobhub_app.py"), default_timeout=90)
    app.run(timeout=90)
    assert not app.exception, [item.value for item in app.exception]
    app.text_input[0].set_value(username)
    app.text_input[1].set_value(password)
    app.button[0].click()
    app.run(timeout=90)
    assert not app.exception, [item.value for item in app.exception]
    return app


admin = login("admin", ADMIN_PASSWORD)
database_path = Path(DATA_DIR) / "jobhub.db"
connection = sqlite3.connect(database_path)
connection.execute("PRAGMA foreign_keys=ON")
cursor = connection.cursor()
admin_user_id = cursor.execute(
    "SELECT id FROM app_users WHERE username='admin'"
).fetchone()[0]
cursor.execute(
    "UPDATE app_users SET must_change_password=0 WHERE id=?",
    (admin_user_id,),
)
admin_user = dict(admin.session_state["user"])
admin_user["must_change_password"] = False
admin.session_state["user"] = admin_user
cursor.execute(
    "INSERT INTO builders_clients(type,name) VALUES (?,?)",
    ("Builder", "Stage Control Test Builder"),
)
builder_id = cursor.lastrowid
cursor.execute(
    """
    INSERT INTO jobs
    (job_no,job_name,builder_client_id,site_address,status,start_date,end_date,contract_value)
    VALUES (?,?,?,?,?,?,?,?)
    """,
    (
        "PB-STAGE-TEST",
        "Stage Control Runtime Check",
        builder_id,
        "Nambour QLD",
        "In Progress",
        date.today().isoformat(),
        (date.today() + timedelta(days=14)).isoformat(),
        50000,
    ),
)
job_id = cursor.lastrowid
cursor.execute(
    """
    INSERT INTO employees(name,role,status,base_hourly_rate,rate_plus_10)
    VALUES (?,?,?,?,?)
    """,
    ("Runtime Ian", "Leading Hand", "Active", 50, 55),
)
employee_id = cursor.lastrowid
cursor.execute(
    """
    INSERT INTO app_users(username,password_hash,role,employee_id,active,notes,must_change_password)
    VALUES (?,?,?,?,?,?,?)
    """,
    ("runtime_ian", hash_password(EMPLOYEE_PASSWORD), "employee", employee_id, 1, "Runtime check", 0),
)
employee_user_id = cursor.lastrowid

stage_specs = [
    ("External Upper — Scaffold", 1, 25),
    ("External Lower", 2, 20),
    ("Internal 1", 3, 25),
    ("Internal 2", 4, 20),
    ("Touch-ups", 5, 10),
]
stage_ids = []
now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
for stage_name, sequence, percent in stage_specs:
    cursor.execute(
        """
        INSERT INTO job_stages
        (job_id,stage_name,sequence_order,job_percent,status,start_date,end_date,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            job_id,
            stage_name,
            sequence,
            percent,
            "In Progress" if sequence == 1 else "Planned",
            date.today().isoformat(),
            (date.today() + timedelta(days=sequence * 2)).isoformat(),
            now,
            now,
        ),
    )
    stage_ids.append(cursor.lastrowid)

cursor.execute(
    """
    INSERT INTO estimate_working_sheets
    (job_id,estimate_no,estimate_date,revision,status,labour_hours,labour_rate,
     margin_percent,contingency_percent,gst_percent,total_ex_gst,total_inc_gst,
     production_day_hours,production_value_low,production_value_target,production_value_high,
     pricing_method,created_at,updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """,
    (
        job_id,
        "PB-STAGE-EST-01",
        date.today().isoformat(),
        "Rev 1",
        "Approved",
        444.44,
        60,
        0,
        0,
        10,
        50000,
        55000,
        8,
        800,
        900,
        1000,
        "Production Target Included",
        now,
        now,
    ),
)
estimate_id = cursor.lastrowid
cursor.execute(
    """
    INSERT INTO estimate_line_items
    (estimate_id,job_stage_id,production_tracking_enabled,section,item_description,
     qty,unit,unit_rate,line_total,substrate,work_location,coating_system)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """,
    (
        estimate_id,
        stage_ids[0],
        1,
        "External",
        "External upper rendered walls",
        250,
        "m²",
        50,
        12500,
        "Render",
        "External Upper",
        "Primer plus two topcoats",
    ),
)
line_id = cursor.lastrowid
cursor.execute(
    """
    INSERT INTO estimate_baselines
    (job_id,estimate_id,baseline_name,estimate_no,revision,total_ex_gst,total_inc_gst,
     labour_hours,production_day_hours,production_value_target,active,locked_at,locked_by)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """,
    (
        job_id,
        estimate_id,
        "Accepted Rev 1",
        "PB-STAGE-EST-01",
        "Rev 1",
        50000,
        55000,
        444.44,
        8,
        900,
        1,
        now,
        "admin",
    ),
)
baseline_id = cursor.lastrowid
cursor.execute(
    """
    INSERT INTO estimate_baseline_lines
    (baseline_id,source_line_id,job_stage_id,stage_name,section,item_description,
     qty,unit,unit_rate,line_total,substrate,work_location,coating_system,production_tracking_enabled)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """,
    (
        baseline_id,
        line_id,
        stage_ids[0],
        stage_specs[0][0],
        "External",
        "External upper rendered walls",
        250,
        "m²",
        50,
        12500,
        "Render",
        "External Upper",
        "Primer plus two topcoats",
        1,
    ),
)
cursor.execute(
    """
    INSERT INTO timesheet_entries
    (job_id,job_stage_id,employee_id,work_date,start_time,finish_time,total_hours,status)
    VALUES (?,?,?,?,?,?,?,?)
    """,
    (job_id, stage_ids[0], employee_id, date.today().isoformat(), "07:00", "15:00", 64, "Approved"),
)
cursor.execute(
    """
    INSERT INTO stage_progress_updates
    (job_id,job_stage_id,estimate_line_item_id,update_date,reported_by,completed_quantity,
     unit,unit_rate_snapshot,crew_hours,crew_names,work_type,substrate,coating_system,
     blocker_type,blocker_status,blocker_notes,created_at,updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """,
    (
        job_id,
        stage_ids[0],
        line_id,
        date.today().isoformat(),
        "runtime_ian",
        30,
        "m²",
        50,
        24,
        "Runtime Ian",
        "External",
        "Render",
        "Primer plus two topcoats",
        "Scaffold / Access",
        "Active",
        "Scaffold handover required",
        now,
        now,
    ),
)
cursor.execute(
    """
    INSERT INTO staff_requests
    (requested_by_user_id,employee_id,job_id,job_stage_id,request_type,title,instructions,
     priority,due_at,status,requested_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """,
    (
        admin_user_id,
        employee_id,
        job_id,
        stage_ids[0],
        "Stage progress update",
        "Submit scaffold-stage progress",
        "Enter m² completed and attach site photos.",
        "High",
        (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
        "Requested",
        now,
    ),
)
request_id = cursor.lastrowid
cursor.execute(
    """
    INSERT INTO app_notifications
    (recipient_user_id,event_type,title,message,job_id,entity_type,entity_id,created_by,created_at,read_at)
    VALUES (?,?,?,?,?,?,?,?,?,?)
    """,
    (
        employee_user_id,
        "staff_request",
        "Submit scaffold-stage progress",
        "Enter m² completed and attach site photos.",
        job_id,
        "staff_request",
        str(request_id),
        "admin",
        now,
        "",
    ),
)
connection.commit()
connection.close()

for route in ("Job Folders", "Staff Requests", "Estimate Working Sheet", "Job Progress Tracker"):
    admin.session_state["go_to_menu"] = route
    admin.run(timeout=90)
    assert not admin.exception, f"{route}: {[item.value for item in admin.exception]}"

metric_labels = {metric.label for metric in admin.metric}
assert "Take-off Target Hours" in metric_labels or "Actual Job Progress" in metric_labels

employee = login("runtime_ian", EMPLOYEE_PASSWORD)
employee.session_state["go_to_menu"] = "Employee Portal"
employee.run(timeout=90)
assert not employee.exception, [item.value for item in employee.exception]
assert any("My Requests" in str(title.value) for title in employee.subheader)
request_text = list(employee.markdown) + list(employee.info)
assert any("Submit scaffold-stage progress" in str(value.value) for value in request_text)

print("PASS: staged baseline, alerts, progress, claims UI and employee staff requests rendered with populated data.")
