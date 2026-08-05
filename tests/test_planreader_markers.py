import tempfile
import unittest
from pathlib import Path

import pb_planreader_app as pr
from pb_planreader_app import (
    _rebuild_takeoff,
    apply_room_corrections,
    load_corrections,
    save_corrections,
)


class PlanMarkerPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="planreader_markers_"))
        self._old_data = pr.DATA_DIR
        self._old_jobs = pr.JOBS_DIR
        pr.DATA_DIR = self._tmp
        pr.JOBS_DIR = self._tmp / "jobs"

    def tearDown(self):
        pr.DATA_DIR = self._old_data
        pr.JOBS_DIR = self._old_jobs

    def test_save_then_load_round_trips(self):
        markers = [
            {
                "label": "Bedroom 4",
                "x": 0.3,
                "y": 0.4,
                "dim1_m": 3.5,
                "dim2_m": 3.0,
                "file": "currawong.pdf",
                "page": 1,
            },
        ]
        saved = save_corrections("J1", markers)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["area_m2"], 10.5)
        self.assertEqual(saved[0]["source"], "Manual plan marker")
        loaded = load_corrections("J1")
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["label"], "Bedroom 4")
        self.assertEqual(loaded[0]["dim1_m"], 3.5)
        self.assertEqual(loaded[0]["dim2_m"], 3.0)

    def test_deduplicates_identical_markers(self):
        markers = [
            {"label": "Lounge", "x": 0.5, "y": 0.5, "dim1_m": 6.0, "dim2_m": 4.0},
            {"label": "Lounge", "x": 0.6, "y": 0.6, "dim1_m": 6.0, "dim2_m": 4.0},
        ]
        saved = save_corrections("J1", markers)
        self.assertEqual(len(saved), 1)

    def test_drops_blank_labels(self):
        saved = save_corrections("J1", [{"label": "  ", "dim1_m": 3.0, "dim2_m": 3.0}])
        self.assertEqual(saved, [])
        self.assertEqual(load_corrections("J1"), [])

    def test_handles_missing_file(self):
        self.assertEqual(load_corrections("J_ghost"), [])


class PlanMarkerMergeTests(unittest.TestCase):
    def setUp(self):
        self.rooms = [
            {"room": "Lounge", "dim1_m": 5.4, "dim2_m": 3.2, "area_m2": 17.28, "source": "detected"},
            {"room": "Kitchen", "dim1_m": 4.2, "dim2_m": 3.0, "area_m2": 12.6, "source": "detected"},
        ]

    def test_matching_label_overrides_dimensions(self):
        markers = [
            {"label": "Lounge", "x": 0.2, "y": 0.2, "dim1_m": 6.0, "dim2_m": 4.0},
        ]
        merged = apply_room_corrections(self.rooms, markers)
        lounge = next(r for r in merged if r["room"] == "Lounge")
        self.assertEqual(lounge["dim1_m"], 6.0)
        self.assertEqual(lounge["dim2_m"], 4.0)
        self.assertEqual(lounge["area_m2"], 24.0)
        self.assertEqual(lounge["source"], "Corrected via plan marker")
        self.assertEqual(len(merged), 2)

    def test_new_label_appends_room(self):
        markers = [
            {"label": "Bedroom 4", "x": 0.7, "y": 0.6, "dim1_m": 3.5, "dim2_m": 3.0},
        ]
        merged = apply_room_corrections(self.rooms, markers)
        self.assertEqual(len(merged), 3)
        bedroom = next(r for r in merged if r["room"] == "Bedroom 4")
        self.assertEqual(bedroom["area_m2"], 10.5)
        self.assertEqual(bedroom["source"], "Marked on plan")

    def test_blank_marker_list_is_identity(self):
        self.assertEqual(apply_room_corrections(self.rooms, []), self.rooms)

    def test_marker_without_dims_only_overrides_when_match_exists(self):
        markers = [
            {"label": "Kitchen", "x": 0.5, "y": 0.5, "dim1_m": None, "dim2_m": None},
            {"label": "Store", "x": 0.1, "y": 0.1, "dim1_m": None, "dim2_m": None},
        ]
        merged = apply_room_corrections(self.rooms, markers)
        kitchen = next(r for r in merged if r["room"] == "Kitchen")
        self.assertEqual(kitchen["dim1_m"], 4.2)
        self.assertNotIn("Store", [r["room"] for r in merged])
        self.assertEqual(len(merged), 2)

    def test_rebuild_takeoff_includes_corrected_room(self):
        job = {
            "analyses": [
                {
                    "file": "currawong.pdf",
                    "pages": [],
                    "painting_snippets": [],
                    "area_candidates": [],
                }
            ],
        }
        rooms = apply_room_corrections(
            [{"room": "Lounge", "dim1_m": 5.4, "dim2_m": 3.2}],
            [{"label": "Lounge", "x": 0.5, "y": 0.5, "dim1_m": 6.0, "dim2_m": 4.0}],
        )
        df = _rebuild_takeoff(job, rooms)
        wall_rows = df[df["substrate"] == "Internal walls"]
        self.assertFalse(wall_rows.empty)
        self.assertAlmostEqual(float(wall_rows["qty_m2"].iloc[0]), 54.0, places=2)


if __name__ == "__main__":
    unittest.main()
