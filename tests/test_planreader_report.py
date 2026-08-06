import tempfile
import unittest
from pathlib import Path

import pb_planreader_app as pr
from pb_planreader_app import (
    build_takeoff_summary,
    labour_hours_for,
    recalculate_takeoff_values,
    takeoff_report_pdf_bytes,
    validate_measurements,
)


class RecalculateValuesTests(unittest.TestCase):
    def test_litres_use_waste_and_coats(self):
        rows = [{"qty_m2": 100.0, "coats": 2, "labour_category": "Walls", "substrate": "Internal walls"}]
        out = recalculate_takeoff_values(rows, coverage_m2_per_l=12.0, waste_pct=5.0)
        self.assertAlmostEqual(out[0]["paint_litres"], 17.5, places=2)
        self.assertAlmostEqual(out[0]["labour_hours"], 12.6, places=2)

    def test_labour_rate_by_category(self):
        self.assertAlmostEqual(labour_hours_for(100.0, "Walls", 0.0), 12.0, places=2)
        self.assertAlmostEqual(labour_hours_for(100.0, "Exterior", 0.0), 14.0, places=2)
        self.assertAlmostEqual(labour_hours_for(100.0, "Woodwork", 0.0), 16.0, places=2)

    def test_quantity_left_untouched(self):
        rows = [{"qty_m2": 37.4, "coats": 1, "labour_category": "Ceilings", "substrate": "Ceilings"}]
        out = recalculate_takeoff_values(rows, coverage_m2_per_l=12.0, waste_pct=0.0)
        self.assertAlmostEqual(out[0]["qty_m2"], 37.4, places=2)

    def test_empty_rows_ok(self):
        self.assertEqual(recalculate_takeoff_values([]), [])


class TakeoffSummaryTests(unittest.TestCase):
    def test_aggregates_by_substrate(self):
        rows = [
            {"internal_external": "Internal", "substrate": "Internal walls", "qty_m2": 50.0, "lineal_m": 0.0, "count": 0.0, "coats": 2.0, "labour_hours": 6.0, "paint_litres": 8.0},
            {"internal_external": "Internal", "substrate": "Internal walls", "qty_m2": 25.0, "lineal_m": 0.0, "count": 0.0, "coats": 2.0, "labour_hours": 3.0, "paint_litres": 4.0},
            {"internal_external": "Internal", "substrate": "Ceilings", "qty_m2": 30.0, "lineal_m": 0.0, "count": 0.0, "coats": 1.0, "labour_hours": 3.0, "paint_litres": 2.5},
        ]
        summary = build_takeoff_summary(rows)
        self.assertEqual(len(summary), 2)
        walls = [s for s in summary if s["substrate"] == "Internal walls"][0]
        self.assertAlmostEqual(walls["qty_m2"], 75.0, places=2)
        self.assertAlmostEqual(walls["paint_litres"], 12.0, places=2)
        self.assertEqual(walls["coats"], 2.0)


class ValidateMeasurementsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="planreader_validate_"))
        self._old_data = pr.DATA_DIR
        self._old_jobs = pr.JOBS_DIR
        pr.DATA_DIR = self._tmp
        pr.JOBS_DIR = self._tmp / "jobs"

    def tearDown(self):
        pr.DATA_DIR = self._old_data
        pr.JOBS_DIR = self._old_jobs

    def test_empty_job_no_warnings(self):
        self.assertEqual(validate_measurements({"job_id": "t0", "rooms": [], "analyses": [], "elevation_progress": {}}), [])

    def test_area_estimate_warns(self):
        job = {
            "job_id": "t1",
            "rooms": [{"room": "Lounge", "dim1_m": 3, "dim2_m": 4}],
            "analyses": [],
            "elevation_progress": {},
        }
        warnings = validate_measurements(job)
        self.assertEqual(len(warnings), 1)
        self.assertIn("area-based estimate", warnings[0])


class ReportPdfTests(unittest.TestCase):
    def test_report_pdf_bytes(self):
        job = {"job_name": "Test House", "job_no": "J-1", "site_address": "1 Test St"}
        rows = [
            {"area_location": "Lounge", "substrate": "Internal walls", "internal_external": "Internal", "qty_m2": 50.0, "lineal_m": 0.0, "coats": 2.0, "labour_hours": 6.0, "paint_litres": 8.0, "rate_ex_gst": 30.0},
        ]
        pdf = takeoff_report_pdf_bytes(job, build_takeoff_summary(rows), rows)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 1000)


if __name__ == "__main__":
    unittest.main()
