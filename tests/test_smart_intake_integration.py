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
app.connect().close()

from jobhub.smart_intake import parse_intake_upload, parts_to_intake_package

scope = (
    b"PROJECT: INT-1\n"
    b"1. 200m2 interior walls, two coats, white\n"
    b"2. 60 lm skirtings, paint\n"
    b"3. 40 m2 ceiling, one coat\n"
)
part = parse_intake_upload(scope, "scope.txt")
parsed = parts_to_intake_package([part], source_name="integration_intake.zip")

conn = app.connect()
cur = conn.cursor()
cur.execute(
    "INSERT INTO jobs (job_no, job_name, site_address, status) VALUES (?, ?, ?, ?)",
    ("JOB-INT-1", "Integration Job", "1 Test St", "Not Started"),
)
job_id = int(cur.lastrowid)
now = app.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
summary = dict(parsed["summary"])
summary["labour_hours"] = 0
summary["material_allowance"] = 0
est_id = app._takeoff_insert_id(
    cur,
    app._takeoff_new_estimate_insert_sql(),
    app._takeoff_new_estimate_values(job_id, "JOB-INT-1-E1", "1", summary, now),
)
conn.commit()
conn.close()

result = app.attach_intake_package_to_job(
    job_id, parsed, merge=True, import_materials=True, attach_documents=True
)
print("RESULT_JSON:" + json.dumps(
    {k: v for k, v in result.items() if isinstance(v, (int, float, str, bool))}, default=str
))

bad = dict(parsed)
bad["summary"] = dict(parsed["summary"])
bad["summary"]["job_no"] = "OTHER-JOB"
try:
    app.attach_intake_package_to_job(job_id, bad, merge=True)
    print("GUARD:NO_ERROR")
except ValueError as exc:
    print("GUARD:" + str(exc)[:60])
"""


class SmartIntakeIntegrationTest(unittest.TestCase):
    def test_attach_helper_end_to_end(self):
        temp_dir = tempfile.mkdtemp(prefix="jobhub_intake_it_")
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
        result_line = next((ln for ln in lines if ln.startswith("RESULT_JSON:")), None)
        self.assertIsNotNone(result_line, "no result line in output:\n" + output)
        result = json.loads(result_line[len("RESULT_JSON:"):])
        self.assertEqual(result["job_no"], "JOB-INT-1")
        self.assertEqual(result["estimate_mode"], "merged")
        self.assertGreater(result["line_count"], 0)
        self.assertGreater(result["labour_hours"], 0)
        self.assertGreaterEqual(result["material_allowance"], 0)
        guard_line = next((ln for ln in lines if ln.startswith("GUARD:")), "")
        self.assertIn("JOB-NUMBER-MISMATCH", guard_line, "job-number guard did not fire:\n" + output)


if __name__ == "__main__":
    unittest.main()
