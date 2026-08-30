from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "jobhub" / "pages" / "builders_clients.py"


def test_builders_clients_page_uses_lazy_sections():
    source = SOURCE.read_text(encoding="utf-8")
    assert "st.tabs(" not in source
    assert 'key="builders_clients_section"' in source
    assert 'BUILDER_SECTIONS = ["Add", "Edit", "Remove", "List"]' in source


def test_builder_delete_still_checks_linked_jobs():
    source = SOURCE.read_text(encoding="utf-8")
    assert "SELECT COUNT(*) AS c FROM jobs WHERE builder_client_id = ?" in source
    assert "if job_count > 0:" in source
    assert "DELETE FROM builders_clients WHERE id = ?" in source


def test_list_reuses_initial_builder_query_for_lookup_options():
    source = SOURCE.read_text(encoding="utf-8")
    list_source = source[source.index("def _render_builder_list():"):source.index("def render_builders_clients():")]
    assert list_source.count("FROM builders_clients") == 1
    assert 'builder_map = {str(row["Company / Client"]): int(row["ID"])' in list_source
