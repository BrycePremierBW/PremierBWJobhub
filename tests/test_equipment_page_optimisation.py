from pathlib import Path


EQUIPMENT_PAGE = Path("jobhub/pages/equipment.py").read_text(encoding="utf-8")


def test_equipment_page_uses_lazy_section_selector_not_tabs():
    assert "st.tabs(" not in EQUIPMENT_PAGE
    assert "EQUIPMENT_SECTIONS" in EQUIPMENT_PAGE
    assert 'key="equipment_section"' in EQUIPMENT_PAGE


def test_supporting_pdf_import_only_renders_in_selected_section():
    assert 'elif section == "Import PDFs":' in EQUIPMENT_PAGE
    assert EQUIPMENT_PAGE.count("render_context_pdf_import_for_selected_job(") == 1


def test_checklist_save_does_not_query_each_item_again():
    persist = EQUIPMENT_PAGE.split("def _persist_equipment_checklist", 1)[1].split("def _render_job_checklist", 1)[0]
    assert "df_query(" not in persist
    assert "existing_by_item" in persist


def test_checklist_mutations_are_batched():
    persist = EQUIPMENT_PAGE.split("def _persist_equipment_checklist", 1)[1].split("def _render_job_checklist", 1)[0]
    assert persist.count("execute_many(") == 3
    assert "insert_rows" in persist
    assert "update_rows" in persist
    assert "delete_rows" in persist


def test_duplicate_existing_records_are_preserved_for_cleanup():
    assert "def _existing_records_by_item" in EQUIPMENT_PAGE
    assert "grouped.setdefault" in EQUIPMENT_PAGE
    assert "existing_rows[1:]" in EQUIPMENT_PAGE
