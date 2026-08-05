import unittest
from pathlib import Path

from jobhub_production import (
    estimate_margin_metrics,
    production_sell_pricing,
)

ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = (ROOT / "pb_jobhub_app.py").read_text(encoding="utf-8")
DB_SOURCE = (ROOT / "jobhub" / "database.py").read_text(encoding="utf-8")


class EstimateMarginMetricsTests(unittest.TestCase):
    def test_hours_driven_margin_uses_default_labour_cost(self):
        result = estimate_margin_metrics(
            line_total=0,
            labour_hours=100,
            material_allowance=1000,
            material_markup_percent=10,
        )

        self.assertEqual(result["work_sell_value"], 12500.0)
        self.assertEqual(result["materials_sell_value"], 1100.0)
        self.assertEqual(result["total_sell"], 13600.0)
        self.assertEqual(result["labour_cost"], 6000.0)
        self.assertEqual(result["total_cost"], 7000.0)
        self.assertEqual(result["gross_profit"], 6600.0)
        self.assertAlmostEqual(result["margin_percent"], 48.53, places=2)
        self.assertEqual(result["margin_status"], "Strong")

    def test_material_markup_raises_margin(self):
        low = estimate_margin_metrics(
            line_total=0, labour_hours=100, material_allowance=1000, material_markup_percent=0
        )
        high = estimate_margin_metrics(
            line_total=0, labour_hours=100, material_allowance=1000, material_markup_percent=25
        )

        self.assertGreater(high["margin_percent"], low["margin_percent"])
        self.assertAlmostEqual(high["materials_sell_value"], 1250.0, places=2)
        self.assertAlmostEqual(high["total_sell"], 13750.0, places=2)
        self.assertAlmostEqual(high["gross_profit"], 6750.0, places=2)

    def test_labour_cost_lowers_margin(self):
        result = estimate_margin_metrics(
            line_total=0,
            labour_hours=100,
            labour_cost_per_hour=95,
            material_allowance=1000,
            material_markup_percent=0,
        )

        self.assertEqual(result["labour_cost"], 9500.0)
        self.assertAlmostEqual(result["margin_percent"], 22.22, places=2)
        self.assertEqual(result["margin_status"], "Low")

    def test_takeoff_line_total_sets_sell_value(self):
        result = estimate_margin_metrics(
            line_total=20000,
            labour_hours=100,
            material_allowance=1000,
            material_markup_percent=0,
        )

        self.assertEqual(result["labour_sell_value"], 12500.0)
        self.assertEqual(result["work_sell_value"], 20000.0)
        self.assertAlmostEqual(result["margin_percent"], 66.67, places=2)

    def test_margin_status_thresholds(self):
        strong = estimate_margin_metrics(
            line_total=0, labour_hours=100, material_allowance=1000, material_markup_percent=0
        )
        acceptable = estimate_margin_metrics(
            line_total=0,
            labour_hours=100,
            labour_cost_per_hour=75,
            material_allowance=1000,
            material_markup_percent=0,
        )

        self.assertEqual(strong["margin_status"], "Strong")
        self.assertEqual(acceptable["margin_status"], "Acceptable")
        self.assertAlmostEqual(acceptable["margin_percent"], 37.04, places=2)

    def test_zero_sell_returns_zero_margin(self):
        result = estimate_margin_metrics(
            line_total=0, labour_hours=0, material_allowance=0, material_markup_percent=10
        )

        self.assertEqual(result["total_sell"], 0.0)
        self.assertEqual(result["margin_percent"], 0.0)
        self.assertEqual(result["margin_status"], "Low")

    def test_negative_inputs_raise_value_error(self):
        with self.assertRaises(ValueError):
            estimate_margin_metrics(line_total=0, labour_hours=100, labour_cost_per_hour=-1)
        with self.assertRaises(ValueError):
            estimate_margin_metrics(line_total=0, labour_hours=100, material_markup_percent=-5)

    def test_allowances_pass_through_at_cost(self):
        result = estimate_margin_metrics(
            line_total=0,
            labour_hours=100,
            material_allowance=1000,
            material_markup_percent=0,
            access_equipment_allowance=500,
            subcontractor_allowance=800,
            sundries_allowance=200,
        )

        self.assertEqual(result["pass_through_sell_value"], 1500.0)
        self.assertEqual(result["total_sell"], 15000.0)
        self.assertEqual(result["total_cost"], 8500.0)


class EstimateMarkupPricingTests(unittest.TestCase):
    def test_markup_prices_materials_to_client(self):
        result = production_sell_pricing(
            line_total=9000,
            labour_hours=80,
            material_allowance=1000,
            material_markup_percent=10,
            gst_percent=10,
        )

        self.assertEqual(result["work_sell_value"], 10000.0)
        self.assertEqual(result["materials_sell_value"], 1100.0)
        self.assertEqual(result["allowances_total"], 1100.0)
        self.assertEqual(result["total_ex_gst"], 11100.0)
        self.assertEqual(result["gst_amount"], 1110.0)
        self.assertEqual(result["total_inc_gst"], 12210.0)

    def test_default_markup_keeps_pass_through_behaviour(self):
        result = production_sell_pricing(
            line_total=9000,
            labour_hours=80,
            material_allowance=1000,
            gst_percent=10,
        )

        self.assertEqual(result["materials_sell_value"], 1000.0)
        self.assertEqual(result["total_ex_gst"], 11000.0)
        self.assertEqual(result["total_inc_gst"], 12100.0)


class EstimatePricingSourceGuardTests(unittest.TestCase):
    def test_margin_metrics_imported_into_app(self):
        self.assertIn("estimate_margin_metrics", APP_SOURCE)

    def test_live_margin_banner_markers(self):
        for marker in (
            "Live Gross-Profit Margin",
            "Projected Gross-Profit Margin",
            "margin_status",
            "estimate_margin_metrics(",
        ):
            self.assertIn(marker, APP_SOURCE)

    def test_pricing_config_fields_rendered(self):
        for marker in (
            "labour_cost_per_hour",
            "material_markup_percent",
            "floor_area_base_rate",
            "ceiling_surcharge_2700",
            "ceiling_surcharge_3000",
        ):
            self.assertIn(marker, APP_SOURCE)

    def test_inline_line_rate_editing(self):
        for marker in ("data_editor", "Save Rate Edits", "Quick add internal floor area"):
            self.assertIn(marker, APP_SOURCE)

    def test_rate_register_markers(self):
        for marker in (
            "estimate_rate_register",
            "snapshot_estimate_rate_register",
            "estimate_rate_register_dataframe",
            "Rate Register",
        ):
            self.assertIn(marker, APP_SOURCE)

    def test_pricing_columns_in_both_schema_sources(self):
        for column in (
            "labour_cost_per_hour",
            "material_markup_percent",
            "floor_area_base_rate",
            "ceiling_surcharge_2700",
            "ceiling_surcharge_3000",
        ):
            self.assertIn(f'ensure_column("estimate_working_sheets", "{column}"', APP_SOURCE)
            self.assertIn(f'ensure_column("estimate_working_sheets", "{column}"', DB_SOURCE)

    def test_rate_register_table_in_both_schema_sources(self):
        for source in (APP_SOURCE, DB_SOURCE):
            self.assertIn("CREATE TABLE IF NOT EXISTS estimate_rate_register", source)


if __name__ == "__main__":
    unittest.main()
