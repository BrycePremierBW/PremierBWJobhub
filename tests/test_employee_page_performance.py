from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "jobhub" / "pages" / "employees.py"


def test_employee_page_uses_lazy_section_selector_instead_of_eager_tabs():
    source = SOURCE.read_text(encoding="utf-8")
    assert "st.tabs(" not in source
    assert 'key="employee_page_section"' in source
    assert 'EMPLOYEE_SECTIONS = ["Add", "Edit", "Remove / Deactivate", "List"]' in source


def test_employee_queries_live_inside_selected_section_helpers():
    source = SOURCE.read_text(encoding="utf-8")
    render_start = source.index("def render_employees():")
    render_source = source[render_start:]
    assert "df_query(" not in render_source
    assert "_render_add_employee()" in render_source
    assert "_render_edit_employee()" in render_source
    assert "_render_remove_employee()" in render_source
    assert "_render_employee_list()" in render_source


def test_employee_destructive_confirmation_is_preserved():
    source = SOURCE.read_text(encoding="utf-8")
    assert "DELETE EMPLOYEES" in source
    assert "delete_or_deactivate_selected_employees" in source
    assert "delete_employee_and_linked_users" in source
