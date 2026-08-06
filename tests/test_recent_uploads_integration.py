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
    ("PB-REC-1", "Recent Upload Job", "1 Recent St", "Active"),
)
job_id = int(cur.lastrowid)
cur.execute(
    "INSERT INTO job_documents (job_id, document_type, file_name, file_path, created_at, notes) VALUES (?, ?, ?, ?, ?, ?)",
    (job_id, "Architectural Plans", "floorplan.pdf", "C:/tmp/floorplan.pdf", "2026-08-06 10:00:00", "Pre-start plans"),
)
cur.execute(
    "INSERT INTO job_documents (job_id, document_type, file_name, file_path, created_at, notes) VALUES (?, ?, ?, ?, ?, ?)",
    (job_id, "Scope of Works", "scopex.docx", "C:/tmp/scopex.docx", "2026-08-05 09:00:00", ""),
)
cur.execute(
    "INSERT INTO job_documents (job_id, document_type, file_name, file_path, created_at, notes, mime_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
    (job_id, "Colour Schedule", "colour_markup_p1.png", "C:/tmp/colour_markup_p1.png", "2026-08-06 11:30:00", "Colour brief", "image/png"),
)
conn.commit()
conn.close()

frame = app.recent_uploads_frame(limit=8)
assert not frame.empty, "recent_uploads_frame returned no rows"
assert len(frame) >= 3, f"expected at least 3 rows, got {len(frame)}"
row = frame.iloc[0]
assert int(row["job_id"]) == job_id
assert row["job_no"] == "PB-REC-1"
assert row["job_name"] == "Recent Upload Job"
assert row["doc_type"] == "Colour Schedule"
assert row["file_name"] == "colour_markup_p1.png"
assert row["uploaded_at"] == "2026-08-06 11:30:00"
# Newest first.
assert frame.iloc[0]["uploaded_at"] >= frame.iloc[1]["uploaded_at"]
# Limit respected.
limited = app.recent_uploads_frame(limit=1)
assert len(limited) == 1, f"limit=1 returned {len(limited)} rows"
# Colour schedule frame only returns colour docs.
colour_docs = app.colour_schedule_documents_frame(job_id)
assert len(colour_docs) == 1, f"expected 1 colour doc, got {len(colour_docs)}"
assert str(colour_docs.iloc[0]["file_name"]) == "colour_markup_p1.png"
assert str(colour_docs.iloc[0]["Mime Type"]) == "image/png"
print("OK")
"""


class RecentUploadsIntegrationTest(unittest.TestCase):
    def test_recent_uploads_query(self):
        temp_dir = tempfile.mkdtemp(prefix="jobhub_recent_it_")
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
