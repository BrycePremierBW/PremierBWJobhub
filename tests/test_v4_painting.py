from io import BytesIO
import json
import sqlite3
import unittest
import zipfile

from jobhub_v4.handover import build_handover_manifest, build_handover_zip
from jobhub_v4.paint import (
    calculate_paint_quantity,
    colour_order_allowed,
    optimise_pack_mix,
)
from jobhub_v4.revisions import compare_revisions
from jobhub_v4.schema import ensure_v4_schema


class PaintQuantityTests(unittest.TestCase):
    def test_quantity_applies_coats_coverage_and_waste(self):
        result = calculate_paint_quantity(
            area_sqm=120,
            coats=2,
            coverage_sqm_per_litre=12,
            waste_percent=10,
        )
        self.assertEqual(result["base_litres"], 20.0)
        self.assertEqual(result["waste_litres"], 2.0)
        self.assertEqual(result["required_litres"], 22.0)

    def test_invalid_coverage_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Coverage"):
            calculate_paint_quantity(
                area_sqm=100,
                coats=2,
                coverage_sqm_per_litre=0,
            )


class PackOptimisationTests(unittest.TestCase):
    def test_warehouse_stock_is_used_before_purchasing(self):
        result = optimise_pack_mix(
            required_litres=22,
            warehouse_stock={4: 1, 10: 1, 15: 0},
            supplier_prices={4: 60, 10: 130, 15: 180},
        )
        self.assertEqual(result["purchase_cost"], 120.0)
        self.assertEqual(result["supplied_litres"], 22)
        self.assertEqual(result["warehouse_packs"], 2)
        self.assertEqual(result["supplier_packs"], 2)

    def test_cheapest_cost_can_beat_smallest_excess(self):
        result = optimise_pack_mix(
            required_litres=16,
            warehouse_stock={4: 0, 10: 0, 15: 0},
            supplier_prices={4: 100, 10: 100, 15: 90},
        )
        self.assertEqual(result["purchase_cost"], 180.0)
        self.assertEqual(result["supplied_litres"], 30)
        self.assertEqual(result["supplier_packs"], 2)

    def test_pack_plan_always_covers_decimal_requirement(self):
        result = optimise_pack_mix(
            required_litres=20.5,
            warehouse_stock={},
            supplier_prices={4: 45, 10: 100, 15: 135},
        )
        self.assertGreaterEqual(result["supplied_litres"], 20.5)


class ColourGateTests(unittest.TestCase):
    def test_approval_requires_status_approver_and_date(self):
        self.assertFalse(colour_order_allowed("pending")[0])
        self.assertFalse(colour_order_allowed("approved", approved_by="Bryce")[0])
        self.assertTrue(
            colour_order_allowed(
                "approved",
                approved_by="Bryce",
                approved_at="2026-07-27T10:00:00+00:00",
            )[0]
        )


class RevisionTests(unittest.TestCase):
    def test_scope_language_creates_variation_signal(self):
        result = compare_revisions(
            "Walls: two coats low sheen\nDoors excluded",
            "Walls: two coats low sheen\nAdd doors with two coats enamel\nAdditional feature wall",
        )
        self.assertTrue(result["likely_variation"])
        self.assertGreaterEqual(result["variation_risk_score"], 20)
        self.assertIn("Add doors with two coats enamel", result["added_lines"])

    def test_identical_revisions_have_no_change(self):
        result = compare_revisions("A\nB", "A\nB")
        self.assertEqual(result["similarity_percent"], 100.0)
        self.assertFalse(result["likely_variation"])


class HandoverTests(unittest.TestCase):
    def test_manifest_reports_missing_evidence(self):
        manifest = build_handover_manifest(
            job={"id": 1, "job_no": "PB001", "job_name": "Test"},
            evidence=[{"evidence_type": "progress_photo", "status": "active"}],
            colour_approvals=[],
            generated_at="2026-07-27T10:00:00+00:00",
        )
        self.assertFalse(manifest["ready"])
        self.assertIn("Approved colour schedule", manifest["missing_requirements"])

    def test_handover_zip_contains_manifest_and_schedules(self):
        manifest = build_handover_manifest(
            job={"id": 1, "job_no": "PB001", "job_name": "Test"},
            evidence=[{"evidence_type": "progress_photo", "status": "active"}],
            colour_approvals=[],
            generated_at="2026-07-27T10:00:00+00:00",
        )
        archive_bytes = build_handover_zip(manifest)
        with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
            self.assertIn("handover_manifest.json", archive.namelist())
            parsed = json.loads(archive.read("handover_manifest.json"))
            self.assertEqual(parsed["job"]["job_no"], "PB001")


class V4SchemaTests(unittest.TestCase):
    def test_schema_is_restart_safe(self):
        connection = sqlite3.connect(":memory:")

        class SharedConnection:
            def cursor(self):
                return connection.cursor()

            def commit(self):
                return connection.commit()

            def rollback(self):
                return connection.rollback()

            def close(self):
                pass

        ensure_v4_schema(lambda: SharedConnection())
        ensure_v4_schema(lambda: SharedConnection())
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertIn("paint_systems", table_names)
        self.assertIn("colour_approvals", table_names)
        self.assertIn("plan_evidence", table_names)
        self.assertIn("drawing_revisions", table_names)
        self.assertIn("variation_suggestions", table_names)
        self.assertIn("handover_packs", table_names)


if __name__ == "__main__":
    unittest.main()
