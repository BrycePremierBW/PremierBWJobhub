import unittest

from planreader_bluebeam import (
    calibration_consistency_warnings,
    detect_scale_from_text,
    extract_story_levels,
    infer_stories_from_pages,
    manual_calibration_from_scale,
    nearest_scale_ratio,
    parse_scale_ratio,
    scale_ratio_label,
    scale_ratio_to_m_per_pt,
    scale_ratio_to_m_per_px,
    side_area_summary,
)


class ParseScaleRatioTests(unittest.TestCase):
    def test_colon_form(self):
        self.assertEqual(parse_scale_ratio("1:100"), 100.0)
        self.assertEqual(parse_scale_ratio("1 : 200"), 200.0)

    def test_title_block_forms(self):
        self.assertEqual(parse_scale_ratio("SCALE 1:100 @ A3"), 100.0)
        self.assertEqual(parse_scale_ratio("Scale 1:50"), 50.0)

    def test_slash_and_in_forms(self):
        self.assertEqual(parse_scale_ratio("1/50"), 50.0)
        self.assertEqual(parse_scale_ratio("1 in 500"), 500.0)
        self.assertEqual(parse_scale_ratio("1 to 100"), 100.0)

    def test_rejects_non_scale(self):
        self.assertIsNone(parse_scale_ratio(""))
        self.assertIsNone(parse_scale_ratio("Lounge 5400 x 3200"))
        self.assertIsNone(parse_scale_ratio("scale bar"))
        self.assertIsNone(parse_scale_ratio("2:100"))


class ScaleConversionTests(unittest.TestCase):
    def test_m_per_pt_1_100(self):
        # 1:100 in mm drawing units -> 100 * 25.4/72 / 1000 m/pt
        self.assertAlmostEqual(scale_ratio_to_m_per_pt(100), 100 * 25.4 / 72.0 / 1000.0, places=8)

    def test_m_per_px_1_100_at_150_dpi(self):
        # 100 * 0.0254 / 150
        self.assertAlmostEqual(scale_ratio_to_m_per_px(100, 150), 100 * 0.0254 / 150.0, places=8)

    def test_m_per_px_default_dpi(self):
        self.assertAlmostEqual(scale_ratio_to_m_per_px(100), 100 * 0.0254 / 150.0, places=8)

    def test_m_per_px_1_50(self):
        self.assertAlmostEqual(scale_ratio_to_m_per_px(50, 150), 50 * 0.0254 / 150.0, places=8)

    def test_invalid_inputs(self):
        self.assertIsNone(scale_ratio_to_m_per_pt(0))
        self.assertIsNone(scale_ratio_to_m_per_pt(None))
        self.assertIsNone(scale_ratio_to_m_per_px(100, 0))
        self.assertIsNone(scale_ratio_to_m_per_px(None, 150))


class NearestScaleTests(unittest.TestCase):
    def test_snaps_to_common_ratio(self):
        # 1:100 in m/pt
        mpt = scale_ratio_to_m_per_pt(100)
        self.assertEqual(nearest_scale_ratio(mpt), 100.0)
        self.assertEqual(nearest_scale_ratio(scale_ratio_to_m_per_pt(200)), 200.0)
        self.assertEqual(nearest_scale_ratio(scale_ratio_to_m_per_pt(50)), 50.0)

    def test_invalid(self):
        self.assertIsNone(nearest_scale_ratio(0))
        self.assertIsNone(nearest_scale_ratio(None))


class ManualCalibrationTests(unittest.TestCase):
    def test_builds_full_width_calibration(self):
        cal = manual_calibration_from_scale(100, 150, 1000, 800)
        self.assertIsNotNone(cal)
        self.assertEqual(cal["x1"], 0.0)
        self.assertEqual(cal["x2"], 100.0)
        self.assertAlmostEqual(cal["len_m"], 1000 * (100 * 0.0254 / 150.0), places=4)

    def test_invalid_inputs(self):
        self.assertIsNone(manual_calibration_from_scale(None, 150, 1000, 800))
        self.assertIsNone(manual_calibration_from_scale(100, 150, 0, 800))


class DetectScaleFromTextTests(unittest.TestCase):
    def test_finds_scale_in_title_block(self):
        found = detect_scale_from_text(
            "PREMIER PAINTING\nPROJECT: HILLS HOUSE\nSCALE 1:100 @ A1\nREV A"
        )
        self.assertIsNotNone(found)
        self.assertEqual(found["ratio"], 100.0)
        self.assertEqual(found["source"], "title-block")
        self.assertEqual(found["label"], "1:100")

    def test_returns_none_without_scale(self):
        self.assertIsNone(detect_scale_from_text("Lounge 5400 x 3200\nBedroom 3600 x 3000"))
        self.assertIsNone(detect_scale_from_text(""))


