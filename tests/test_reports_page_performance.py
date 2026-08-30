from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "jobhub" / "pages" / "reports.py"


def test_reports_page_uses_lazy_top_level_sections():
    source = SOURCE.read_text(encoding="utf-8")
    assert 'REPORT_SECTIONS = ["Job Pack by Job", "General Reports"]' in source
    assert 'key="reports_page_section"' in source
    assert "st.tabs(" not in source


def test_job_pack_queries_timesheets_only_once():
    source = SOURCE.read_text(encoding="utf-8")
    load_source = source[source.index("def _load_job_pack_data"):source.index("def _job_pack_totals")]
    assert load_source.count('data["timesheets"] = df_query(') == 1
    assert load_source.count("FROM timesheet_entries t") == 1


def test_photo_binary_data_is_not_loaded_with_normal_job_pack_data():
    source = SOURCE.read_text(encoding="utf-8")
    load_source = source[source.index("def _load_job_pack_data"):source.index("def _job_pack_totals")]
    gallery_source = source[source.index("def _render_photo_gallery"):source.index("def _build_job_pack_excel")]
    assert "photo_data" not in load_source
    assert "photo_data" in gallery_source
    assert 'key=f"job_pack_load_photo_gallery_{job_id}"' in gallery_source


def test_excel_workbook_is_generated_only_after_explicit_opt_in():
    source = SOURCE.read_text(encoding="utf-8")
    download_source = source[source.index("def _render_job_pack_downloads"):source.index("def _render_job_pack")]
    assert 'key=f"prepare_job_pack_excel_{clean_job_no}"' in download_source
    assert "if prepare_excel:" in download_source
    assert "_build_job_pack_excel(" in download_source


def test_general_reports_query_only_selected_report():
    source = SOURCE.read_text(encoding="utf-8")
    general_source = source[source.index("def _render_general_reports"):source.index("def render_reports")]
    assert "report_df = df_query(GENERAL_REPORTS[report_name])" in general_source
