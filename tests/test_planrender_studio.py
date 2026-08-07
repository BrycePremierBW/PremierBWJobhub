"""Unit tests for the PB PlanRender Takeoff Studio module."""

import unittest

from planrender_studio import (
    CSV_COLUMNS,
    ELEVATION_FACE_LABELS,
    STATUSES,
    SUBSTRATES,
    UNIT_COUNT,
    _face_key,
    _substrate_for_substrate_text,
    build_studio_data,
    export_areas_csv,
    next_area_id,
    render_planrender_studio_html,
    totals,
)


class SubstratePaletteTest(unittest.TestCase):
    def test_all_spec_substrates_present(self):
        codes = {s["code"] for s in SUBSTRATES}
        expected = {
            "EC1", "EC2", "EC3", "RBL", "SOF", "EC5", "BA2 / SCR",
            "BA1", "SHD", "BC / EG / PPT", "RS", "DP", "GD",
        }
        self.assertEqual(codes, expected)

    def test_every_substrate_has_a_hex_swatch(self):
        for s in SUBSTRATES:
            self.assertRegex(s["hex"], r"^#[0-9A-F]{6}$")
        hexes = [s["hex"] for s in SUBSTRATES]
        self.assertEqual(len(hexes), len(set(hexes)), "swatch colours should be unique")

    def test_spec_statuses_present(self):
        for status in ("Paint Included", "Paint Excluded", "Provisional", "Variation",
                       "Completed", "Not Started", "Requires Site Verification"):
            self.assertIn(status, STATUSES)


class DataBuilderTest(unittest.TestCase):
    def test_default_sample_studio_data(self):
        data = build_studio_data()
        self.assertEqual(data["appName"], "PB PlanRender Takeoff Studio")
        self.assertEqual(data["project"]["id"], "sample-king-street")
        self.assertEqual(len(data["units"]), UNIT_COUNT)
        self.assertEqual(len(data["substrates"]), 13)
        self.assertAlmostEqual(data["envelope"]["w"], 30.0)
        self.assertAlmostEqual(data["envelope"]["h"], 9.0)
        self.assertEqual(len(data["elevations"]), 0)
        self.assertEqual(data["areas"], [])

    def test_envelope_scales_units(self):
        data = build_studio_data(
            envelope={"envelope_w_m": 14.0, "envelope_h_m": 9.0, "method": "vector-wall"},
            external_info={"wall_height_m": 2.7},
        )
        self.assertAlmostEqual(data["envelope"]["w"], 14.0)
        self.assertAlmostEqual(data["envelope"]["h"], 2.7)

    def test_job_settings_wall_height_fallback(self):
        job = {"external_settings": {"wall_height_m": 3.1}}
        data = build_studio_data(job=job, envelope={"envelope_w_m": 20.0, "envelope_h_m": 10.0})
        self.assertAlmostEqual(data["envelope"]["h"], 3.1)

    def test_project_label_and_id(self):
        data = build_studio_data(project_label="PB-123 – 1 Test Street", project_id="pb-123")
        self.assertEqual(data["project"]["label"], "PB-123 – 1 Test Street")
        self.assertEqual(data["project"]["id"], "pb-123")
        self.assertEqual(data["projects"][0]["id"], "pb-123")

    def test_elevations_and_seed_areas_flow_through(self):
        data = build_studio_data(
            elevations=[{"key": "front", "label": "Front", "dataUrl": "data:image/jpeg;base64,AA==",
                         "m_per_px": 0.05, "w_px": 100, "h_px": 80, "zones": []}],
            seed_areas=[{"id": "SEED-01", "unit": None, "unit_label": "Whole building",
                         "substrate": "SOF", "area": 12.5, "status": "Paint Included", "progress": 0}],
        )
        self.assertEqual(len(data["elevations"]), 1)
        self.assertEqual(data["elevations"][0]["label"], "Front")
        self.assertEqual(len(data["areas"]), 1)
        self.assertEqual(data["totals"]["total"], 12.5)


class FaceAndSubstrateMappingTest(unittest.TestCase):
    def test_face_keywords(self):
        self.assertEqual(_face_key("job_files/north_elevation.png"), "front")
        self.assertEqual(_face_key("job_files/south rear.png"), "rear")
        self.assertEqual(_face_key("west side"), "left")
        self.assertEqual(_face_key("EAST"), "right")
        self.assertIsNone(_face_key("floor_plan.png"))

    def test_substrate_text_mapping(self):
        self.assertEqual(_substrate_for_substrate_text("External soffits / eaves"), "SOF")
        self.assertEqual(_substrate_for_substrate_text("External walls / render"), "RBL")
        self.assertEqual(_substrate_for_substrate_text("Fascia / gutters / trim"), "BC / EG / PPT")
        self.assertEqual(_substrate_for_substrate_text("Roof"), "RS")
        self.assertIsNone(_substrate_for_substrate_text("Windows / doors / frames"))

    def test_elevation_labels(self):
        self.assertEqual(ELEVATION_FACE_LABELS["front"], "Front – King Street")
        self.assertEqual(ELEVATION_FACE_LABELS["rear"], "Rear – Hamilton Street")


