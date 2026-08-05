import io
import unittest

import pandas as pd

from jobhub.smart_intake import (
    parts_to_intake_package,
    parse_colour_schedule_bytes,
    parse_plan_text,
    parse_scope_text,
)

INTAKE_CEILING_HEIGHT_M = 2.7
INTAKE_COVERAGE_M2_PER_LITRE = 12.0
INTAKE_DEFAULT_COATS = 2


REAL_PLAN = """PROJECT: DWELLING ALTERATIONS AND ADDITIONS
AT 14 CURRAWONG STREET, NERANG QLD 4211
DRAWING NO. PB4821-A
SCALE 1:100   SHEET 2 OF 6   A3
GROUND FLOOR PLAN
MASTER BEDROOM  5400 x 3200
BED 2  3600 x 3200
BED 3  3400 x 3100
LOUNGE  5.4m x 3.2m
DINING  4000 x 3200
KITCHEN  4200mm x 3000mm
LAUNDRY  2200 x 1800
TOILET  2000 x 1500
DOOR SCHEDULE
DOOR D1  2040 wide x 820 high
"""

COMBINED_PLAN = """PROJECT: DWELLING ALTERATIONS AND ADDITIONS
AT 14 CURRAWONG STREET, NERANG QLD 4211
DRAWING NO. PB4821-A
GROUND FLOOR PLAN
MASTER BEDROOM  5400 x 3200
BED 2  3600 x 3200
LOUNGE  5.4m x 3.2m
DINING  4000 x 3200
KITCHEN  4200mm x 3000mm
LAUNDRY  2200 x 1800
TOILET  2000 x 1500
"""

NOISY_PLAN = """PB9999 LEVEL 1 FLOOR PLAN  SCALE 1:100
RUMPUS ROOM
approx 4,800 x 3,900
STUDY 3.0m x 2.4m
ALFRESCO  5.2 x 3.0
DOOR SCHEDULE  D3  2040 wide x 820 high
"""

REAL_SCOPE = """PROJECT: PAINTING OF RESIDENTIAL DWELLING
AT 14 CURRAWONG STREET, NERANG QLD 4211
JOB NO. PB4821

SCOPE OF WORKS

1. Internal plasterboard walls - approximately 210 m2.
2. Ceilings - paint 85 m2.
3. Skirting and architraves to all rooms - 45 lm.
4. Repaint 8 interior doors.
5. Feature walls - apply 3 coats - 25 m2.
6. External timber cladding - 60 m2.
7. 30 litres Dulux Wash and Wear, colour: Antique White USA
8. 4 litres Dulux Ceiling White
"""

COMBINED_SCOPE = """PROJECT: PAINTING OF RESIDENTIAL DWELLING
AT 14 CURRAWONG STREET, NERANG QLD 4211
JOB NO. PB4821
1. Internal plasterboard walls - approximately 210 m2.
2. Ceilings - paint 85 m2.
3. Skirting and architraves to all rooms - 45 lm.
4. Repaint 8 interior doors.
5. Feature walls - apply 3 coats - 25 m2.
6. External timber cladding - 60 m2.
7. 30 litres Dulux Wash and Wear, colour: Antique White USA
"""

REAL_COLOUR_CSV = """Room,Surface,Product,Colour,Sheen,Qty,Unit
Bedroom 1,Internal walls,Dulux Wash and Wear,Antique White USA,Low Sheen,8,Litre
Bedroom 2,Internal walls,Dulux Wash and Wear,Antique White USA,Low Sheen,8,Litre
Lounge,Internal walls,Dulux Wash and Wear,Antique White USA,Low Sheen,10,Litre
Lounge,Internal ceilings,Dulux Ceiling White,White,Ceiling,4,Litre
Ensuite,Internal walls,Dulux Wash and Wear,Crisp White,Low Sheen,4,Litre
Kitchen,Internal ceilings,Elite Ceiling,White,Ceiling,3,Litre
Hall,Timber doors,Dulux Enamel,Antique White USA,Low Sheen,2,Litre
""".encode("utf-8")


def rows_for(parts, location, substrate):
    return [
        row for row in parts["lines"]
        if row.get("Location") == location and row["Substrate"] == substrate
    ]


