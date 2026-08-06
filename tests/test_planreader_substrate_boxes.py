import tempfile
import unittest
from pathlib import Path

import pb_planreader_app as pr
from pb_planreader_app import (
    ELEVATION_BOX_SOURCE,
    calibration_mpp,
    effective_box_m2,
    measured_box_m2,
    merge_elevation_box_rows,
    normalise_boxes,
    normalise_calibration,
    save_substrate_boxes,
    substrate_boxes_from_job,
)


class SubstrateBoxNormaliseTests(unittest.TestCase):
    def test_clamps_and_defaults(self):
        boxes = normalise_boxes(
            [{"x": -5, "y": 120, "w": 30, "h": 40, "label": "Wall", "progress": 200}]
        )
        self.assertEqual(len(boxes), 1)
        b = boxes[0]
        self.assertEqual(b["x"], 0.0)
        self.assertEqual(b["y"], 100.0)
        self.assertEqual(b["progress"], 100.0)
        self.assertEqual(b["qty_m2"], 0.0)
        self.assertIn(b["substrate"], pr.SUBSTRATE_OPTIONS)

    def test_drops_empty_boxes(self):
        self.assertEqual(normalise_boxes([{"w": 0, "h": 0}]), [])
        self.assertEqual(normalise_boxes([{"x": 0, "y": 0, "w": 5, "h": 0}]), [])

    def test_rounds_to_four_decimals(self):
        b = normalise_boxes([{"x": 10.123456, "y": 20, "w": 30, "h": 40}])[0]
        self.assertEqual(b["x"], 10.1235)

    def test_ensures_boxes_fit_image(self):
        b = normalise_boxes([{"x": 90, "y": 90, "w": 50, "h": 50}])[0]
        self.assertAlmostEqual(b["w"], 10.0)
        self.assertAlmostEqual(b["h"], 10.0)

    def test_negative_qty_is_floor_at_zero(self):
        b = normalise_boxes([{"x": 0, "y": 0, "w": 10, "h": 10, "qty_m2": -5}])[0]
        self.assertEqual(b["qty_m2"], 0.0)


class SubstrateBoxPersistTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="planreader_boxes_"))
        self._old_data = pr.DATA_DIR
        self._old_jobs = pr.JOBS_DIR
        pr.DATA_DIR = self._tmp
        pr.JOBS_DIR = self._tmp / "jobs"

    def tearDown(self):
        pr.DATA_DIR = self._old_data
        pr.JOBS_DIR = self._old_jobs

    def test_save_load_round_trip(self):
        job = {}
        boxes = [
            {
                "label": "Front render",
                "substrate": "External walls / render",
                "x": 5, "y": 5, "w": 40, "h": 60,
                "progress": 25, "qty_m2": 34.5,
            }
        ]
        save_substrate_boxes(job, "elev.png", boxes)
        loaded = substrate_boxes_from_job(job, "elev.png")
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["substrate"], "External walls / render")
        self.assertEqual(loaded[0]["qty_m2"], 34.5)
        self.assertEqual(loaded[0]["progress"], 25.0)

    def test_legacy_zones_get_default_substrate(self):
        job = {
            "elevation_progress": {
                "elev.png": {
                    "zones": [
                        {"label": "Cladding area", "x": 0, "y": 0, "w": 50, "h": 50, "progress": 0}
                    ]
                }
            }
        }
        loaded = substrate_boxes_from_job(job, "elev.png")
        self.assertEqual(loaded[0]["substrate"], "Cladding / external lining")


