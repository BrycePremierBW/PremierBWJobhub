import unittest

from pb_planreader_app import (
    build_takeoff_from_analysis,
    compute_room_takeoff_rows,
    extract_room_dimensions,
    litres_from_area,
)


class ExtractRoomDimensionTests(unittest.TestCase):
    def test_millimetre_pairs_become_metres(self):
        rooms = extract_room_dimensions("LOUNGE 5400 X 3200")
        self.assertEqual(len(rooms), 1)
        room = rooms[0]
        self.assertEqual(room["room"], "Lounge")
        self.assertEqual(room["dim1_m"], 5.4)
        self.assertEqual(room["dim2_m"], 3.2)
        self.assertAlmostEqual(room["area_m2"], 17.28, places=2)

    def test_metre_pairs_with_units(self):
        rooms = extract_room_dimensions("BED 1  3.2m x 2.8m")
        self.assertEqual(len(rooms), 1)
        room = rooms[0]
        self.assertEqual(room["room"], "Bed 1")
        self.assertEqual(room["dim1_m"], 3.2)
        self.assertEqual(room["dim2_m"], 2.8)

    def test_commas_and_mm_suffix(self):
        rooms = extract_room_dimensions("KITCHEN  4,200mm x 3,000")
        self.assertEqual(len(rooms), 1)
        self.assertEqual(rooms[0]["dim1_m"], 4.2)
        self.assertEqual(rooms[0]["dim2_m"], 3.0)

    def test_label_carries_to_adjacent_line(self):
        text = "MASTER BEDROOM\napprox 4.0 x 3.5"
        rooms = extract_room_dimensions(text)
        self.assertEqual(len(rooms), 1)
        self.assertEqual(rooms[0]["room"], "Master Bedroom")

    def test_multiple_rooms_on_one_line(self):
        text = "LOUNGE 5400 x 3200   DINING 4000 x 3200"
        rooms = extract_room_dimensions(text)
        self.assertEqual(len(rooms), 2)
        self.assertEqual(rooms[0]["room"], "Lounge")
        self.assertEqual(rooms[1]["room"], "Dining")

    def test_sheet_size_and_junk_are_rejected(self):
        text = "A3 SHEET 420 x 297  SCALE 1:100  TILE 200 x 200"
        self.assertEqual(extract_room_dimensions(text), [])

    def test_door_schedule_size_without_room_label_is_ignored(self):
        text = "DOOR D1  2400 wide x 900 high"
        self.assertEqual(extract_room_dimensions(text), [])


class RoomTakeoffComputationTests(unittest.TestCase):
    def test_wall_and_ceiling_areas(self):
        rows = compute_room_takeoff_rows(
            [{"room": "Lounge", "dim1_m": 5.4, "dim2_m": 3.2}],
            ceiling_height=2.7,
        )
        wall = [r for r in rows if r["substrate"] == "Internal walls"][0]
        ceiling = [r for r in rows if r["substrate"] == "Internal ceilings"][0]
        self.assertAlmostEqual(wall["qty_m2"], 17.2 * 2.7, places=2)
        self.assertAlmostEqual(ceiling["qty_m2"], 17.28, places=2)
        self.assertEqual(wall["confidence"], "Computed from room dimensions")

    def test_opening_allowance_is_deducted(self):
        rows = compute_room_takeoff_rows(
            [{"room": "Lounge", "dim1_m": 5.4, "dim2_m": 3.2}],
            ceiling_height=2.7,
            openings_allowance_m2=3.0,
        )
        wall = [r for r in rows if r["substrate"] == "Internal walls"][0]
        self.assertAlmostEqual(wall["qty_m2"], 17.2 * 2.7 - 3.0, places=2)

    def test_wall_area_is_never_negative(self):
        rows = compute_room_takeoff_rows(
            [{"room": "Tiny", "dim1_m": 1.0, "dim2_m": 1.0}],
            ceiling_height=2.7,
            openings_allowance_m2=99.0,
        )
        wall = [r for r in rows if r["substrate"] == "Internal walls"][0]
        self.assertEqual(wall["qty_m2"], 0.0)

    def test_invalid_rooms_are_skipped(self):
        rows = compute_room_takeoff_rows(
            [{"room": "Bad", "dim1_m": 0, "dim2_m": 3.0}, {"room": "Ok", "dim1_m": 2.0, "dim2_m": 2.0}]
        )
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["area_location"] == "Ok" for r in rows))


class TakeoffBuildTests(unittest.TestCase):
    def test_room_rows_fill_standard_buckets_once(self):
        analysis = {
            "rooms": [{"room": "Lounge", "dim1_m": 5.4, "dim2_m": 3.2}],
            "painting_snippets": [],
            "area_candidates": [],
        }
        df = build_takeoff_from_analysis(analysis)
        walls = df[df["substrate"] == "Internal walls"]
        self.assertEqual(len(walls), 1)
        self.assertGreater(walls.iloc[0]["qty_m2"], 0)
        ceilings = df[df["substrate"] == "Internal ceilings"]
        self.assertEqual(len(ceilings), 1)
        # The four remaining standard buckets are still present.
        self.assertIn("Doors / frames / trim", set(df["substrate"]))
        self.assertIn("External walls / render / cladding", set(df["substrate"]))

    def test_lineal_trim_quantity_creates_row(self):
        analysis = {
            "rooms": [],
            "painting_snippets": [],
            "area_candidates": [{"source": "Skirting to lounge paint 42 lm", "qty": 42.0, "unit": "lm", "file": "p.pdf", "page": 2}],
        }
        df = build_takeoff_from_analysis(analysis)
        trim = df[df["lineal_m"] == 42.0]
        self.assertEqual(len(trim), 1)
        self.assertEqual(trim.iloc[0]["confidence"], "Medium - lineal quantity found")

    def test_lineal_without_paint_or_trim_is_ignored(self):
        analysis = {
            "rooms": [],
            "painting_snippets": [],
            "area_candidates": [{"source": "Chainage 42 lm", "qty": 42.0, "unit": "lm", "file": "p.pdf", "page": 2}],
        }
        df = build_takeoff_from_analysis(analysis)
        self.assertEqual(len(df[df["lineal_m"] == 42.0]), 0)

    def test_empty_analysis_still_adds_standard_buckets(self):
        df = build_takeoff_from_analysis({"painting_snippets": [], "area_candidates": []})
        self.assertEqual(len(df), 6)


class CoverageTests(unittest.TestCase):
    def test_coverage_param_changes_litres(self):
        self.assertEqual(litres_from_area(100, 2, 10), 20.0)
        self.assertAlmostEqual(litres_from_area(100, 2), round(200 / 12, 2), places=2)


if __name__ == "__main__":
    unittest.main()
