from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "jobhub" / "pages" / "jobs.py"


def test_jobs_page_uses_lazy_section_selector_instead_of_eager_tabs():
    source = SOURCE.read_text(encoding="utf-8")
    assert "st.tabs(" not in source
    assert 'key="job_register_section"' in source
    assert '"Add Job", "Edit Job", "Remove / Archive", "Archived Jobs"' in source


def test_jobs_page_keeps_destructive_archive_delete_guards():
    source = SOURCE.read_text(encoding="utf-8")
    assert "linked_job_counts(selected_id)" in source
    assert "permanently_delete_job_and_linked_data(selected_id)" in source
    assert "confirm_delete_archived_job" in source
    assert "permanently_delete_job_and_linked_data(selected_archived_id)" in source


def test_jobs_page_avoids_builder_lookup_for_sections_that_do_not_need_it():
    source = SOURCE.read_text(encoding="utf-8")
    render_source = source[source.index("def render_jobs():"):]
    remove_index = render_source.index('if section == "Remove / Archive":')
    lookup_index = render_source.index("builder_options = get_builder_options()")
    assert remove_index < lookup_index
    assert 'elif section == "Job Register":' in render_source[:lookup_index]


def test_job_date_order_validation_is_present_for_add_edit_and_archived_edit():
    source = SOURCE.read_text(encoding="utf-8")
    assert source.count("End Date cannot be before Start Date.") >= 3
