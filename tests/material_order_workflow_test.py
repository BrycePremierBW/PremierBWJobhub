"""End-to-end data and PDF test for the material-order approval workflow."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="jobhub_material_order_test_")

from jobhub import database, documents, material_orders
from jobhub.registry import bind_modules

bind_modules([database, documents, material_orders])

database.init_db()
database.execute("INSERT INTO builders_clients (type, name) VALUES (?, ?)", ("Builder", "Test Builder"))
database.execute(
    """
    INSERT INTO jobs (job_no, job_name, builder_client_id, site_address, status)
    VALUES (?, ?, ?, ?, ?)
    """,
    ("PB99999", "Material Order Test", 1, "1 Test Street", "Active"),
)
database.execute("INSERT INTO employees (name, status) VALUES (?, ?)", ("Test Employee", "Active"))
database.execute(
    """
    INSERT INTO products (product_code, product_name, supplier, unit, price_ex_gst)
    VALUES (?, ?, ?, ?, ?)
    """,
    ("P-001", "Test Paint", "Supplier A", "15L", 125.0),
)

order_id = material_orders.create_material_order_request(
    job_id=1,
    requested_by_user_id=101,
    requested_by_employee_id=1,
    requested_by_name="Test Employee",
    required_delivery_date="2026-07-20",
    supplier="Supplier A",
    employee_notes="Deliver to site office.",
)
material_orders.add_material_order_item(
    order_id=order_id,
    product_id=1,
    product_code="P-001",
    product_name="Test Paint",
    supplier="Supplier A",
    unit="15L",
    unit_price=125.0,
    colour="White",
    qty_required=2,
    qty_received=0,
    notes="Urgent",
)
material_orders.submit_material_order(order_id)
pdf_path = material_orders.approve_material_order(order_id, "Admin User", "Approved for ordering.")

order = material_orders.get_material_order(order_id)
material_entries = database.df_query(
    "SELECT * FROM material_entries WHERE material_order_request_id = ?",
    (order_id,),
)
documents_df = database.df_query(
    """
    SELECT * FROM job_documents
    WHERE job_id = 1 AND document_type = 'Approved Paint & Materials Order'
    """
)

assert order["status"] == "Approved"
assert len(material_entries) == 1
assert len(documents_df) == 1
assert Path(pdf_path).exists()

try:
    material_orders.approve_material_order(order_id, "Admin User", "Duplicate approval")
except ValueError:
    pass
else:
    raise AssertionError("A previously approved order must not be approved a second time.")

assert len(database.df_query(
    "SELECT * FROM material_entries WHERE material_order_request_id = ?",
    (order_id,),
)) == 1

print("PASS: material order submitted, approved, converted to one cost entry, attached and rendered to PDF.")
