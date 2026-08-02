import unittest

from jobhub_production import (
    actual_production_rate,
    budget_production_allowance,
    claimable_value,
    crew_duration_days,
    expected_progress,
    line_production_metrics,
    measured_progress,
    overhead_recovery_metrics,
    production_sell_pricing,
    production_variance,
    validate_production_targets,
)


class ProductionTargetTests(unittest.TestCase):
    def test_m2_rate_converts_daily_value_to_required_quantity(self):
        metrics = line_production_metrics(quantity=500, unit_rate=45, unit="m²")

        self.assertAlmostEqual(metrics["units_per_day_low"], 1000 / 45)
        self.assertAlmostEqual(metrics["units_per_day_target"], 1000 / 45)
        self.assertAlmostEqual(metrics["units_per_day_high"], 1000 / 45)
        self.assertAlmostEqual(metrics["labour_hours_at_target"], 180.0)

    def test_lineal_metres_use_the_same_value_model(self):
        metrics = line_production_metrics(
            quantity=250,
            unit_rate=20,
            line_total=5000,
            unit="lineal m",
        )

        self.assertAlmostEqual(metrics["units_per_day_target"], 50.0)
        self.assertAlmostEqual(metrics["painter_days_at_target"], 5.0)

    def test_timesheet_hours_return_expected_completion(self):
        progress = expected_progress(actual_hours=140, budget_hours=800)

        self.assertAlmostEqual(progress["expected_percent"], 17.5)
        self.assertAlmostEqual(progress["remaining_hours"], 660.0)
        self.assertEqual(progress["hours_over_budget"], 0.0)

    def test_progress_reports_hours_over_target(self):
        progress = expected_progress(actual_hours=90, budget_hours=80)

        self.assertEqual(progress["expected_percent"], 100.0)
        self.assertAlmostEqual(progress["raw_expected_percent"], 112.5)
        self.assertEqual(progress["hours_over_budget"], 10.0)

    def test_crew_size_converts_painter_hours_to_job_days(self):
        self.assertAlmostEqual(crew_duration_days(800, crew_size=4), 25.0)

    def test_budget_cross_check_deducts_non_painter_allowances(self):
        result = budget_production_allowance(
            contract_value=18000,
            material_allowance=3000,
            sundries_allowance=500,
            access_allowance=500,
            subcontractor_allowance=1000,
            measured_quantity=200,
            measurement_unit="floor m²",
        )
        self.assertEqual(result["painter_production_value"], 13000.0)
        self.assertEqual(result["allowed_painter_hours"], 104.0)
        self.assertEqual(result["net_sell_value_per_unit"], 65.0)
        self.assertAlmostEqual(result["target_units_per_day"], 1000 / 65)
        self.assertEqual(result["planning_labour_cost"], 6240.0)

    def test_invalid_value_order_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "ordered low, target, then high"):
            validate_production_targets(value_low=900, value_target=800, value_high=1000)

    def test_overhead_recovery_and_profit_bridge(self):
        result = overhead_recovery_metrics(
            monthly_overhead=19700,
            painter_count=12,
            paid_hours_per_week=38,
            productive_utilisation_percent=80,
            production_value_target=1000,
            planning_hourly_rate=60,
        )

        self.assertAlmostEqual(result["paid_hour_overhead_recovery"], 9.9696356275)
        self.assertAlmostEqual(result["productive_hour_overhead_recovery"], 12.4620445344)
        self.assertEqual(result["recommended_overhead_recovery"], 12.5)
        self.assertAlmostEqual(result["profit_per_hour_paid_basis"], 55.0303643725)
        self.assertAlmostEqual(result["profit_per_day_paid_basis"], 440.24291498)
        self.assertAlmostEqual(result["profit_margin_paid_basis"], 44.024291498)
        self.assertAlmostEqual(result["profit_per_hour_recommended"], 52.5)

    def test_measured_progress_is_capped_but_keeps_raw_result(self):
        result = measured_progress(110, 100)

        self.assertEqual(result["actual_percent"], 100.0)
        self.assertAlmostEqual(result["raw_actual_percent"], 110.0)
        self.assertEqual(result["remaining_quantity"], 0.0)

    def test_production_variance_flags_critical_shortfall(self):
        result = production_variance(actual_percent=45, expected_percent=70)

        self.assertEqual(result["status"], "Critical")
        self.assertEqual(result["variance_points"], -25.0)

    def test_claimable_value_deducts_previous_claims(self):
        result = claimable_value(stage_value=25000, progress_percent=60, previously_claimed=10000)

        self.assertEqual(result["earned_value"], 15000.0)
        self.assertEqual(result["claimable_value"], 5000.0)

    def test_actual_rate_uses_painter_days(self):
        result = actual_production_rate(120, crew_hours=24)

        self.assertEqual(result["painter_days"], 3.0)
        self.assertEqual(result["units_per_painter_day"], 40.0)

    def test_profit_inclusive_target_needs_no_margin_percentage(self):
        result = production_sell_pricing(
            line_total=9000,
            labour_hours=80,
            material_allowance=1000,
            gst_percent=10,
        )

        self.assertEqual(result["work_sell_value"], 10000.0)
        self.assertEqual(result["margin_amount"], 0.0)
        self.assertEqual(result["total_ex_gst"], 11000.0)
        self.assertEqual(result["total_inc_gst"], 12100.0)

    def test_legacy_contingency_input_is_ignored(self):
        result = production_sell_pricing(
            line_total=10000,
            labour_hours=0,
            contingency_percent=25,
            gst_percent=10,
        )

        self.assertEqual(result["contingency_amount"], 0.0)
        self.assertEqual(result["total_ex_gst"], 10000.0)


if __name__ == "__main__":
    unittest.main()