class PlanReaderAccuracyTests(unittest.TestCase):
    def test_all_eight_rooms_detected(self):
        parts = parse_plan_text(REAL_PLAN, "PB4821-A Floor Plan.pdf")
        self.assertEqual(parts["document_type"], "plan")
        self.assertEqual(len(parts["lines"]), 16)
        locations = sorted({row["Location"] for row in parts["lines"]})
        self.assertEqual(locations, [
            "Bed 2", "Bed 3", "Dining", "Kitchen", "Laundry",
            "Lounge", "Master Bedroom", "Toilet",
        ])
        self.assertEqual(len(parts["materials"]), 16)

    def test_wall_and_ceiling_quantities_exact(self):
        parts = parse_plan_text(REAL_PLAN, "PB4821-A Floor Plan.pdf")
        master_wall = rows_for(parts, "Master Bedroom", "Internal walls")[0]
        master_ceil = rows_for(parts, "Master Bedroom", "Internal ceilings")[0]
        self.assertAlmostEqual(master_wall["Qty"], 46.44, places=2)
        self.assertAlmostEqual(master_ceil["Qty"], 17.28, places=2)
        kitchen_wall = rows_for(parts, "Kitchen", "Internal walls")[0]
        kitchen_ceil = rows_for(parts, "Kitchen", "Internal ceilings")[0]
        self.assertAlmostEqual(kitchen_wall["Qty"], 38.88, places=2)
        self.assertAlmostEqual(kitchen_ceil["Qty"], 12.60, places=2)

    def test_metric_and_imperial_formats(self):
        parts = parse_plan_text(REAL_PLAN, "PB4821-A Floor Plan.pdf")
        lounge_wall = rows_for(parts, "Lounge", "Internal walls")[0]
        self.assertAlmostEqual(lounge_wall["Qty"], 46.44, places=2)
        toilet_ceil = rows_for(parts, "Toilet", "Internal ceilings")[0]
        self.assertAlmostEqual(toilet_ceil["Qty"], 3.0, places=2)

    def test_hours_match_takeoff_productivity(self):
        parts = parse_plan_text(REAL_PLAN, "PB4821-A Floor Plan.pdf")
        master_wall = rows_for(parts, "Master Bedroom", "Internal walls")[0]
        master_ceil = rows_for(parts, "Master Bedroom", "Internal ceilings")[0]
        self.assertAlmostEqual(master_wall["Estimated Labour Hours"], 46.44 * INTAKE_DEFAULT_COATS / 9.0, places=2)
        self.assertAlmostEqual(master_ceil["Estimated Labour Hours"], 17.28 * INTAKE_DEFAULT_COATS / 8.0, places=2)
        self.assertAlmostEqual(
            sum(row["Estimated Labour Hours"] for row in parts["lines"]), 85.12, places=2
        )

    def test_litres_are_two_coats_at_twelve_m2_per_litre(self):
        parts = parse_plan_text(REAL_PLAN, "PB4821-A Floor Plan.pdf")
        total_m2 = sum(row["Qty"] for row in parts["lines"])
        expected = total_m2 * INTAKE_DEFAULT_COATS / INTAKE_COVERAGE_M2_PER_LITRE
        self.assertAlmostEqual(
            sum(row["Qty Required"] for row in parts["materials"]), expected, places=1
        )

    def test_job_hints(self):
        parts = parse_plan_text(REAL_PLAN, "PB4821-A Floor Plan.pdf")
        self.assertEqual(parts["job_hints"]["job_no"], "PB4821")
        self.assertEqual(parts["job_hints"]["site_address"], "14 CURRAWONG STREET, NERANG QLD 4211")
        self.assertIn("DWELLING ALTERATIONS", parts["job_hints"]["job_name"])

    def test_door_schedule_does_not_pollute_rooms(self):
        parts = parse_plan_text(REAL_PLAN, "PB4821-A Floor Plan.pdf")
        self.assertFalse(any("door" in (row.get("Location") or "").lower() for row in parts["lines"]))
        self.assertFalse(any("schedule" in (row.get("Location") or "").lower() for row in parts["lines"]))

    def test_adjacent_label_and_grouped_numbers(self):
        parts = parse_plan_text(NOISY_PLAN, "PB9999 level 1 plan.pdf")
        self.assertEqual(len(parts["lines"]), 6)
        rumpus_wall = rows_for(parts, "Rumpus Room", "Internal walls")[0]
        rumpus_ceil = rows_for(parts, "Rumpus Room", "Internal ceilings")[0]
        self.assertAlmostEqual(rumpus_wall["Qty"], 46.98, places=2)
        self.assertAlmostEqual(rumpus_ceil["Qty"], 18.72, places=2)
        study_wall = rows_for(parts, "Study", "Internal walls")[0]
        self.assertAlmostEqual(study_wall["Qty"], 29.16, places=2)
        alfresco_wall = rows_for(parts, "Alfresco", "Internal walls")[0]
        self.assertAlmostEqual(alfresco_wall["Qty"], 44.28, places=2)
        self.assertEqual(parts["job_hints"]["job_no"], "PB9999")
        self.assertEqual(parts["job_hints"]["site_address"], "")


