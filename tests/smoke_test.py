"""Run a compile and page-render smoke test before deploying JobHub."""
from __future__ import annotations

import os
import py_compile
import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="jobhub_smoke_"))

for source in ROOT.rglob("*.py"):
    if "__pycache__" not in source.parts:
        py_compile.compile(str(source), doraise=True)

from streamlit.testing.v1 import AppTest

routes = [
    "Dashboard", "Job Folders", "Jobs", "Control Centre", "Timesheets",
    "Material Costs", "Wages", "Equipment", "Job Photos", "PDF Import Centre",
    "Estimate Working Sheet", "Job Costs / Forecasting",
    "Import Take-off / Model File", "Progress / Billing Model", "3D Model Viewer",
    "Reports / Export", "Builders & Clients", "Employees", "Products",
    "User Access", "JobHub AI Assistant", "App Builder AI",
]

at = AppTest.from_file(str(ROOT / "pb_jobhub_app.py"), default_timeout=60)
at.run(timeout=60)
assert not at.exception, [e.value for e in at.exception]
at.text_input[0].set_value("admin")
at.text_input[1].set_value("admin123")
at.button[0].click()
at.run(timeout=60)
assert not at.exception, [e.value for e in at.exception]

for route in routes:
    at.session_state["go_to_menu"] = route
    at.run(timeout=60)
    assert not at.exception, f"{route}: {[e.value for e in at.exception]}"

print(f"PASS: compiled all Python files and rendered {len(routes)} JobHub routes.")
