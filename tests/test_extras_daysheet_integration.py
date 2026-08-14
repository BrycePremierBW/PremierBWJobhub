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
    "INSERT INTO employees (name, role, status) VALUES (?, ?, ?)",
    ("Extras Crew", "Painter", "Active"),
)
employee_id = int(cur.lastrowid)
cur.execute(
    "INSERT INTO jobs (job_no, job_name, site_address, status) VALUES (?, ?, ?, ?)",
    ("PB-EXT-1", "Extras Job", "15 Site St", "Active"),
)
job_id = int(cur.lastrowid)
conn.commit()
conn.close()

daysheet_id, daysheet_no = app.create_extras_daysheet(
    job_id,
    created_by="Extras Crew",
    employee_name="Extras Crew",
    area="External",
    notes="Crew handover extras",
)
print("SHEET_NO:" + daysheet_no)
assert daysheet_no == "EXT-001", daysheet_no
assert isinstance(daysheet_id, int) and isinstance(daysheet_no, str), (type(daysheet_id), type(daysheet_no))

# Same crew member reuses their editable draft sheet.
reused = app.find_editable_extras_daysheet(job_id, created_by="Extras Crew")
assert reused == daysheet_id, (reused, daysheet_id)

app.save_extras_daysheet_item(daysheet_id, "Extra prep to columns", qty=2, unit="hrs", unit_price_ex_gst=45)
app.save_extras_daysheet_item(daysheet_id, "Extra scaffolding hire", qty=1, unit="day", unit_price_ex_gst=180)

sheet = app.get_extras_daysheet(daysheet_id)
assert sheet is not None
assert sheet["job_no"] == "PB-EXT-1"
expected_total = 2 * 45 + 1 * 180
assert abs(float(sheet["total_ex_gst"]) - expected_total) < 0.001, sheet["total_ex_gst"]
print("TOTAL_EX_GST:" + str(sheet["total_ex_gst"]))

# Approved variation prefill, then guard against duplicates.
conn = app.connect()
cur = conn.cursor()
cur.execute(
    "INSERT INTO job_variations (job_id, variation_no, description, amount_ex_gst, status, created_at) VALUES (?, ?, ?, ?, 'Approved', ?)",
    (job_id, "VAR-001", "Extra masonry", 500, app.jobhub_now().strftime("%Y-%m-%d %H:%M:%S")),
)
conn.commit()
conn.close()

added_first = app.add_approved_variations_to_daysheet(daysheet_id, job_id)
added_second = app.add_approved_variations_to_daysheet(daysheet_id, job_id)
print("ADDED_FIRST:" + str(added_first))
print("ADDED_SECOND:" + str(added_second))
assert added_first == 1, added_first
assert added_second == 0, added_second

items = app.get_extras_daysheet_items(daysheet_id)
assert len(items) == 3, len(items)
var_rows = items[items["variation_no"].str.strip() == "VAR-001"]
assert len(var_rows) == 1, len(var_rows)
assert abs(float(var_rows.iloc[0]["amount_ex_gst"]) - 500) < 0.001

app.update_extras_daysheet_meta(daysheet_id, status="Submitted", sheet_date="2026-01-05")
sheet = app.get_extras_daysheet(daysheet_id)
assert sheet["status"] == "Submitted"
assert sheet["sheet_date"] == "2026-01-05"

app.delete_extras_daysheet_item(daysheet_id, int(items.iloc[0]["id"]))
items_after = app.get_extras_daysheet_items(daysheet_id)
assert len(items_after) == 2, len(items_after)
sheet = app.get_extras_daysheet(daysheet_id)
assert abs(float(sheet["total_ex_gst"]) - (180 + 500)) < 0.001

pdf_path = app.generate_extras_daysheet_pdf(daysheet_id)
assert os.path.exists(pdf_path), pdf_path
assert pdf_path.lower().endswith(".pdf")
print("PDF_PATH:" + pdf_path)

docs = app.df_query(
    "SELECT document_type, file_name, notes FROM job_documents WHERE job_id = ?",
    (job_id,),
)
assert len(docs) == 1, len(docs)
print("DOC_TYPE:" + docs.iloc[0]["document_type"])
assert docs.iloc[0]["document_type"] == "Extras Day Sheet"

# Staff (un-priced) PDF must not expose pricing.
from pypdf import PdfReader
staff_pdf_path = app.generate_extras_daysheet_pdf(daysheet_id, include_pricing=False)
assert os.path.exists(staff_pdf_path)
staff_text = ""
with open(staff_pdf_path, "rb") as fh:
    reader = PdfReader(fh)
    for page in reader.pages:
        staff_text += (page.extract_text() or "")
assert "$" not in staff_text, staff_text[:500]
assert "GST" not in staff_text, staff_text[:500]
print("STAFF_PDF_CLEAN")

# Priced PDF shows totals and is attached admin-only.
priced_pdf_path = app.generate_extras_daysheet_pdf(daysheet_id, include_pricing=True)
priced_text = ""
with open(priced_pdf_path, "rb") as fh:
    reader = PdfReader(fh)
    for page in reader.pages:
        priced_text += (page.extract_text() or "")
assert "GST" in priced_text, priced_text[:500]
print("PRICED_PDF_OK")

docs = app.df_query(
    "SELECT document_type, viewer_scope FROM job_documents WHERE job_id = ?",
    (job_id,),
)
assert {"admin", "crew"}.issubset(set(docs["viewer_scope"])), docs
print("SCOPE_OK")

# Job delete cleanup removes daysheet rows too.
conn = app.connect()
cur = conn.cursor()
app._delete_job_rows(cur, job_id)
conn.commit()
cur.execute("SELECT COUNT(*) FROM job_extra_daysheets WHERE job_id = ?", (job_id,))
assert cur.fetchone()[0] == 0
conn.close()
print("CLEANUP_OK")
"""


class ExtrasDaysheetIntegrationTest(unittest.TestCase):
    def test_extras_daysheet_workflow(self):
        temp_dir = tempfile.mkdtemp(prefix="jobhub_extras_it_")
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
        self.assertIn("SHEET_NO:EXT-001", output)
        self.assertIn("TOTAL_EX_GST:270.0", output)
        self.assertIn("ADDED_FIRST:1", output)
        self.assertIn("ADDED_SECOND:0", output)
        self.assertIn("DOC_TYPE:Extras Day Sheet", output)
        self.assertIn("STAFF_PDF_CLEAN", output)
        self.assertIn("PRICED_PDF_OK", output)
        self.assertIn("SCOPE_OK", output)
        self.assertIn("CLEANUP_OK", output)
        self.assertIn("PDF_PATH:", output)
        pdf_path = next(
            (ln[len("PDF_PATH:"):] for ln in output.splitlines() if ln.startswith("PDF_PATH:")),
            "",
        )
        self.assertTrue(pdf_path and os.path.exists(pdf_path), "pdf missing")
        self.addCleanup(lambda: os.path.exists(pdf_path) and os.remove(pdf_path))


if __name__ == "__main__":
    unittest.main()