class ScopeAccuracyTests(unittest.TestCase):
    def test_substrate_not_leaked_from_following_line(self):
        parts = parse_scope_text(REAL_SCOPE, "Scope of Works.pdf")
        wall = [row for row in parts["lines"] if row["Qty"] == 210.0][0]
        self.assertEqual(wall["Substrate"], "Internal walls")
        ceiling = [row for row in parts["lines"] if row["Qty"] == 85.0][0]
        self.assertEqual(ceiling["Substrate"], "Ceilings / soffits")

    def test_area_rows_exact_hours(self):
        parts = parse_scope_text(REAL_SCOPE, "Scope of Works.pdf")
        by_qty = {row["Qty"]: row for row in parts["lines"] if row["Unit"] == "m²"}
        self.assertAlmostEqual(by_qty[210.0]["Estimated Labour Hours"], 46.67, places=2)
        self.assertAlmostEqual(by_qty[85.0]["Estimated Labour Hours"], 21.25, places=2)
        self.assertAlmostEqual(by_qty[60.0]["Estimated Labour Hours"], 20.0, places=2)
        self.assertEqual(by_qty[60.0]["Substrate"], "External walls")

    def test_three_coats_doubles_hours(self):
        parts = parse_scope_text(REAL_SCOPE, "Scope of Works.pdf")
        feature = [row for row in parts["lines"] if row["Qty"] == 25.0][0]
        self.assertAlmostEqual(feature["Estimated Labour Hours"], 25.0 * 3 / 9.0, places=2)

    def test_lineal_skirting_hours(self):
        parts = parse_scope_text(REAL_SCOPE, "Scope of Works.pdf")
        lm_rows = [row for row in parts["lines"] if row["Unit"] == "lm"]
        self.assertEqual(len(lm_rows), 1)
        self.assertAlmostEqual(lm_rows[0]["Qty"], 45.0, places=2)
        self.assertAlmostEqual(lm_rows[0]["Estimated Labour Hours"], 1.8, places=2)

    def test_interior_doors_counted_with_adjective(self):
        parts = parse_scope_text(REAL_SCOPE, "Scope of Works.pdf")
        door_rows = [row for row in parts["lines"] if row["Unit"] == "each"]
        self.assertEqual(len(door_rows), 1)
        self.assertAlmostEqual(door_rows[0]["Qty"], 8.0, places=2)
        self.assertEqual(door_rows[0]["Substrate"], "Timber doors")
        self.assertAlmostEqual(door_rows[0]["Estimated Labour Hours"], 6.0, places=2)
        door_material = [
            row for row in parts["materials"]
            if row["Product / Material Name"] == "Paint (calculated - Timber doors)"
        ]
        self.assertAlmostEqual(door_material[0]["Qty Required"], 4.0, places=2)

    def test_all_coat_litres_survive_to_materials(self):
        parts = parse_scope_text(REAL_SCOPE, "Scope of Works.pdf")
        wall_material = [
            row for row in parts["materials"]
            if row["Product / Material Name"] == "Paint (calculated - Internal walls)"
        ]
        self.assertAlmostEqual(wall_material[0]["Qty Required"], 35.0 + 25.0 * 3 / 12.0, places=2)

    def test_stated_litres_and_colour(self):
        parts = parse_scope_text(REAL_SCOPE, "Scope of Works.pdf")
        stated = [row for row in parts["materials"] if row["Product / Material Name"] == "Paint (stated qty)"]
        self.assertEqual(len(stated), 2)
        by_qty = {row["Qty Required"]: row for row in stated}
        self.assertAlmostEqual(by_qty[30.0]["Colour / Finish"], "Antique White USA")
        self.assertIn("Antique White USA", [row["Colour / Finish"] for row in parts["colours"]])

    def test_total_hours(self):
        parts = parse_scope_text(REAL_SCOPE, "Scope of Works.pdf")
        self.assertAlmostEqual(
            sum(row["Estimated Labour Hours"] for row in parts["lines"]), 104.05, places=2
        )

    def test_job_hints(self):
        parts = parse_scope_text(REAL_SCOPE, "Scope of Works.pdf")
        self.assertEqual(parts["job_hints"]["job_no"], "PB4821")
        self.assertEqual(parts["job_hints"]["site_address"], "14 CURRAWONG STREET, NERANG QLD 4211")
        self.assertIn("PAINTING OF RESIDENTIAL DWELLING", parts["job_hints"]["job_name"])


