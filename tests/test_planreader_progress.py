import importlib.util
import tempfile
import unittest
from pathlib import Path

import pb_planreader_app as pr
from pb_planreader_app import (
    build_elevation_board,
    elevation_image_options,
    normalise_progress,
    render_elevation_overlay,
    zone_colour,
    zone_rect_px,
)

HAS_FITZ = importlib.util.find_spec("fitz") is not None


def _write_png(path: Path, width: int, height: int, colour=(255, 255, 255, 255)):
    from PIL import Image

    img = Image.new("RGBA", (width, height), colour)
    img.save(str(path))
    return path


class ProgressNormalisationTests(unittest.TestCase):
    def test_clamps_and_rounds(self):
        self.assertEqual(normalise_progress(50), 50.0)
        self.assertEqual(normalise_progress(-5), 0.0)
        self.assertEqual(normalise_progress(150), 100.0)
        self.assertEqual(normalise_progress("33.333"), 33.3)
        self.assertEqual(normalise_progress(None), 0.0)
        self.assertEqual(normalise_progress("abc"), 0.0)

    def test_zone_colour_boundaries(self):
        self.assertEqual(zone_colour(0), (128, 128, 128))
        self.assertEqual(zone_colour(1), (230, 140, 30))
        self.assertEqual(zone_colour(49), (230, 140, 30))
        self.assertEqual(zone_colour(50), (255, 180, 0))
        self.assertEqual(zone_colour(99), (255, 180, 0))
        self.assertEqual(zone_colour(100), (0, 150, 60))


class ZoneGeometryTests(unittest.TestCase):
    def test_percent_to_pixel_math_is_exact(self):
        rect = zone_rect_px({"x": 12.5, "y": 20, "w": 30, "h": 40}, 1000, 800)
        self.assertEqual(rect, (125, 160, 300, 320))

    def test_percent_to_pixel_math_is_consistent(self):
        rect = zone_rect_px({"x": 0.1, "y": 0.2, "w": 99.8, "h": 99.8}, 1000, 800)
        self.assertEqual(rect, (1, 2, 998, 798))

    def test_coords_are_clamped_to_image_bounds(self):
        rect = zone_rect_px({"x": 120, "y": 120, "w": 500, "h": 500}, 100, 100)
        self.assertEqual(rect, (99, 99, 1, 1))


class ElevationOverlayTests(unittest.TestCase):
    def _make_overlay(self, path: Path, zones, progress):
        src = Path(tempfile.gettempdir()) / "pr_overlay_src.png"
        _write_png(src, 100, 100, (255, 255, 255, 255))
        return render_elevation_overlay(str(src), zones, str(path), progress)

    def test_render_preserves_dimensions_and_is_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            zones = [{"label": "Wall", "x": 0, "y": 0, "w": 100, "h": 100, "progress": 100}]
            a = self._make_overlay(Path(td) / "a.png", zones, 100)
            b = self._make_overlay(Path(td) / "b.png", zones, 100)
            from PIL import Image

            with Image.open(str(a)) as im:
                self.assertEqual(im.size, (100, 100))
            self.assertEqual(a.read_bytes(), b.read_bytes())
            self.assertTrue(a.read_bytes().startswith(b"\x89PNG"))

    def test_green_zone_blends_over_white_base(self):
        with tempfile.TemporaryDirectory() as td:
            zones = [{"label": "", "x": 0, "y": 0, "w": 100, "h": 100, "progress": 100}]
            out = self._make_overlay(Path(td) / "o.png", zones, 100)
            from PIL import Image

            with Image.open(str(out)).convert("RGB") as im:
                px = im.getpixel((50, 50))
        # white base 255,255,255 blended with green(0,150,60) at alpha 120/255.
        self.assertLess(abs(px[0] - 135), 2)
        self.assertLess(abs(px[1] - 206), 2)
        self.assertLess(abs(px[2] - 163), 2)


class ElevationBoardTests(unittest.TestCase):
    def test_board_is_built_deterministically(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            src1 = _write_png(td / "e1.png", 120, 80)
            src2 = _write_png(td / "e2.png", 100, 60)
            entries = [(str(src1), "North", 0), (str(src2), "South", 100)]
            a = build_elevation_board(entries, str(td / "b1.png"))
            b = build_elevation_board(entries, str(td / "b2.png"))
            self.assertIsNotNone(a)
            self.assertIsNotNone(b)
            self.assertEqual(a.read_bytes(), b.read_bytes())
            self.assertTrue(a.read_bytes().startswith(b"\x89PNG"))


class ElevationOptionTests(unittest.TestCase):
    def test_elevations_are_found_and_deduped(self):
        with tempfile.TemporaryDirectory() as td:
            img = _write_png(Path(td) / "elev.png", 50, 50)
            job = {
                "analyses": [{
                    "file": "drawing.pdf",
                    "pages": [
                        {"page": 2, "page_type": "elevation", "title": "North Elevation", "image_path": str(img)},
                        {"page": 3, "page_type": "elevation", "title": "North Elevation", "image_path": str(img)},
                        {"page": 1, "page_type": "floor_plan", "title": "Floor", "image_path": str(img)},
                    ],
                }],
                "files": [],
            }
            opts = elevation_image_options(job)
            self.assertEqual(len(opts), 1)
            self.assertEqual(opts[0]["image_path"], str(img))

    def test_drawing_image_files_are_included(self):
        with tempfile.TemporaryDirectory() as td:
            img = _write_png(Path(td) / "photo.png", 50, 50)
            job = {
                "analyses": [],
                "files": [{"name": "east.png", "path": str(img), "category": "Drawing image"}],
            }
            opts = elevation_image_options(job)
            self.assertEqual(len(opts), 1)
            self.assertEqual(opts[0]["label"], "east.png")

    def test_missing_files_are_skipped(self):
        job = {
            "analyses": [{
                "file": "d.pdf",
                "pages": [{"page": 1, "page_type": "elevation", "title": "X", "image_path": "C:/nope/missing.png"}],
            }],
            "files": [],
        }
        self.assertEqual(elevation_image_options(job), [])


@unittest.skipUnless(HAS_FITZ, "PyMuPDF not installed")
class PdfPageImagePathTests(unittest.TestCase):
    def test_elevation_page_record_carries_image_path(self):
        import fitz

        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "plan.pdf"
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((72, 100), "NORTH ELEVATION   REAR ELEVATION  painting render", fontsize=14)
            doc.save(str(pdf))
            doc.close()
            analysis = pr.analyse_pdf(pdf, render_pages=True, dpi=72)
            elev = [p for p in analysis["pages"] if p.get("page_type") == "elevation"]
            self.assertGreaterEqual(len(elev), 1)
            img_path = elev[0].get("image_path", "")
            self.assertTrue(img_path)
            self.assertTrue(Path(img_path).exists())
            self.assertTrue(Path(img_path).read_bytes().startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
