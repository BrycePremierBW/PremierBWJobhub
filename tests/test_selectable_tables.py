from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SelectableTableSourceTests(unittest.TestCase):
    def test_material_rows_open_edit_and_delete_controls(self):
        ui = (ROOT / "jobhub_lean" / "ui.py").read_text(encoding="utf-8")
        source = (ROOT / "jobhub_lean" / "resources.py").read_text(encoding="utf-8")
        self.assertIn('selection_mode="single-row"', ui)
        self.assertIn('key=f"materials_table_{job_id}"', source)
        self.assertIn("UPDATE material_entries", source)
        self.assertIn("DELETE FROM material_entries WHERE id=?", source)

    def test_schedule_day_tiles_are_primary_and_selectable(self):
        source = (ROOT / "pb_jobhub_visual_scheduler.py").read_text(encoding="utf-8")
        self.assertIn('st.tabs(["Clickable tile board"', source)
        self.assertIn('selection_mode="single-cell"', source)
        self.assertIn('key="clickable_schedule_board"', source)
        self.assertIn('@st.dialog("Choose a job", dismissible=False)', source)
        self.assertIn('"Add to job"', source)
        self.assertIn("time(7, 0)", source)
        self.assertIn("time(15, 0)", source)
        board_position = source.index('st.markdown("### Staff × Day tile board")')
        timeline_position = source.index('with st.expander("View coloured schedule timeline")')
        self.assertLess(board_position, timeline_position)

    def test_standard_dataframes_use_explicit_row_selection(self):
        source = (ROOT / "jobhub_lean" / "ui.py").read_text(encoding="utf-8")
        self.assertIn("def selected_row", source)
        self.assertIn('on_select="rerun"', source)
        self.assertIn('selection_mode="single-row"', source)
        self.assertNotIn("st.dataframe =", source)

    def test_estimate_working_sheet_dataframes_have_unique_keys(self):
        source = (ROOT / "jobhub_lean" / "estimating.py").read_text(encoding="utf-8")
        expected_keys = (
            'key=f"estimates_table_{job_id}"',
            'key=f"estimate_lines_{estimate_id}"',
            'key=f"estimate_csv_{estimate_id}"',
        )
        for expected_key in expected_keys:
            self.assertEqual(source.count(expected_key), 1)

    def test_selected_job_row_can_change_status(self):
        source = (ROOT / "jobhub_lean" / "jobs.py").read_text(encoding="utf-8")
        self.assertIn('row = selected_row(', source)
        self.assertIn('status = c3.selectbox("Status"', source)
        self.assertIn("UPDATE jobs SET job_no=?,job_name=?", source)
        self.assertIn("status=?", source)

    def test_selected_job_row_can_edit_all_job_details(self):
        source = (ROOT / "jobhub_lean" / "jobs.py").read_text(encoding="utf-8")
        for label in (
            '"Job number"',
            '"Job name"',
            '"Builder / client"',
            '"Site address"',
            '"Contract value ex GST"',
            '"Start date"',
            '"Finish date"',
        ):
            self.assertIn(label, source)
        self.assertIn('st.form_submit_button("Update job"', source)
        self.assertIn("UPDATE jobs SET", source)

    def test_edit_job_dates_use_calendar_inputs(self):
        source = (ROOT / "jobhub_lean" / "jobs.py").read_text(encoding="utf-8")
        self.assertIn('date_input("Start date"', source)
        self.assertIn('date_input("Finish date"', source)
        self.assertNotIn('text_input("Start date"', source)
        self.assertNotIn('text_input("Finish date"', source)

    def test_no_live_daily_default_is_below_eight_hours(self):
        scheduler = (ROOT / "pb_jobhub_visual_scheduler.py").read_text(encoding="utf-8")
        timesheets = (ROOT / "jobhub_lean" / "timesheets.py").read_text(encoding="utf-8")
        job_packs = (ROOT / "jobhub_lean" / "job_packs.py").read_text(encoding="utf-8")
        self.assertNotIn("value=7.6", scheduler)
        self.assertNotIn("DEFAULT 7.6", scheduler)
        self.assertIn("time(7, 0)", timesheets)
        self.assertIn("time(15, 0)", timesheets)
        self.assertIn('"painter_day_hours": 8', job_packs)


if __name__ == "__main__":
    unittest.main()
