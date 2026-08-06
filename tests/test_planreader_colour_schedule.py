import tempfile
import unittest
from pathlib import Path

import pb_planreader_app as pr
from pb_planreader_app import (
    colour_schedule_df,
    colour_schedule_excel_bytes,
    colour_schedule_pdf_bytes,
    default_colour_finish,
    generate_colour_markup_images,
    markup_plan_image_bytes,
    normalise_colour_schedule,
    resolve_colour_hex,
    seed_colour_schedule,
)


class ColourScheduleTests(unittest.TestCase):
    def test_seed_from_takeoff_and_rooms(self):
        job = {
            "takeoff_rows": [
                {"area_location": "Lounge", "substrate": "Internal walls"},
                {"area_location": "Lounge", "substrate": "Ceilings"},
                {"area_location": "External", "substrate": "External walls"},
            ],
            "rooms": [
                {"room": "Lounge", "dim1_m": 4, "dim2_m": 5},
                {"room": "Bedroom 1", "dim1_m": 3, "dim2_m": 3},
            ],
        }
        rows = seed_colour_schedule(job)
        keys = {(r["area_location"], r["surface"]) for r in rows}
        self.assertIn(("Lounge", "Walls"), keys)
        self.assertIn(("Lounge", "Ceiling"), keys)
        self.assertIn(("External", "External walls"), keys)
        self.assertIn(("Bedroom 1", "Walls"), keys)
        self.assertGreaterEqual(len(rows), 4)

    def test_seed_no_duplicate_walls(self):
        job = {
            "takeoff_rows": [{"area_location": "Kitchen", "substrate": "Internal walls"}],
            "rooms": [{"room": "Kitchen", "dim1_m": 3, "dim2_m": 4}],
        }
        rows = seed_colour_schedule(job)
        walls = [r for r in rows if r["area_location"] == "Kitchen" and r["surface"] == "Walls"]
        self.assertEqual(len(walls), 1)

    def test_normalise_dedupes_and_sorts(self):
        raw = [
            {"area_location": "Zed", "surface": "Walls", "colour": "White"},
            {"area_location": "Alpha", "surface": "Ceiling", "colour": "Antique White"},
            {"area_location": "Zed", "surface": "Walls", "colour": "White"},
        ]
        rows = normalise_colour_schedule(raw)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["area_location"], "Alpha")

    def test_default_finish(self):
        self.assertEqual(default_colour_finish("Walls"), "Low Sheen")
        self.assertEqual(default_colour_finish("Ceiling"), "Flat / Matt")

    def test_resolve_hex(self):
        self.assertEqual(resolve_colour_hex("Antique White"), "#F0E6D2")
        self.assertEqual(resolve_colour_hex("#FF0000"), "#FF0000")
        self.assertEqual(resolve_colour_hex("xyzzy not a colour"), "")
        self.assertEqual(resolve_colour_hex(""), "")

    def test_excel_and_pdf(self):
        job = {"job_name": "Test", "colour_schedule": [
            {"area_location": "Lounge", "surface": "Walls", "colour": "Antique White", "finish": "Low Sheen", "product": "", "notes": "", "hex": ""},
        ]}
        excel = colour_schedule_excel_bytes(job)
        self.assertTrue(excel.startswith(b"PK"))
        pdf = colour_schedule_pdf_bytes(job)
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_markup_image(self):
        with tempfile.TemporaryDirectory(prefix="pr_markup_") as td:
            img_path = Path(td) / "plan.png"
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (800, 600), "white")
            draw = ImageDraw.Draw(img)
            draw.rectangle([100, 100, 400, 300], outline="black", width=3)
            img.save(img_path)
            option = {"image_path": str(img_path), "file": "plan.pdf", "page": 1}
            markers = [{"label": "Lounge", "x": 0.3, "y": 0.3, "file": "plan.pdf", "page": 1}]
            schedule = [{"area_location": "Lounge", "surface": "Walls", "colour": "Antique White", "finish": "Low Sheen", "product": "", "notes": "", "hex": ""}]
            png = markup_plan_image_bytes(option, schedule, markers)
            self.assertTrue(png.startswith(b"\x89PNG"))
            self.assertGreater(len(png), 2000)


class ColourMarkupFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="planreader_colour_markup_"))
        self._old_data = pr.DATA_DIR
        self._old_jobs = pr.JOBS_DIR
        pr.DATA_DIR = self._tmp
        pr.JOBS_DIR = self._tmp / "jobs"

    def tearDown(self):
        pr.DATA_DIR = self._old_data
        pr.JOBS_DIR = self._old_jobs

    def test_generate_markup_images_for_job(self):
        from PIL import Image
        job_id = "PB-X_Test"
        job_dir = pr.job_dir(job_id)
        img_path = job_dir / "converted_images" / "p1.png"
        Image.new("RGB", (600, 400), "white").save(img_path)
        job = {
            "job_no": "PB-X", "job_name": "Test",
            "analyses": [{"file": "plan.pdf", "pages": [{"page": 1, "image_path": str(img_path), "page_type": "plan"}]}],
            "rooms": [{"room": "Lounge", "dim1_m": 4, "dim2_m": 5}],
            "colour_schedule": [{"area_location": "Lounge", "surface": "Walls", "colour": "Antique White", "finish": "Low Sheen", "product": "", "notes": "", "hex": ""}],
        }
        pr.save_corrections(job_id, [{"label": "Lounge", "x": 0.5, "y": 0.5, "dim1_m": 4, "dim2_m": 5, "file": "plan.pdf", "page": 1}])
        created = generate_colour_markup_images(job_id, job)
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].exists())
        self.assertTrue(created[0].read_bytes().startswith(b"\x89PNG"))

    def test_schedule_df_defaults(self):
        job = {"takeoff_rows": [{"area_location": "Lounge", "substrate": "Internal walls"}], "rooms": []}
        df = colour_schedule_df(job)
        self.assertGreaterEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["surface"], "Walls")


if __name__ == "__main__":
    unittest.main()
