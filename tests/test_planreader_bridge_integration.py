import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

INTEGRATION_SCRIPT = r"""
import importlib.util
import os

os.environ["DATA_DIR"] = os.environ["INTEGRATION_DATA_DIR"]

# --- PlanReader side: load the self-contained bridge by file path ------------
spec = importlib.util.spec_from_file_location("planreader_bridge", "jobhub/planreader_bridge.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
bridge.ensure_bridge_schema()

job_id = bridge.create_linked_job("PB-BR-1", job_name="Bridge Job", site_address="1 Bridge St", status="Active")
assert bridge.link_job_by_no("PB-BR-1") == job_id
assert bridge.create_linked_job("PB-BR-1", job_name="Bridge Job") == job_id  # no duplicate

n1 = bridge.sync_takeoff_rows(job_id, [
    {"area_location": "Kitchen", "substrate": "Internal walls", "internal_external": "Internal",
     "qty_m2": 30.0, "lineal_m": 0.0, "count": 0.0, "coats": 2, "rate_ex_gst": 18.0,
     "labour_hours": 4.2, "paint_litres": 5.0, "value_ex_gst": 540.0,
     "source_note": "Auto", "confidence": "High"},
    {"area_location": "Kitchen", "substrate": "Ceiling", "internal_external": "Internal",
     "qty_m2": 12.0, "coats": 2},
])
n2 = bridge.sync_colour_schedule(job_id, [
    {"area_location": "Kitchen", "surface": "Walls", "colour": "Antique White USA", "finish": "Low Sheen", "hex": "#F0E6D2"},
    {"area_location": "Kitchen", "surface": "Ceiling", "colour": "White", "finish": "Flat / Matt"},
])
assert n1 == 2, f"expected 2 takeoff rows, got {n1}"
assert n2 == 2, f"expected 2 colour lines, got {n2}"
assert bridge.upsert_document_blob(job_id, "colour_markup_p1.png", b"PNGBLOB", mime_type="image/png", doc_type="Colour markup")

# Upsert updates rather than duplicating.
bridge.sync_takeoff_rows(job_id, [{"area_location": "Kitchen", "substrate": "Internal walls", "qty_m2": 44.0}])
takeoff_now = bridge.job_takeoff_frame(job_id)
assert len(takeoff_now) == 2
wall_row = takeoff_now[takeoff_now["Substrate"] == "Internal walls"].iloc[0]
assert float(wall_row["Qty (m\u00b2)"]) == 44.0

# --- JobHub side: the app shares the same database ---------------------------
import pb_jobhub_app as app

assert app.PLANREADER_BRIDGE is not None, "JobHub could not load the PlanReader bridge"
tf = app.planreader_synced_takeoff(job_id)
cs = app.planreader_synced_colours(job_id)
blobs = app.planreader_synced_blobs(job_id)
assert len(tf) == 2, f"JobHub sees {len(tf)} takeoff rows"
assert len(cs) == 2, f"JobHub sees {len(cs)} colour lines"
assert len(blobs) == 1, f"JobHub sees {len(blobs)} documents"
wall_row_jobhub = tf[tf["Substrate"] == "Internal walls"].iloc[0]
assert float(wall_row_jobhub["Qty (m\u00b2)"]) == 44.0
walls_colour = cs[cs["Surface"] == "Walls"].iloc[0]
assert str(walls_colour["Colour"]) == "Antique White USA"
assert str(walls_colour["Finish"]) == "Low Sheen"
assert app.PLANREADER_BRIDGE.decode_document_blob(blobs.iloc[0].to_dict()) == b"PNGBLOB"
sf = app.PLANREADER_BRIDGE.jobs_with_sync_frame()
assert len(sf) == 1, f"expected 1 synced job, got {len(sf)}"

# --- PlanReader app module still loads and can reach the bridge --------------
import pb_planreader_app as pr
assert pr.PLANREADER_BRIDGE_AVAILABLE, "PlanReader could not load the bridge"
print("OK")
"""


class PlanReaderBridgeIntegrationTest(unittest.TestCase):
    def test_planreader_writes_visible_in_jobhub(self):
        temp_dir = tempfile.mkdtemp(prefix="jobhub_bridge_it_")
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
            timeout=600,
        )
        if completed.returncode != 0:
            self.fail("subprocess failed:\n" + (completed.stderr or "") + "\n" + (completed.stdout or ""))
        self.assertIn("OK", completed.stdout)


if __name__ == "__main__":
    unittest.main()