class MergeBoxTakeoffTests(unittest.TestCase):
    def test_boxes_with_qty_become_rows(self):
        job = {
            "elevation_progress": {
                "elev.png": {
                    "zones": [
                        {
                            "label": "North render",
                            "substrate": "External walls / render",
                            "x": 0, "y": 0, "w": 50, "h": 50,
                            "progress": 0, "qty_m2": 40,
                        }
                    ]
                }
            }
        }
        rows = merge_elevation_box_rows(job, [])
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["qty_m2"], 40.0)
        self.assertEqual(r["internal_external"], "External")
        self.assertEqual(r["substrate"], "External walls / render")
        self.assertGreater(r["paint_litres"], 0)

    def test_boxes_without_qty_are_skipped(self):
        job = {
            "elevation_progress": {
                "elev.png": {
                    "zones": [
                        {
                            "label": "North",
                            "substrate": "External walls / render",
                            "x": 0, "y": 0, "w": 50, "h": 50, "qty_m2": 0,
                        }
                    ]
                }
            }
        }
        self.assertEqual(merge_elevation_box_rows(job, []), [])

    def test_remerge_replaces_old_box_rows(self):
        job = {
            "elevation_progress": {
                "elev.png": {
                    "zones": [
                        {
                            "label": "North",
                            "substrate": "External walls / render",
                            "x": 0, "y": 0, "w": 50, "h": 50, "qty_m2": 40,
                        }
                    ]
                }
            }
        }
        first = merge_elevation_box_rows(job, [])
        self.assertEqual(len(first), 1)
        second = merge_elevation_box_rows(job, first)
        self.assertEqual(len(second), 1)

    def test_non_box_rows_survive_merge(self):
        job = {"elevation_progress": {}}
        base = [
            {
                "substrate": "Internal walls",
                "area_location": "Lounge",
                "source_note": "Room dimension Lounge: 3 x 4 m",
                "qty_m2": 30,
            }
        ]
        rows = merge_elevation_box_rows(job, base)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["area_location"], "Lounge")

    def test_labour_category_maps_for_soffits(self):
        job = {
            "elevation_progress": {
                "elev.png": {
                    "zones": [
                        {
                            "label": "Eave",
                            "substrate": "Soffits / eaves",
                            "x": 0, "y": 0, "w": 50, "h": 50, "qty_m2": 10,
                        }
                    ]
                }
            }
        }
        r = merge_elevation_box_rows(job, [])[0]
        self.assertEqual(r["labour_category"], "Ceilings")
        self.assertEqual(r["internal_external"], "External")

    def test_rebuild_takeoff_preserves_box_rows(self):
        job = {
            "elevation_progress": {
                "elev.png": {
                    "zones": [
                        {
                            "label": "North",
                            "substrate": "External walls / render",
                            "x": 0, "y": 0, "w": 50, "h": 50, "qty_m2": 40,
                        }
                    ]
                }
            },
            "analyses": [
                {"file": "a.pdf", "pages": [], "painting_snippets": [], "area_candidates": []}
            ],
        }
        df = pr._rebuild_takeoff(job, [])
        box_rows = df[df["source_note"] == ELEVATION_BOX_SOURCE]
        self.assertFalse(box_rows.empty)
        self.assertAlmostEqual(float(box_rows["qty_m2"].iloc[0]), 40.0, places=2)


class ManualM2RoundTripTests(unittest.TestCase):
    def test_normalise_keeps_manual_override(self):
        b = normalise_boxes([{"x": 0, "y": 0, "w": 10, "h": 10, "qty_m2": 9, "manual_m2": 9}])[0]
        self.assertEqual(b["manual_m2"], 9.0)

    def test_normalise_clears_negative_manual_override(self):
        b = normalise_boxes([{"x": 0, "y": 0, "w": 10, "h": 10, "manual_m2": -3}])[0]
        self.assertEqual(b["manual_m2"], 0.0)

    def test_job_round_trip_keeps_manual_override(self):
        job = {}
        save_substrate_boxes(job, "elev.png", [
            {"x": 0, "y": 0, "w": 10, "h": 10, "qty_m2": 9, "manual_m2": 9}
        ])
        loaded = substrate_boxes_from_job(job, "elev.png")
        self.assertEqual(loaded[0]["manual_m2"], 9.0)


