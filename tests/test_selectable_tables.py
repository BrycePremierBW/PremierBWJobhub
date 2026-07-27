from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SelectableTableSourceTests(unittest.TestCase):
    def test_material_cost_rows_open_edit_and_delete_controls(self):
        source = (ROOT / "pb_jobhub_app.py").read_text(encoding="utf-8")
        self.assertIn('selection_mode="single-row"', source)
        self.assertIn('key="selectable_material_cost_entries"', source)
        self.assertIn('st.tabs(["Edit selected line", "Delete selected line"])', source)
        self.assertIn("UPDATE material_entries", source)
        self.assertIn("DELETE FROM material_entries WHERE id = ?", source)

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

    def test_all_standard_dataframes_are_row_selectable(self):
        source = (ROOT / "pb_jobhub_app.py").read_text(encoding="utf-8")
        self.assertIn("def pb_selectable_dataframe", source)
        self.assertIn('kwargs["on_select"] = "rerun"', source)
        self.assertIn('kwargs["selection_mode"] = "single-row"', source)
        self.assertIn("st.dataframe = pb_selectable_dataframe", source)

    def test_no_live_daily_default_is_below_eight_hours(self):
        scheduler = (ROOT / "pb_jobhub_visual_scheduler.py").read_text(encoding="utf-8")
        app = (ROOT / "pb_jobhub_app.py").read_text(encoding="utf-8")
        estimating = (ROOT / "jobhub" / "estimating.py").read_text(encoding="utf-8")
        self.assertNotIn("value=7.6", scheduler)
        self.assertNotIn("DEFAULT 7.6", scheduler)
        self.assertNotIn("value=7.5", app)
        self.assertNotIn("value=7.5", estimating)


if __name__ == "__main__":
    unittest.main()
