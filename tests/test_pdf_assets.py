import pathlib
import unittest

from PIL import Image
from pypdf import PdfReader


ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"


def form_summary(path):
    reader = PdfReader(str(path))
    fields = reader.get_fields() or {}
    widgets = []
    for page in reader.pages:
        for reference in page.get("/Annots", []) or []:
            annotation = reference.get_object()
            if annotation.get("/Subtype") == "/Widget":
                widgets.append(annotation)
    return reader, fields, widgets


class PdfAssetTests(unittest.TestCase):
    def test_day_labour_template_is_generic_and_complete(self):
        reader, fields, widgets = form_summary(
            TEMPLATES / "Day_Labour_Sheet_FILLABLE.pdf"
        )
        expected = {
            "job_number",
            "project_name",
            "site_address",
            "builder_client",
            "leading_hand",
        }
        expected |= {
            f"{prefix}_{index:02d}"
            for prefix in ("task", "date_completed", "hours", "signed")
            for index in range(1, 19)
        }
        self.assertEqual(set(fields), expected)
        self.assertEqual(len(widgets), len(expected))
        self.assertTrue(
            all(
                annotation.get("/AP") and annotation.get("/AP").get("/N")
                for annotation in widgets
            )
        )
        extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
        self.assertNotRegex(extracted, r"\b\d{3,}\s+\S+\s+(?:RD|ROAD|ST|STREET)\b")

    def test_supplied_templates_match_jobhub_field_contracts(self):
        _reader, master_fields, master_widgets = form_summary(
            TEMPLATES / "PB Master Checklist FILLABLE INITIAL.pdf"
        )
        self.assertTrue(
            {
                "p1_job_0",
                "p1_job_1",
                "p1_job_2",
                "p1_job_3",
                "p1_team_0",
            }
            <= set(master_fields)
        )
        self.assertEqual(len(master_fields), len(master_widgets))

        _reader, paint_fields, paint_widgets = form_summary(
            TEMPLATES / "PB Paint and Materials Order Form fillable.pdf"
        )
        self.assertTrue(
            {
                "Project",
                "Builder__Client",
                "Site_Address",
                "Required_Delivery_Date",
                "Ordered_By",
            }
            <= set(paint_fields)
        )
        self.assertEqual(len(paint_fields), len(paint_widgets))

    def test_logo_is_a_valid_square_png(self):
        with Image.open(ROOT / "assets" / "PB_Logo_Main_PNG.png") as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.width, image.height)
            self.assertGreaterEqual(image.width, 300)


if __name__ == "__main__":
    unittest.main()
