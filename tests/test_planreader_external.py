import tempfile
import unittest
from pathlib import Path

import pb_planreader_app as pr
from pb_planreader_app import (
    AUTO_EXTERNAL_SOURCE,
    compute_external_takeoff_rows,
    elevation_openings_m2,
    external_footprint,
    merge_auto_external_rows,
)


def _png(path: Path, w: int = 1000, h: int = 800):
    from PIL import Image
    Image.new("RGB", (w, h), "white").save(path)


def _job(plan_png: Path) -> dict:
    return {
        "job_id": "exttest",
        "analyses": [
            {
                "file": "plan.pdf",
                "pages": [{"page": 1, "image_path": str(plan_png)}],
                "painting_snippets": [],
                "area_candidates": [],
            }
        ],
        "rooms": [],
        "elevation_progress": {},
    }


class ExternalFootprintTests(unittest.TestCase):
    def test_marker_envelope_solves_scale_and_perimeter(self):
        with tempfile.TemporaryDirectory() as td:
            png = Path(td) / "plan.png"
            _png(png)
            markers = [
                {"label": "Lounge", "file": "plan.pdf", "page": 1, "x": 0.25, "y": 0.5, "dim1_m": 5, "dim2_m": 8},
                {"label": "Kitchen", "file": "plan.pdf", "page": 1, "x": 0.75, "y": 0.5, "dim1_m": 5, "dim2_m": 8},
            ]
            fp = external_footprint(_job(png), markers, [], 0.15)
            self.assertEqual(fp["method"], "marker-envelope")
            self.assertAlmostEqual(fp["envelope_w_m"], 10.0, places=1)
            self.assertAlmostEqual(fp["envelope_h_m"], 8.0, places=1)
            self.assertAlmostEqual(fp["total_area_m2"], 80.0, places=1)
            self.assertAlmostEqual(fp["perimeter_m"], 37.2, places=1)

    def test_no_markers_falls_back_to_area_estimate(self):
        fp = external_footprint({}, [], [
            {"room": "Lounge", "dim1_m": 5, "dim2_m": 8},
            {"room": "Kitchen", "dim1_m": 5, "dim2_m": 8},
        ], 0.15)
        self.assertEqual(fp["method"], "area-estimate")
        self.assertGreater(fp["perimeter_m"], 0)

    def test_no_rooms_returns_zero(self):
        fp = external_footprint({}, [], [])
        self.assertEqual(fp["perimeter_m"], 0.0)
        self.assertEqual(fp["method"], "none")


class ElevationOpeningsTests(unittest.TestCase):
    def test_sums_measured_window_door_boxes_only(self):
        with tempfile.TemporaryDirectory() as td:
            png = Path(td) / "elev.png"
            _png(png, w=1000, h=1000)
            job = _job(png)
            job["elevation_progress"] = {
                str(png): {
                    "calibration": {"x1": 0, "y1": 0, "x2": 10, "y2": 0, "len_m": 5},
                    "zones": [
                        {
                            "label": "Window",
                            "substrate": "Windows / doors / frames",
                            "x": 0, "y": 0, "w": 4, "h": 2,
                            "progress": 0, "qty_m2": 0, "manual_m2": 0,
                        },
                        {
                            "label": "Render",
                            "substrate": "External walls / render",
                            "x": 0, "y": 0, "w": 10, "h": 20,
                            "progress": 0, "qty_m2": 0, "manual_m2": 0,
                        },
                    ],
                }
            }
            # 4% x 2% of 1000px image @ 0.05 m/px => 40px x 20px = 800 px2 = 2 m2
            self.assertAlmostEqual(elevation_openings_m2(job), 2.0, places=2)


class AutoExternalRowsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="planreader_ext_"))
        self._old_data = pr.DATA_DIR
        self._old_jobs = pr.JOBS_DIR
        pr.DATA_DIR = self._tmp
        pr.JOBS_DIR = self._tmp / "jobs"
        self._png = self._tmp / "plan.png"
        _png(self._png)
        self.job = _job(self._png)
        markers = [
            {"label": "Lounge", "file": "plan.pdf", "page": 1, "x": 0.25, "y": 0.5, "dim1_m": 5, "dim2_m": 8},
            {"label": "Kitchen", "file": "plan.pdf", "page": 1, "x": 0.75, "y": 0.5, "dim1_m": 5, "dim2_m": 8},
        ]
        pr.save_corrections(self.job["job_id"], markers)

    def tearDown(self):
        pr.DATA_DIR = self._old_data
        pr.JOBS_DIR = self._old_jobs

    def test_computes_rows_from_plan_and_elevations(self):
        elev = self._tmp / "elev.png"
        _png(elev, w=1000, h=1000)
        self.job["elevation_progress"] = {
            str(elev): {
                "calibration": {"x1": 0, "y1": 0, "x2": 10, "y2": 0, "len_m": 5},
                "zones": [
                    {
                        "label": "Window",
                        "substrate": "Windows / doors / frames",
                        "x": 0, "y": 0, "w": 4, "h": 2,
                        "progress": 0, "qty_m2": 0, "manual_m2": 0,
                    }
                ],
            }
        }
        rows, info = compute_external_takeoff_rows(self.job)
        self.assertEqual(info["perimeter_m"], 37.2)
        self.assertAlmostEqual(info["openings_m2"], 2.0, places=2)
        self.assertAlmostEqual(info["gross_walls_m2"], 100.44, places=2)
        self.assertAlmostEqual(info["net_walls_m2"], 98.44, places=2)
        self.assertAlmostEqual(info["soffits_m2"], 16.74, places=2)
        self.assertEqual(info["fascia_lineal_m"], 37.2)
        by_substrate = {r["substrate"]: r for r in rows}
        self.assertIn("External walls / render", by_substrate)
        self.assertAlmostEqual(by_substrate["External walls / render"]["qty_m2"], 98.44, places=2)
        self.assertEqual(by_substrate["Fascia / gutters / trim"]["lineal_m"], 37.2)
        self.assertIn("Windows / doors / frames", by_substrate)

    def test_merge_replaces_old_auto_rows_keeps_others(self):
        base = [
            {"source_note": AUTO_EXTERNAL_SOURCE, "substrate": "External walls / render", "qty_m2": 999},
            {"source_note": "Room dimension Lounge: 3 x 4 m", "substrate": "Internal walls", "qty_m2": 30},
        ]
        merged = merge_auto_external_rows(self.job, base)
        auto = [r for r in merged if r.get("source_note") == AUTO_EXTERNAL_SOURCE]
        self.assertEqual(len(auto), 3)
        self.assertTrue(any(r["qty_m2"] == 999 for r in merged) is False)
        self.assertTrue(any(r.get("substrate") == "Internal walls" for r in merged))

    def test_merge_drops_auto_rows_when_no_footprint(self):
        job = _job(self._png)
        job.pop("job_id")
        pr.save_corrections("", [])
        base = [{"source_note": AUTO_EXTERNAL_SOURCE, "substrate": "External walls / render", "qty_m2": 999}]
        merged = merge_auto_external_rows(job, base)
        self.assertEqual(merged, [])


if __name__ == "__main__":
    unittest.main()
