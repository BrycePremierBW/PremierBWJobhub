import tempfile
import unittest
from pathlib import Path

from PIL import Image

import pb_planreader_app as pr


class PlanReaderFloorM2Tests(unittest.TestCase):
    def test_calibrated_floor_box_creates_internal_floor_basis_row(self):
        with tempfile.TemporaryDirectory() as td:
            image_path = Path(td) / "floor.png"
            Image.new("RGB", (100, 100), "white").save(image_path)
            job = {
                "floor_measurements": {
                    str(image_path): {
                        "calibration": {
                            "x1": 0.0,
                            "y1": 0.0,
                            "x2": 100.0,
                            "y2": 0.0,
                            "len_m": 10.0,
                        },
                        "zones": [
                            {
                                "id": "unit-501",
                                "label": "Unit 501",
                                "substrate": pr.FLOOR_M2_OPTIONS[0],
                                "x": 0.0,
                                "y": 0.0,
                                "w": 50.0,
                                "h": 50.0,
                                "progress": 0,
                                "qty_m2": 0,
                                "manual_m2": 0,
                            }
                        ],
                    }
                }
            }
            rows = pr.merge_floor_area_rows(job, [])
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["internal_external"], "Internal")
            self.assertEqual(row["measurement_basis"], "Floor m²")
            self.assertEqual(row["area_location"], "Unit 501")
            self.assertAlmostEqual(row["qty_m2"], 25.0)
            self.assertEqual(row["paint_litres"], 0.0)
            self.assertEqual(row["coats"], 0)

    def test_floor_basis_recalculation_does_not_invent_paint(self):
        rows = [
            {
                "measurement_basis": "Floor m²",
                "substrate": "Internal works (Floor m² basis)",
                "labour_category": "General",
                "qty_m2": 120.0,
                "lineal_m": 0.0,
                "coats": 0,
                "paint_litres": 99.0,
                "labour_hours": 99.0,
            }
        ]
        result = pr.recalculate_takeoff_values(rows)
        self.assertEqual(result[0]["paint_litres"], 0.0)
        self.assertEqual(result[0]["labour_hours"], 0.0)

    def test_floor_source_rows_are_replaced_not_duplicated(self):
        existing = [
            {
                "source_note": pr.FLOOR_AREA_SOURCE,
                "measurement_basis": "Floor m²",
                "qty_m2": 999.0,
            },
            {"source_note": "Keep me", "qty_m2": 1.0},
        ]
        rows = pr.merge_floor_area_rows({"floor_measurements": {}}, existing)
        self.assertEqual(rows, [{"source_note": "Keep me", "qty_m2": 1.0}])


if __name__ == "__main__":
    unittest.main()
