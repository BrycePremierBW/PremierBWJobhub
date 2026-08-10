"""Timesheet UX guards: bulk staff/date entry, edit-without-delete, and clean
handling of field-clock (mobile / "Blip"-style) linked entries.

The heavy monolith is intentionally not imported here — the existing suite
guards behaviour by source so CI stays fast and never touches a live database.
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = (ROOT / "pb_jobhub_app.py").read_text(encoding="utf-8")


class TimesheetFieldClockGuards(unittest.TestCase):
    def test_edit_is_in_place_update_not_delete_reinsert(self):
        update = APP[APP.index("def update_timesheet_entry"):APP.index("def delete_timesheet_entry")]
        self.assertIn("UPDATE timesheet_entries", update)
        self.assertIn("SET job_id = ?, job_stage_id = ?, employee_id = ?, work_date = ?", update)
        self.assertIn("WHERE id = ?", update)
        self.assertNotIn("DELETE FROM timesheet_entries", update)

    def test_editing_a_field_clock_linked_timesheet_reconciles_the_clock_row(self):
        update = APP[APP.index("def update_timesheet_entry"):APP.index("def delete_timesheet_entry")]
        self.assertIn("UPDATE field_clock_entries", update)
        self.assertIn("submitted_timesheet_id = ?", update)

    def test_deleting_a_field_clock_linked_timesheet_unlinks_the_clock_row(self):
        delete = APP[APP.index("def delete_timesheet_entry"):APP.index("def normalise_timesheet_statuses")]
        self.assertIn("DELETE FROM wage_entries WHERE timesheet_id = ?", delete)
        self.assertIn("submitted_timesheet_id = NULL", delete)
        self.assertIn("DELETE FROM timesheet_entries WHERE id = ?", delete)

    def test_bulk_employee_and_date_entry_exists(self):
        form = APP[APP.index("def timesheet_entry_form"):APP.index("def timesheets_page")]
        self.assertIn("st.multiselect(", form)
        self.assertIn('["Single Date", "Multiple Dates"]', form)
        self.assertIn("default=weekday_defaults", form)
        self.assertIn("len(selected_employee_ids) * len(selected_work_dates)", form)
        self.assertIn("calculate_shift_hours(", form)

    def test_timesheet_date_defaults_are_business_local_not_utc(self):
        # UTC date.today() can be a day behind Brisbane. Timesheet "today"
        # defaults must use jobhub_today() like the scheduler does.
        form = APP[APP.index("def timesheet_entry_form"):APP.index("def timesheets_page")]
        self.assertNotIn("date.today()", form)
        self.assertIn("value=jobhub_today()", form)
        self.assertIn("default_from = jobhub_today() - timedelta(days=jobhub_today().weekday())", form)
        edit_form = APP[APP.index("def render_timesheet_edit_form"):APP.index("def timesheet_entry_form")]
        self.assertIn("current_date = jobhub_today() if pd.isna(parsed_date) else parsed_date.date()", edit_form)


if __name__ == "__main__":
    unittest.main()