class CalibrationMathTests(unittest.TestCase):
    def test_normalise_calibration_valid(self):
        cal = normalise_calibration({"x1": 0, "y1": 10, "x2": 25, "y2": 10, "len_m": 5})
        self.assertIsNotNone(cal)
        self.assertEqual(cal["x2"], 25.0)
        self.assertEqual(cal["len_m"], 5.0)

    def test_normalise_calibration_rejects_bad_input(self):
        self.assertIsNone(normalise_calibration(None))
        self.assertIsNone(normalise_calibration({}))
        self.assertIsNone(normalise_calibration({"x1": 0, "y1": 0, "x2": 10, "y2": 0, "len_m": 0}))
        self.assertIsNone(normalise_calibration({"x1": 0, "y1": 0, "x2": 10, "y2": 0}))
        self.assertIsNone(normalise_calibration({"x1": "a", "y1": 0, "x2": 10, "y2": 0, "len_m": 5}))

    def test_normalise_calibration_clamps_and_rounds(self):
        cal = normalise_calibration({"x1": -50, "y1": 999, "x2": 10.123456, "y2": 0, "len_m": 5.123456})
        self.assertEqual(cal["x1"], 0.0)
        self.assertEqual(cal["y1"], 100.0)
        self.assertEqual(cal["x2"], 10.1235)
        self.assertEqual(cal["len_m"], 5.1235)

    def test_mpp_horizontal(self):
        mpp = calibration_mpp({"x1": 0, "y1": 0, "x2": 10, "y2": 0, "len_m": 5}, 1000, 800)
        self.assertAlmostEqual(mpp, 0.05)

    def test_mpp_vertical(self):
        mpp = calibration_mpp({"x1": 0, "y1": 0, "x2": 0, "y2": 20, "len_m": 8}, 1000, 1000)
        self.assertAlmostEqual(mpp, 0.04)

    def test_mpp_diagonal(self):
        mpp = calibration_mpp({"x1": 0, "y1": 0, "x2": 30, "y2": 40, "len_m": 5}, 1000, 1000)
        self.assertAlmostEqual(mpp, 0.01, places=6)

    def test_mpp_degenerate_or_missing(self):
        self.assertIsNone(calibration_mpp({"x1": 0, "y1": 0, "x2": 0, "y2": 0, "len_m": 5}, 1000, 1000))
        self.assertIsNone(calibration_mpp(None, 1000, 1000))
        self.assertIsNone(calibration_mpp({"x1": 0, "y1": 0, "x2": 10, "y2": 0, "len_m": 5}, 0, 1000))

    def test_measured_box_m2(self):
        mpp = 0.05  # metres per pixel
        b = {"x": 0, "y": 0, "w": 10, "h": 20}
        area = measured_box_m2(b, mpp, 1000, 1000)
        self.assertAlmostEqual(area, 50.0)  # 100x200 px @ 0.05 m/px

    def test_measured_box_m2_no_calibration(self):
        self.assertEqual(measured_box_m2({"w": 10, "h": 10}, None, 1000, 1000), 0.0)

    def test_effective_prefers_manual_override(self):
        box = {"w": 10, "h": 10, "qty_m2": 100, "manual_m2": 7.5}
        self.assertAlmostEqual(effective_box_m2(box, 0.05, 1000, 1000), 7.5)

    def test_effective_uses_measured_when_calibrated(self):
        box = {"w": 10, "h": 20, "qty_m2": 3, "manual_m2": 0}
        self.assertAlmostEqual(effective_box_m2(box, 0.05, 1000, 1000), 50.0)

    def test_effective_falls_back_to_qty_without_scale(self):
        box = {"w": 10, "h": 20, "qty_m2": 34.5, "manual_m2": 0}
        self.assertAlmostEqual(effective_box_m2(box), 34.5)


class CalibratedMergeTests(unittest.TestCase):
    def _png(self, path: Path, w: int = 1000, h: int = 1000):
        from PIL import Image
        Image.new("RGB", (w, h), "white").save(path)

    def test_merge_uses_measured_area_from_calibration(self):
        with tempfile.TemporaryDirectory() as td:
            img_path = str(Path(td) / "elev.png")
            self._png(Path(img_path))
            job = {
                "elevation_progress": {
                    img_path: {
                        "calibration": {"x1": 0, "y1": 0, "x2": 10, "y2": 0, "len_m": 5},
                        "zones": [
                            {
                                "label": "North render",
                                "substrate": "External walls / render",
                                "x": 0, "y": 0, "w": 10, "h": 20,
                                "progress": 0, "qty_m2": 3, "manual_m2": 0,
                            }
                        ],
                    }
                }
            }
            rows = merge_elevation_box_rows(job, [])
            self.assertEqual(len(rows), 1)
            self.assertAlmostEqual(rows[0]["qty_m2"], 50.0, places=2)

    def test_merge_uses_manual_override_even_when_calibrated(self):
        with tempfile.TemporaryDirectory() as td:
            img_path = str(Path(td) / "elev.png")
            self._png(Path(img_path))
            job = {
                "elevation_progress": {
                    img_path: {
                        "calibration": {"x1": 0, "y1": 0, "x2": 10, "y2": 0, "len_m": 5},
                        "zones": [
                            {
                                "label": "North render",
                                "substrate": "External walls / render",
                                "x": 0, "y": 0, "w": 10, "h": 20,
                                "progress": 0, "qty_m2": 7.5, "manual_m2": 7.5,
                            }
                        ],
                    }
                }
            }
            rows = merge_elevation_box_rows(job, [])
            self.assertEqual(len(rows), 1)
            self.assertAlmostEqual(rows[0]["qty_m2"], 7.5, places=2)

    def test_merge_measures_box_even_when_qty_never_entered(self):
        with tempfile.TemporaryDirectory() as td:
            img_path = str(Path(td) / "elev.png")
            self._png(Path(img_path))
            job = {
                "elevation_progress": {
                    img_path: {
                        "calibration": {"x1": 0, "y1": 0, "x2": 10, "y2": 0, "len_m": 5},
                        "zones": [
                            {
                                "label": "North",
                                "substrate": "External walls / render",
                                "x": 0, "y": 0, "w": 10, "h": 20, "qty_m2": 0, "manual_m2": 0,
                            }
                        ],
                    }
                }
            }
            rows = merge_elevation_box_rows(job, [])
            self.assertEqual(len(rows), 1)
            self.assertAlmostEqual(rows[0]["qty_m2"], 50.0, places=2)


if __name__ == "__main__":
    unittest.main()
