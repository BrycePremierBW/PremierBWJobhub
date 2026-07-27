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
        board_position = source.index('st.markdown("### Staff × Day tile board")')
        timeline_position = source.index('with st.expander("View coloured schedule timeline")')
        self.assertLess(board_position, timeline_position)


if __name__ == "__main__":
    unittest.main()