class ColourScheduleAccuracyTests(unittest.TestCase):
    def test_csv_materials_and_colours(self):
        parts = parse_colour_schedule_bytes(REAL_COLOUR_CSV, "Colour Schedule.csv")
        self.assertEqual(parts["document_type"], "colour_schedule")
        self.assertEqual(len(parts["colours"]), 7)
        self.assertAlmostEqual(sum(row["Qty Required"] for row in parts["materials"]), 39.0, places=2)

    def test_area_rows_in_schedule_estimate_labour(self):
        csv_bytes = (
            "Room,Substrate,Qty,Unit,Colour\n"
            "Lounge,Internal walls,20,m2,Antique White USA\n"
        ).encode("utf-8")
        parts = parse_colour_schedule_bytes(csv_bytes, "Schedule.csv")
        self.assertEqual(len(parts["lines"]), 1)
        row = parts["lines"][0]
        self.assertEqual(row["Unit"], "m²")
        self.assertAlmostEqual(row["Qty"], 20.0, places=2)
        self.assertAlmostEqual(row["Estimated Labour Hours"], 20.0 * 2 / 9.0, places=2)
        calculated = [
            m for m in parts["materials"]
            if m["Product / Material Name"] == "Paint (calculated - Internal walls)"
        ]
        self.assertAlmostEqual(calculated[0]["Qty Required"], 20.0 * 2 / 12.0, places=2)

    def test_xlsx_colour_schedule(self):
        df = pd.DataFrame([
            ["Bedroom 1", "Internal walls", "Dulux Wash and Wear", "Antique White USA", "Low Sheen", 8, "Litre"],
            ["Lounge", "Internal ceilings", "Dulux Ceiling White", "White", "Ceiling", 4, "Litre"],
            ["Lounge", "Internal walls", "Dulux Wash and Wear", "Antique White USA", "Low Sheen", 20, "m2"],
        ], columns=["Room", "Surface", "Product", "Colour", "Sheen", "Qty", "Unit"])
        buffer = io.BytesIO()
        df.to_excel(buffer, index=False)
        parts = parse_colour_schedule_bytes(buffer.getvalue(), "Finish Schedule.xlsx")
        self.assertEqual(parts["document_type"], "colour_schedule")
        self.assertEqual(len(parts["colours"]), 3)
        self.assertEqual(len(parts["lines"]), 1)
        self.assertAlmostEqual(parts["lines"][0]["Qty"], 20.0, places=2)
        self.assertAlmostEqual(
            sum(row["Qty Required"] for row in parts["materials"]), 35.33, places=2
        )

    def test_empty_sheet_raises(self):
        empty = pd.DataFrame()
        buffer = io.BytesIO()
        empty.to_excel(buffer, index=False)
        with self.assertRaises(ValueError) as ctx:
            parse_colour_schedule_bytes(buffer.getvalue(), "Schedule.xlsx")
        self.assertIn("empty", str(ctx.exception).lower())


class IntakePipelineAccuracyTests(unittest.TestCase):
    def test_plan_scope_schedule_merge(self):
        plan = parse_plan_text(COMBINED_PLAN, "PB4821-A Floor Plan.pdf")
        scope = parse_scope_text(COMBINED_SCOPE, "Scope of Works.pdf")
        colour = parse_colour_schedule_bytes(REAL_COLOUR_CSV, "Colour Schedule.csv")
        package = parts_to_intake_package([plan, scope, colour])

        self.assertEqual(package["summary"]["job_no"], "PB4821")
        self.assertEqual(package["summary"]["site_address"], "14 CURRAWONG STREET, NERANG QLD 4211")
        self.assertAlmostEqual(package["summary"]["labour_hours"], 178.74, places=2)
        self.assertEqual(len(package["documents"]), 3)
        self.assertEqual(package["merge_summary"]["parts"], 3)
        self.assertEqual(len(package["lines"]), 20)
        self.assertEqual(len(package["materials"]), 26)

    def test_labour_total_is_internally_consistent(self):
        plan = parse_plan_text(COMBINED_PLAN, "PB4821-A Floor Plan.pdf")
        scope = parse_scope_text(COMBINED_SCOPE, "Scope of Works.pdf")
        colour = parse_colour_schedule_bytes(REAL_COLOUR_CSV, "Colour Schedule.csv")
        package = parts_to_intake_package([plan, scope, colour])
        expected = sum(part["Estimated Labour Hours"] for part in (
            list(plan["lines"]) + list(scope["lines"]) + list(colour["lines"])
        ))
        self.assertAlmostEqual(package["summary"]["labour_hours"], expected, places=2)


if __name__ == "__main__":
    unittest.main()
