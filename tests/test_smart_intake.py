import unittest
import zipfile
from io import BytesIO

from jobhub.smart_intake import (
    build_intake_zip_bytes,
    classify_intake_document,
    extract_intake_pdf_text,
    merge_intake_parts,
    parse_colour_schedule_bytes,
    parse_intake_upload,
    parse_plan_text,
    parse_scope_text,
    parts_to_intake_package,
)


class ClassifyIntakeTests(unittest.TestCase):
    def test_plan_filenames(self):
        self.assertEqual(classify_intake_document("PB1234-Floor-Plan.pdf"), "plan")
        self.assertEqual(classify_intake_document("ARCH drawings.pdf"), "plan")

    def test_scope_filenames(self):
        self.assertEqual(classify_intake_document("Scope of Works.pdf"), "scope")
        self.assertEqual(classify_intake_document("Painting Specification.pdf"), "scope")

    def test_colour_schedule_filenames(self):
        self.assertEqual(classify_intake_document("Colour Schedule.csv"), "colour_schedule")
        self.assertEqual(classify_intake_document("Finish Schedule.xlsx"), "colour_schedule")

    def test_spreadsheets_are_colour_schedules(self):
        self.assertEqual(classify_intake_document("any_table.csv"), "colour_schedule")

    def test_unclassified_pdf_defaults_to_scope(self):
        self.assertEqual(classify_intake_document("PB1234.pdf"), "scope")


class ParsePlanTextTests(unittest.TestCase):
    def test_room_dimensions_build_lines_with_hours_and_litres(self):
        text = "PROJECT: 12 Smith Street\nJOB NUMBER: PB12345\nLOUNGE 5400 X 3200\nKITCHEN 4000 X 3200"
        parts = parse_plan_text(text, "PB12345-Floor-Plan.pdf")
        self.assertEqual(parts["document_type"], "plan")
        self.assertEqual(parts["job_hints"]["job_no"], "PB12345")
        self.assertGreaterEqual(len(parts["lines"]), 4)
        self.assertTrue(all(row["Estimated Labour Hours"] > 0 for row in parts["lines"]))
        self.assertTrue(parts["materials"])
        self.assertTrue(all(row["Qty Required"] > 0 for row in parts["materials"]))
        substrates = {row["Substrate"] for row in parts["lines"]}
        self.assertIn("Internal walls", substrates)
        self.assertIn("Internal ceilings", substrates)

    def test_lounge_room_wall_area_and_hours(self):
        parts = parse_plan_text("LOUNGE 5400 X 3200", "plan.pdf")
        wall = [row for row in parts["lines"] if row["Substrate"] == "Internal walls"][0]
        self.assertAlmostEqual(wall["Qty"], round(17.2 * 2.7, 2), places=2)
        self.assertAlmostEqual(wall["Estimated Labour Hours"], round((17.2 * 2.7 * 2) / 9.0, 2), places=2)


class ParseScopeTextTests(unittest.TestCase):
    SCOPE = (
        "PROJECT: 12 Smith Street\n"
        "JOB NUMBER: PB12345\n"
        "Paint internal walls 120 m2\n"
        "Paint ceilings 40 m2\n"
        "Skirting 60 lm\n"
        "12 doors\n"
        "Apply 3 coats\n"
        "10 litres Dulux Wash and Wear, Colour: Antique White USA\n"
    )

    def test_job_hints_extracted(self):
        parts = parse_scope_text(self.SCOPE, "Scope.pdf")
        self.assertEqual(parts["job_hints"]["job_no"], "PB12345")
        self.assertIn("12 Smith Street", parts["job_hints"]["site_address"])

    def test_area_rows_have_labour_hours(self):
        parts = parse_scope_text(self.SCOPE, "Scope.pdf")
        wall = [row for row in parts["lines"] if row["Unit"] == "m²" and "wall" in row["Item Description"].lower()][0]
        self.assertGreater(wall["Estimated Labour Hours"], 0)
        self.assertAlmostEqual(wall["Qty"], 120.0)

    def test_lineal_and_each_rows(self):
        parts = parse_scope_text(self.SCOPE, "Scope.pdf")
        units = {row["Unit"] for row in parts["lines"]}
        self.assertIn("lm", units)
        self.assertIn("each", units)
        lm = [row for row in parts["lines"] if row["Unit"] == "lm"][0]
        self.assertAlmostEqual(lm["Estimated Labour Hours"], round(60 / 25.0, 2), places=2)

    def test_stated_litres_become_materials(self):
        parts = parse_scope_text(self.SCOPE, "Scope.pdf")
        stated = [row for row in parts["materials"] if row["Product / Material Name"] == "Paint (stated qty)"]
        self.assertTrue(stated)
        self.assertAlmostEqual(stated[0]["Qty Required"], 10.0)

    def test_colour_capture(self):
        parts = parse_scope_text(self.SCOPE, "Scope.pdf")
        colours = [row["Colour / Finish"] for row in parts["colours"]]
        self.assertIn("Antique White USA", colours)


