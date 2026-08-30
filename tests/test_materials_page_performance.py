from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "jobhub" / "pages" / "materials.py"


def test_materials_page_uses_lazy_sections():
    source = SOURCE.read_text(encoding="utf-8")
    assert 'key="materials_page_section"' in source
    assert "st.tabs(" not in source
    for section in ("Material Orders", "Add Material Cost", "Cost Register", "Imported PDF Lines", "Import PDFs"):
        assert section in source


def test_material_bulk_deletes_use_batched_transactions():
    source = SOURCE.read_text(encoding="utf-8")
    assert 'execute_many(\n                "DELETE FROM material_entries WHERE id = ?"' in source
    assert 'execute_many(\n                "DELETE FROM imported_material_entries WHERE id = ?"' in source
    assert 'for material_id in selected_material_ids:\n                    execute(' not in source
    assert 'for imported_id in selected_imported_ids:\n                    execute(' not in source


def test_material_delete_confirmations_are_preserved():
    source = SOURCE.read_text(encoding="utf-8")
    assert "DELETE MATERIALS" in source
    assert "DELETE IMPORTED MATERIALS" in source


def test_expensive_pdf_import_and_order_queue_are_isolated_helpers():
    source = SOURCE.read_text(encoding="utf-8")
    render_source = source[source.index("def render_material_costs():"):]
    assert "render_context_pdf_import_for_selected_job(" not in render_source
    assert "render_material_order_admin_queue()" not in render_source
    assert "_render_material_pdf_import()" in render_source
    assert "_render_material_orders()" in render_source
