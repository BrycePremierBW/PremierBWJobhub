import os
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from jobhub_time import DEFAULT_JOBHUB_TIMEZONE, jobhub_today


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


if __name__ == "__main__":
    unittest.main()