class AreaEngineTest(unittest.TestCase):
    def test_next_area_id_sequence(self):
        existing = [{"id": "A-001"}, {"id": "A-002"}]
        self.assertEqual(next_area_id(existing), "A-003")

    def test_next_area_id_skips_used(self):
        existing = [{"id": "A-001"}, {"id": "A-003"}]
        self.assertEqual(next_area_id(existing), "A-002")

    def test_totals_with_progress(self):
        areas = [
            {"area": 10.0, "progress": 50, "status": "Paint Included"},
            {"area": 5.0, "progress": 100, "status": "Paint Included"},
            {"area": 3.0, "progress": 0, "status": "Paint Excluded"},
        ]
        t = totals(areas)
        self.assertAlmostEqual(t["total"], 15.0)
        self.assertAlmostEqual(t["completed"], 10.0)
        self.assertAlmostEqual(t["remaining"], 5.0)


class CsvExportTest(unittest.TestCase):
    def test_header_matches_spec(self):
        csv_text = export_areas_csv([])
        header = csv_text.strip().split("\r\n")[0]
        self.assertEqual(header.split(","), CSV_COLUMNS)

    def test_row_values_and_progress_math(self):
        csv_text = export_areas_csv([
            {"id": "A-001", "unit_label": "Unit 1", "drawing": "D1", "elevation": "Front – King Street",
             "substrate": "SOF", "area": 6.25, "status": "Paint Included", "progress": 65,
             "notes": "Scaffold required"},
        ])
        lines = csv_text.strip().split("\r\n")
        row = lines[1].split(",")
        self.assertEqual(row[0], "A-001")
        self.assertEqual(row[1], "Unit 1")
        self.assertEqual(row[2], "D1")
        self.assertEqual(row[3], "Front – King Street")
        self.assertEqual(row[4], "SOF")
        self.assertEqual(row[5], "6.25")
        self.assertEqual(row[6], "Paint Included")
        self.assertEqual(row[7], "65")
        self.assertEqual(row[8], "4.06")
        self.assertEqual(row[9], "2.19")
        self.assertEqual(row[10], "Scaffold required")

    def test_csv_quotes_commas_in_notes(self):
        csv_text = export_areas_csv([
            {"id": "A-002", "area": 2.0, "status": "Paint Included", "progress": 0,
             "notes": "Left, right and centre"},
        ])
        self.assertIn('"Left, right and centre"', csv_text)


class HtmlRenderTest(unittest.TestCase):
    def test_html_includes_all_major_ui_elements(self):
        data = build_studio_data()
        html = render_planrender_studio_html(data)
        for marker in (
            "PB PlanRender Takeoff Studio",
            "3D Model",
            "Elevations",
            "Reports",
            "Export",
            "Substrate legend",
            "Draw Box",
            "Realistic",
            "X-Ray / Transparent",
            "Show Soffits",
            "Front – King Street",
            "Rear – Hamilton Street",
            "Aerial Top",
            "Selected Area",
            "Update Area",
            "Delete Area",
            "Export CSV",
            "Download Image",
            "Total Areas",
            "Completed:",
            "Remaining:",
            "PB PlanRender Takeoff Studio v1.0",
            "three.min.js",
        ):
            self.assertIn(marker, html, "missing marker: " + marker)
        self.assertNotIn("__STUDIO_JSON__", html)

    def test_html_embeds_valid_studio_json(self):
        data = build_studio_data(
            envelope={"envelope_w_m": 30.0, "envelope_h_m": 9.0},
            elevations=[{"key": "front", "label": "Front – King Street", "dataUrl": "data:image/jpeg;base64,AA==",
                         "m_per_px": 0.05, "w_px": 100, "h_px": 80, "zones": []}],
        )
        html = render_planrender_studio_html(data)
        marker = "const STUDIO = "
        start = html.index(marker) + len(marker)
        end = html.index(";\n", start)
        payload = html[start:end]
        import json

        parsed = json.loads(payload)
        self.assertEqual(parsed["project"]["id"], "sample-king-street")
        self.assertEqual(len(parsed["substrates"]), 13)
        self.assertEqual(len(parsed["elevations"]), 1)


if __name__ == "__main__":
    unittest.main()
