import os
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from jobhub_time import DEFAULT_JOBHUB_TIMEZONE, jobhub_now, jobhub_today


class JobHubBusinessDateTests(unittest.TestCase):
    def test_render_utc_date_rolls_forward_to_queensland_business_date(self):
        render_time = datetime(2026, 8, 2, 15, 30, tzinfo=timezone.utc)

        with patch.dict(os.environ, {"JOBHUB_TIMEZONE": "Australia/Brisbane"}):
            self.assertEqual(jobhub_today(render_time), date(2026, 8, 3))

    def test_invalid_timezone_falls_back_to_brisbane(self):
        render_time = datetime(2026, 8, 2, 15, 30, tzinfo=timezone.utc)

        with patch.dict(os.environ, {"JOBHUB_TIMEZONE": "Not/A-Timezone"}):
            self.assertEqual(jobhub_today(render_time), date(2026, 8, 3))
            self.assertEqual(DEFAULT_JOBHUB_TIMEZONE, "Australia/Brisbane")

    def test_now_is_business_local_naive_wall_time(self):
        render_time = datetime(2026, 8, 2, 15, 30, tzinfo=timezone.utc)

        with patch.dict(os.environ, {"JOBHUB_TIMEZONE": "Australia/Brisbane"}):
            local = jobhub_now(render_time)

        self.assertEqual(local, datetime(2026, 8, 3, 1, 30))
        self.assertIsNone(local.tzinfo)
        self.assertEqual(local.strftime("%Y-%m-%d %H:%M:%S"), "2026-08-03 01:30:00")
        self.assertEqual(local.isoformat(timespec="seconds"), "2026-08-03T01:30:00")


if __name__ == "__main__":
    unittest.main()
