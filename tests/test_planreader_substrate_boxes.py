import tempfile
import unittest
from pathlib import Path

import pb_planreader_app as pr
from pb_planreader_app import (
    ELEVATION_BOX_SOURCE,
    merge_elevation_box_rows,
    normalise_boxes,
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


if __name__ == "__main__":
    unittest.main()
