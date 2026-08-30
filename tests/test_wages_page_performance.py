from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "jobhub" / "pages" / "wages.py"


def test_wages_page_uses_lazy_sections():
    source = SOURCE.read_text(encoding="utf-8")
    assert 'WAGE_SECTIONS = ["Add Wage", "Wage Register", "Import PDFs"]' in source
    assert 'key="wages_page_section"' in source
    assert "st.tabs(" not in source


def test_bulk_wage_delete_uses_one_batched_transaction():
    source = SOURCE.read_text(encoding="utf-8")
    assert 'execute_many(\n                "DELETE FROM wage_entries WHERE id = ?"' in source
    assert 'for wage_id in selected_wage_ids:\n                    execute(' not in source


def test_wage_delete_confirmation_is_preserved():
    source = SOURCE.read_text(encoding="utf-8")
    assert "DELETE WAGES" in source
    assert "Type DELETE WAGES exactly before deleting wage entries." in source


def test_pdf_import_is_only_in_import_section_helper():
    source = SOURCE.read_text(encoding="utf-8")
    render_source = source[source.index("def render_wages():"):]
    assert "render_context_pdf_import_for_selected_job(" not in render_source
    assert "_render_wage_imports()" in render_source
