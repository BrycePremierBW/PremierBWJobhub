import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

INTEGRATION_SCRIPT = r"""
import os
os.environ["DATA_DIR"] = os.environ["INTEGRATION_DATA_DIR"]
import pb_jobhub_app as app
app.USE_POSTGRES = False
app.init_db()
app.apply_schema_migrations()
app.init_linked_schema()

conn = app.connect()
cur = conn.cursor()
cur.execute(
    "INSERT INTO jobs (job_no, job_name, site_address, status) VALUES (?, ?, ?, ?)",
    ("PB-NOTES-1", "Notes Job", "1 Notes St", "Active"),
)
job_id = int(cur.lastrowid)
conn.commit()
conn.close()

# Empty comments are rejected.
assert app.add_job_comment(job_id, "Bryce", "admin", "   ") is False

# Admin posts a note, then an employee replies.
assert app.add_job_comment(job_id, "Bryce", "admin", "Site access via rear gate, code 4821.") is True
assert app.add_job_comment(job_id, "Sam Payne", "employee", "Thanks, found it. Painting starts tomorrow.") is True

frame = app.job_comments_frame(job_id)
assert len(frame) == 2, f"expected 2 comments, got {len(frame)}"
# Oldest first thread order.
assert frame.iloc[0]["Author"] == "Bryce"
assert frame.iloc[0]["Role"] == "admin"
assert frame.iloc[0]["Comment"] == "Site access via rear gate, code 4821."
assert frame.iloc[1]["Author"] == "Sam Payne"
assert frame.iloc[1]["Role"] == "employee"
assert frame.iloc[1]["Comment"] == "Thanks, found it. Painting starts tomorrow."

# Comments are scoped to the job they were posted on.
other_conn = app.connect()
other_cur = other_conn.cursor()
other_cur.execute(
    "INSERT INTO jobs (job_no, job_name) VALUES (?, ?)",
    ("PB-NOTES-2", "Other Job"),
)
other_job_id = int(other_cur.lastrowid)
other_conn.commit()
other_conn.close()
assert app.job_comments_frame(other_job_id).empty

# Admin can delete a comment.
app.delete_job_comment(int(frame.iloc[0]["Comment ID"]))
remaining = app.job_comments_frame(job_id)
assert len(remaining) == 1, f"expected 1 comment after delete, got {len(remaining)}"
assert remaining.iloc[0]["Author"] == "Sam Payne"
print("OK")
"""


class JobCommentsIntegrationTest(unittest.TestCase):
    def test_job_comments_thread(self):
        temp_dir = tempfile.mkdtemp(prefix="jobhub_notes_it_")
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
        if completed.returncode != 0:
            self.fail("subprocess failed:\n" + (completed.stderr or "") + "\n" + (completed.stdout or ""))
        self.assertIn("OK", completed.stdout)


if __name__ == "__main__":
    unittest.main()
