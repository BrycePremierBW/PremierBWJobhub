"""Smoke tests for split PO upload workflow."""

from pathlib import Path
import ast


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_split_po_guard_source_parses():
    ast.parse(read("jobhub/po_upload_split_guard.py"), filename="jobhub/po_upload_split_guard.py")


def test_split_po_guard_is_installed_at_startup():
    init_source = read("jobhub/__init__.py")
    assert "from .po_upload_split_guard import install_po_upload_split_guard" in init_source
    assert "install_po_upload_split_guard()" in init_source


def test_split_po_creates_internal_and_external_lines_from_one_file():
    source = read("jobhub/po_upload_split_guard.py")
    required = [
        "Split one PO into Internal + External",
        "Total PO value ex GST",
        "Split by amounts",
        "Split by percentages",
        "Internal PO amount ex GST",
        "External PO amount ex GST",
        "Internal contract/scope value ex GST",
        "External contract/scope value ex GST",
        "_record_document_once",
        "_record_po_line",
        "Split PO - Internal",
        "Split PO - External",
        "Internal + External must equal the total PO value",
    ]
    for marker in required:
        assert marker in source


def test_split_po_uses_same_po_file_for_both_lines():
    source = read("jobhub/po_upload_split_guard.py")
    assert "file_name, file_path = po._save_uploaded_file" in source
    assert source.count("_record_po_line(") >= 3
    assert "scope_label=\"Internal\"" in source
    assert "scope_label=\"External\"" in source


def test_split_po_allows_same_po_number_for_internal_and_external_scope_lines():
    source = read("jobhub/po_upload_split_guard.py")
    required = [
        "PO_NUMBER_UNIQUE_CONSTRAINT",
        "job_purchase_orders_job_id_po_number_key",
        "_relax_po_number_uniqueness",
        "ALTER TABLE job_purchase_orders DROP CONSTRAINT IF EXISTS",
        "DROP INDEX IF EXISTS",
        "one Internal row and one External row",
    ]
    for marker in required:
        assert marker in source
    assert "-INT" not in source
    assert "-EXT" not in source
