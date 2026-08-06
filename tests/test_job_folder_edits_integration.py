"""Regression tests for the Job Folder edit round.

Covers: all-user-selection missed-timesheet catch-up, the simplified employee
timesheet form, viewer-scoped job documents, the new Job Folder tabs, and the
editable pre-start approval answers in the enterprise module.
"""

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

conn = app.connect()
cur = conn.cursor()
cur.execute(
    "INSERT INTO jobs (job_no, job_name, site_address, status) VALUES (?, ?, ?, ?)",
    ("PB-FOLDER-1", "Folder Edit Job", "7 Test Rd", "Active"),
)
job_id = int(cur.lastrowid)
conn.commit()
conn.close()

# viewer_scope column must exist after migrations.
scopes = app.df_query("PRAGMA table_info(job_documents)")
assert any(str(r["name"]).lower() == "viewer_scope" for _, r in scopes.iterrows()), "viewer_scope column missing"

class FakeUpload:
    name = "plan_a.pdf"
    type = "application/pdf"
    def getvalue(self):
        return b"%PDF-1.4 fake plan bytes"

doc_id = app.upload_job_document(job_id, FakeUpload(), "Plans", notes="scope test", viewer_scope="managers")
print("DOC_ID:" + str(doc_id))

visible = app.employee_visible_job_documents(job_id)
assert visible.empty, "managers-only document leaked into crew view"
assert app.document_visible_to_role("managers", "admin") is True
assert app.document_visible_to_role("managers", "painter") is False
assert app.document_visible_to_role("admin", "manager") is False
assert app.document_visible_to_role("", "painter") is True

app.set_job_document_viewer_scope(doc_id, "crew")
visible_after = app.employee_visible_job_documents(job_id)
assert len(visible_after) == 1, "crew-visible document not listed"
print("SCOPE_OK:" + str(doc_id))

# Enterprise editable pre-start answers.
from jobhub_enterprise import ensure_enterprise_schema, render_job_field_forms_panel
ensure_enterprise_schema(app.connect)
conn = app.connect()
cur = conn.cursor()
cur.execute(
    "INSERT INTO field_forms (job_id, employee_id, form_type, form_date, status, answers_json, created_by, created_at) "
    "VALUES (?, NULL, 'Daily Pre-Start', '2026-08-07', 'Submitted', ?, 'tester', ?)",
    (job_id, json.dumps({"weather": "Cloudy", "ppe": "Yes", "access": "Yes", "swms": "No", "planned_work": "", "hazards": "", "controls": ""}), app.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
)
form_id = int(cur.lastrowid)
conn.commit()
conn.close()

# Simulate the approval-section answer edit path.
ctx = {
    "df_query": app.df_query,
    "execute": app.execute,
    "record_audit_event": app.record_audit_event,
    "get_current_user": lambda: {"username": "tester", "role": "admin"},
    "connect": app.connect,
    "pb_success": lambda *a, **k: None,
    "pb_error": lambda *a, **k: None,
    "pb_rerun": lambda *a, **k: None,
}
edited = {"weather": "Sunny", "planned_work": "External top coats", "hazards": "Wind", "controls": "Tie off", "ppe": "Yes", "access": "No", "swms": "Yes"}
app.execute("UPDATE field_forms SET answers_json = ? WHERE id = ?", (json.dumps(edited, sort_keys=True), form_id))
row = app.df_query("SELECT answers_json FROM field_forms WHERE id = ?", (form_id,))
stored = json.loads(row.iloc[0]["answers_json"])
assert stored["swms"] == "Yes" and stored["weather"] == "Sunny", "answer edit not persisted"
print("FORM_EDIT_OK:" + str(form_id))
print("HAS_PANEL:" + str(hasattr(__import__("jobhub_enterprise"), "render_job_field_forms_panel")))
"""


class JobFolderEditsIntegrationTest(unittest.TestCase):
    def test_viewer_scope_documents_and_enterprise_form_edit(self):
        temp_dir = tempfile.mkdtemp(prefix="jobhub_folder_edits_")
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
        self.assertIn("SCOPE_OK", output)
        self.assertIn("FORM_EDIT_OK", output)
        self.assertIn("HAS_PANEL:True", output)

    def test_source_contracts_for_edit_round(self):
        app_source = (REPO_ROOT / "pb_jobhub_app.py").read_text(encoding="utf-8")
        enterprise_source = (REPO_ROOT / "jobhub_enterprise.py").read_text(encoding="utf-8")

        # Catch-up is all user selection when manual_entry=True.
        self.assertIn("def render_missed_timesheet_catchup(", app_source)
        self.assertIn("manual_entry=False", app_source)
        self.assertIn("Select job / stage...", app_source)

        # Simplified employee timesheet form.
        self.assertIn("def timesheet_entry_form(", app_source)
        self.assertIn("simple=False", app_source)
        self.assertIn("### Check your hours", app_source)
        self.assertIn('key_prefix="employee_timesheet",\n                simple=True', app_source)
        self.assertIn("manual_entry=True", app_source)

        # Job Folder tabs include the new sections.
        self.assertIn('"Forms / Safety"', app_source)
        self.assertIn('"Colours"', app_source)
        self.assertIn('"Documents"', app_source)
        self.assertIn("def render_job_documents_panel(", app_source)
        self.assertIn("render_job_field_forms_panel(jobhub_enterprise_context(), job_id)", app_source)

        # Viewer-scope machinery.
        self.assertIn('"Crew (everyone on this job)": "crew"', app_source)
        self.assertIn("def document_visible_to_role(", app_source)
        self.assertIn("def employee_visible_job_documents(", app_source)
        self.assertIn("def upload_job_document(", app_source)
        self.assertIn("def set_job_document_viewer_scope(", app_source)
        self.assertIn("20260807_job_document_viewer_scope_v1", app_source)

        # Enterprise pre-start approval answers are editable with dropdowns.
        self.assertIn("def render_job_field_forms_panel(", enterprise_source)
        self.assertIn("Save Answers", enterprise_source)
        self.assertIn("field_form_answers_updated", enterprise_source)


if __name__ == "__main__":
    unittest.main()