class StoryTests(unittest.TestCase):
    def test_extract_levels(self):
        self.assertEqual(extract_story_levels("GROUND FLOOR PLAN"), [0])
        self.assertEqual(extract_story_levels("LEVEL 1 FLOOR PLAN"), [1])
        self.assertEqual(extract_story_levels("FIRST FLOOR PLAN"), [1])
        self.assertEqual(extract_story_levels("SECOND FLOOR PLAN"), [2])
        self.assertEqual(
            extract_story_levels("GROUND FLOOR PLAN\nLEVEL 1 FLOOR PLAN"),
            [0, 1],
        )

    def test_infer_stories(self):
        pages = [
            {"title": "GROUND FLOOR PLAN"},
            {"title": "LEVEL 1 FLOOR PLAN"},
            {"title": "NORTH ELEVATION"},
        ]
        self.assertEqual(infer_stories_from_pages(pages), 2)

    def test_infer_stories_uses_all_text(self):
        pages = [{"title": "FLOOR PLAN"}]
        self.assertEqual(infer_stories_from_pages(pages), 1)
        self.assertEqual(infer_stories_from_pages(pages, all_text="SECOND FLOOR PLAN"), 3)

    def test_infer_stories_defaults_to_one(self):
        self.assertEqual(infer_stories_from_pages([]), 1)
        self.assertEqual(infer_stories_from_pages(None), 1)


class ConsistencyWarningsTests(unittest.TestCase):
    def test_flags_mismatch(self):
        boxes = [
            {"label": "Front wall", "manual_m2": 10.0, "x": 10, "y": 10, "w": 20, "h": 20},
        ]
        warnings = calibration_consistency_warnings(boxes, 0.01, 1000, 1000)
        # drawn = (0.20*1000)*(0.20*1000)*0.01*0.01 = 4 m2 vs manual 10
        self.assertEqual(len(warnings), 1)
        self.assertIn("Front wall", warnings[0])

    def test_no_warning_when_consistent(self):
        boxes = [
            {"label": "Wall", "manual_m2": 4.0, "x": 10, "y": 10, "w": 20, "h": 20},
        ]
        warnings = calibration_consistency_warnings(boxes, 0.01, 1000, 1000)
        self.assertEqual(warnings, [])

    def test_ignores_missing_manual_or_drawn(self):
        boxes = [{"label": "Wall", "manual_m2": 0.0, "x": 10, "y": 10, "w": 20, "h": 20}]
        self.assertEqual(calibration_consistency_warnings(boxes, 0.01, 1000, 1000), [])
        boxes = [{"label": "Wall", "manual_m2": 10.0, "w": 0, "h": 0}]
        self.assertEqual(calibration_consistency_warnings(boxes, 0.01, 1000, 1000), [])


class SideAreaSummaryTests(unittest.TestCase):
    def test_single_storey(self):
        s = side_area_summary(12.0, 9.0, stories=1, wall_height_m=2.7)
        self.assertEqual(s["stories"], 1)
        self.assertAlmostEqual(s["per_story_m2"], 2 * (12 + 9) * 2.7, places=2)
        self.assertAlmostEqual(s["gross_walls_m2"], 2 * (12 + 9) * 2.7, places=2)

    def test_two_storey(self):
        s = side_area_summary(12.0, 9.0, stories=2, wall_height_m=2.7)
        self.assertEqual(s["stories"], 2)
        self.assertAlmostEqual(s["gross_walls_m2"], 2 * 2 * (12 + 9) * 2.7, places=2)
        self.assertAlmostEqual(s["volume_m3"], 12 * 9 * 2.7 * 2, places=2)

    def test_openings_net(self):
        s = side_area_summary(12.0, 9.0, stories=2, wall_height_m=2.7, openings_m2=20.0)
        gross = 2 * 2 * (12 + 9) * 2.7
        self.assertAlmostEqual(s["net_walls_m2"], gross - 20.0, places=2)

    def test_bad_inputs_default(self):
        s = side_area_summary(0, 0, stories=0, wall_height_m=0)
        self.assertEqual(s["gross_walls_m2"], 0.0)
        self.assertEqual(s["stories"], 1)


class LabelTests(unittest.TestCase):
    def test_label(self):
        self.assertEqual(scale_ratio_label(100), "1:100")
        self.assertEqual(scale_ratio_label(50), "1:50")
        self.assertEqual(scale_ratio_label(None), "")


if __name__ == "__main__":
    unittest.main()
