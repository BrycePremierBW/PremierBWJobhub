"""Unit tests for the PlanReader external 3D render module."""

import json
import unittest

from planreader_3d import (
    _elevation_faces,
    _face_from_key,
    default_resolve_hex,
    external_scene_data,
    render_planreader_3d_html,
)


def _sample_job():
    return {
        "job_id": "pb-3d-test",
        "job_no": "PB-3D",
        "rooms": [
            {"room": "Lounge", "dim1_m": 5.0, "dim2_m": 4.0, "area_m2": 20.0},
            {"room": "Kitchen", "dim1_m": 4.0, "dim2_m": 3.5, "area_m2": 14.0},
        ],
        "external_settings": {
            "wall_height_m": 2.7,
            "eave_depth_m": 0.45,
            "wall_thickness_m": 0.15,
        },
        "colour_schedule": [
            {"area_location": "External walls", "surface": "Walls", "colour": "Charcoal", "hex": ""},
            {"area_location": "Soffits", "surface": "Soffit", "colour": "Off white", "hex": ""},
            {"area_location": "Fascia / trim", "surface": "Trim", "colour": "White", "hex": ""},
            {"area_location": "Roof", "surface": "Roof", "colour": "Slate", "hex": ""},
        ],
        "elevation_progress": {
            "job_files/north_elevation.png": {
                "zones": [
                    {"label": "Window 1", "substrate": "Windows / doors / frames",
                     "x": 20.0, "y": 30.0, "w": 15.0, "h": 40.0, "qty_m2": 3.6},
                ]
            },
            "job_files/side_west.png": {
                "zones": [
                    {"label": "Garage door", "substrate": "Windows / doors / frames",
                     "x": 10.0, "y": 50.0, "w": 30.0, "h": 30.0, "qty_m2": 6.0},
                ]
            },
        },
    }


class ExternalSceneDataTest(unittest.TestCase):
    def test_envelope_from_provided_footprint(self):
        job = _sample_job()
        scene = external_scene_data(
            job,
            {"envelope_w_m": 14.0, "envelope_h_m": 9.0, "perimeter_m": 46.0, "method": "vector-wall"},
        )
        self.assertEqual(scene["envelope"]["w"], 14.0)
        self.assertEqual(scene["envelope"]["d"], 9.0)
        self.assertEqual(scene["envelope"]["h"], 2.7)
        self.assertEqual(scene["envelope"]["t"], 0.15)
        self.assertEqual(scene["summary"]["gross_walls_m2"], round(2 * (14 + 9) * 2.7, 2))

    def test_missing_envelope_falls_back_to_placeholder(self):
        scene = external_scene_data(_sample_job(), None)
        self.assertGreater(scene["envelope"]["w"], 0)
        self.assertGreater(scene["envelope"]["d"], 0)
        self.assertEqual(scene["envelope"]["method"], "none")

    def test_colours_from_colour_schedule(self):
        scene = external_scene_data(_sample_job(), {"envelope_w_m": 14.0, "envelope_h_m": 9.0})
        self.assertEqual(scene["walls"]["colour"], "#3B3B3B")
        self.assertEqual(scene["eave"]["colour"], "#F5F3EC")
        self.assertEqual(scene["trim"]["colour"], "#FFFFFF")
        self.assertEqual(scene["roof"]["colour"], "#5B6570")

    def test_palette_lists_scheduled_external_colours(self):
        scene = external_scene_data(_sample_job(), {"envelope_w_m": 14.0, "envelope_h_m": 9.0})
        names = {p["name"] for p in scene["palette"]}
        self.assertIn("Charcoal", names)
        self.assertIn("Off white", names)
        self.assertIn("Slate", names)

    def test_openings_mapped_to_faces(self):
        scene = external_scene_data(_sample_job(), {"envelope_w_m": 14.0, "envelope_h_m": 9.0})
        self.assertEqual(len(scene["openings"]["front"]), 1)
        self.assertEqual(len(scene["openings"]["left"]), 1)
        self.assertEqual(len(scene["openings"]["rear"]), 0)
        self.assertEqual(len(scene["openings"]["right"]), 0)
        front = scene["openings"]["front"][0]
        self.assertAlmostEqual(front["x_frac"], 0.275)
        self.assertAlmostEqual(front["y_frac"], 0.5)
        self.assertAlmostEqual(front["w_frac"], 0.15)
        self.assertAlmostEqual(front["h_frac"], 0.4)
        self.assertAlmostEqual(scene["summary"]["openings_m2"], 9.6)

    def test_summary_calculations(self):
        job = _sample_job()
        external_info = {
            "wall_height_m": 2.7,
            "eave_depth_m": 0.45,
            "wall_thickness_m": 0.15,
            "openings_m2": 5.0,
            "perimeter_m": 46.0,
        }
        scene = external_scene_data(job, {"envelope_w_m": 14.0, "envelope_h_m": 9.0}, external_info)
        self.assertEqual(scene["summary"]["gross_walls_m2"], round(2 * (14 + 9) * 2.7, 2))
        self.assertEqual(scene["summary"]["openings_m2"], 5.0)
        self.assertEqual(scene["summary"]["net_walls_m2"], round(2 * (14 + 9) * 2.7 - 5.0, 2))
        self.assertGreater(scene["summary"]["soffits_m2"], 0)


class FaceMappingTest(unittest.TestCase):
    def test_face_keywords(self):
        self.assertEqual(_face_from_key("front elevation north.png"), "front")
        self.assertEqual(_face_from_key("south rear"), "rear")
        self.assertEqual(_face_from_key("west side"), "left")
        self.assertEqual(_face_from_key("east"), "right")
        self.assertIsNone(_face_from_key("floor plan"))

    def test_elevation_faces_groups_by_keyword(self):
        faces = _elevation_faces(_sample_job())
        self.assertEqual(len(faces["front"]), 1)
        self.assertEqual(len(faces["left"]), 1)


class HexResolutionTest(unittest.TestCase):
    def test_resolve_hex(self):
        self.assertEqual(default_resolve_hex("Charcoal"), "#3B3B3B")
        self.assertEqual(default_resolve_hex("#112233"), "#112233")
        self.assertEqual(default_resolve_hex(""), "#F1EDE4")


class HtmlRenderTest(unittest.TestCase):
    def test_html_renders_scene_json(self):
        scene = external_scene_data(_sample_job(), {"envelope_w_m": 14.0, "envelope_h_m": 9.0})
        html = render_planreader_3d_html(scene)
        self.assertIn("three.min.js", html)
        self.assertIn("OrbitControls", html)
        self.assertNotIn("__SCENE_JSON__", html)
        self.assertIn("14.0", html)
        self.assertIn("three.min.js", html)

    def test_html_is_valid_json_embedding(self):
        scene = external_scene_data(_sample_job(), {"envelope_w_m": 14.0, "envelope_h_m": 9.0})
        html = render_planreader_3d_html(scene)
        marker = "const S = "
        start = html.index(marker) + len(marker)
        end = html.index(";\n", start)
        payload = html[start:end]
        data = json.loads(payload)
        self.assertEqual(data["envelope"]["w"], 14.0)
        self.assertEqual(len(data["openings"]["front"]), 1)


if __name__ == "__main__":
    unittest.main()