class ParseColourScheduleTests(unittest.TestCase):
    def test_colour_schedule_csv(self):
        csv_bytes = (
            "Room,Surface,Product,Colour,Sheen,Qty,Unit\n"
            "Bedroom 1,Internal Walls,Dulux Wash and Wear,Antique White USA,Low Sheen,4,Litre\n"
            "Bedroom 2,Internal Walls,Dulux Wash and Wear,Antique White USA,Low Sheen,4,Litre\n"
            "Kitchen,Ceilings,Elite Ceiling,White,Ceiling,2,Litre\n"
        ).encode("utf-8")
        parts = parse_colour_schedule_bytes(csv_bytes, "Colour Schedule.csv")
        self.assertEqual(parts["document_type"], "colour_schedule")
        self.assertEqual(len(parts["colours"]), 3)
        materials = parts["materials"]
        self.assertAlmostEqual(sum(row["Qty Required"] for row in materials), 10.0)

    def test_colour_schedule_with_area_creates_labour_lines(self):
        csv_bytes = (
            "Room,Substrate,Qty,Unit,Colour\n"
            "Lounge,Internal walls,20,m2,Antique White USA\n"
        ).encode("utf-8")
        parts = parse_colour_schedule_bytes(csv_bytes, "Schedule.csv")
        self.assertTrue(parts["lines"])
        self.assertGreater(parts["lines"][0]["Estimated Labour Hours"], 0)


class ParseIntakeUploadTests(unittest.TestCase):
    def test_dispatch_colour_schedule(self):
        parts = parse_intake_upload(
            "Room,Colour\nBedroom 1,White\n".encode("utf-8"), "Colour Schedule.csv"
        )
        self.assertEqual(parts["document_type"], "colour_schedule")
        self.assertTrue(parts["raw_bytes"])

    def test_scope_txt(self):
        parts = parse_intake_upload("Paint walls 80 m2".encode("utf-8"), "Scope.txt")
        self.assertEqual(parts["document_type"], "scope")
        self.assertEqual(len(parts["lines"]), 1)

    def test_plan_image_is_rejected_clearly(self):
        with self.assertRaises(ValueError):
            parse_intake_upload(b"not an image", "Floor Plan.png")


class MergeIntakePartsTests(unittest.TestCase):
    def test_matching_lines_are_deduplicated(self):
        plan = parse_plan_text("LOUNGE 5400 X 3200", "plan.pdf")
        scope = parse_scope_text("Paint internal walls 120 m2", "Scope.pdf")
        merged = merge_intake_parts([plan, scope])
        self.assertEqual(merged["merge_summary"]["parts"], 2)
        self.assertLessEqual(merged["merge_summary"]["lines_after"], merged["merge_summary"]["lines_before"])

    def test_materials_are_accumulated(self):
        part_a = parse_scope_text("Paint internal walls 120 m2", "a.pdf")
        part_b = parse_scope_text("Paint internal walls 120 m2", "b.pdf")
        merged = merge_intake_parts([part_a, part_b])
        materials = merged["materials"]
        total = sum(row["Qty Required"] for row in materials)
        self.assertGreater(total, 0)


class BuildIntakeZipTests(unittest.TestCase):
    def test_zip_contains_manifest_and_source_files(self):
        parts = parse_scope_text("PROJECT: 12 Smith\nJOB NUMBER: PB12345\nPaint walls 80 m2", "Scope of Works.pdf")
        merged = merge_intake_parts([parts])
        zip_bytes, member_names = build_intake_zip_bytes(merged)
        with zipfile.ZipFile(BytesIO(zip_bytes), "r") as zf:
            names = zf.namelist()
        self.assertIn("job_manifest.json", names)
        for member in member_names:
            self.assertIn(member, names)


class IntakePackageTests(unittest.TestCase):
    def test_package_matches_import_shape(self):
        parts = parse_scope_text(
            "PROJECT: 12 Smith\nJOB NUMBER: PB12345\nPaint walls 80 m2\nPaint ceilings 30 m2",
            "Scope.pdf",
        )
        package = parts_to_intake_package([parts])
        for key in ["source_bytes", "member_names", "manifest", "summary", "lines",
                    "labour", "materials", "colours", "purchase_orders", "stages", "documents"]:
            self.assertIn(key, package)
        self.assertEqual(package["summary"]["job_no"], "PB12345")
        self.assertGreater(package["summary"]["labour_hours"], 0)
        self.assertEqual(len(package["lines"].columns), 14)
        self.assertEqual(len(package["materials"].columns), 12)
        self.assertEqual(len(package["colours"].columns), 6)
        self.assertEqual(len(package["documents"]), 1)

    def test_merge_across_files_surfaces_in_package(self):
        scope = parse_scope_text("PROJECT: 12 Smith\nJOB NUMBER: PB12345\nPaint walls 80 m2", "Scope.pdf")
        colour = parse_colour_schedule_bytes(
            "Room,Colour\nBedroom 1,Antique White USA\n".encode("utf-8"), "Colour Schedule.csv"
        )
        package = parts_to_intake_package([scope, colour])
        self.assertEqual(len(package["documents"]), 2)
        self.assertEqual(len(package["colours"]), 1)
        self.assertEqual(package["merge_summary"]["parts"], 2)


if __name__ == "__main__":
    unittest.main()
