"""Production-target calculations for Premier Brushworks estimates and progress.

The operating target is expressed as completed sell value per painter-day.  A
measured line's unit rate converts that value target back into the m², lineal
metres, items or other units that should be completed in an eight-hour day.
"""

from __future__ import annotations

import math
from typing import Any


DEFAULT_DAY_HOURS = 8.0
DEFAULT_VALUE_LOW = 1000.0
DEFAULT_VALUE_TARGET = 1000.0
DEFAULT_VALUE_HIGH = 1000.0


def _non_negative_number(value: Any, name: str) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number.") from None
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be a finite number of zero or more.")
    return number


def validate_production_targets(
    *,
    day_hours: Any = DEFAULT_DAY_HOURS,
    value_low: Any = DEFAULT_VALUE_LOW,
    value_target: Any = DEFAULT_VALUE_TARGET,
    value_high: Any = DEFAULT_VALUE_HIGH,
) -> dict[str, float]:
    """Validate and normalise the painter-day target settings."""
    hours = _non_negative_number(day_hours, "Day hours")
    low = _non_negative_number(value_low, "Low production value")
    target = _non_negative_number(value_target, "Target production value")
    high = _non_negative_number(value_high, "High production value")
    if hours <= 0 or low <= 0:
        raise ValueError("Day hours and production values must be greater than zero.")
    if not low <= target <= high:
        raise ValueError("Production values must be ordered low, target, then high.")
    return {
        "day_hours": hours,
        "value_low": low,
        "value_target": target,
        "value_high": high,
    }


def line_production_metrics(
    *,
    quantity: Any,
    unit_rate: Any,
    line_total: Any | None = None,
    unit: str = "item",
    day_hours: Any = DEFAULT_DAY_HOURS,
    value_low: Any = DEFAULT_VALUE_LOW,
    value_target: Any = DEFAULT_VALUE_TARGET,
    value_high: Any = DEFAULT_VALUE_HIGH,
) -> dict[str, float | str]:
    """Return units per painter-day plus duration and labour-hour targets."""
    settings = validate_production_targets(
        day_hours=day_hours,
        value_low=value_low,
        value_target=value_target,
        value_high=value_high,
    )
    qty = _non_negative_number(quantity, "Quantity")
    rate = _non_negative_number(unit_rate, "Unit rate")
    supplied_total = _non_negative_number(line_total, "Line total") if line_total is not None else 0.0
    work_value = supplied_total if supplied_total > 0 else qty * rate

    def units_per_day(value: float) -> float:
        return value / rate if rate > 0 else 0.0

    def painter_days(value: float) -> float:
        return work_value / value if work_value > 0 else 0.0

    return {
        "unit": str(unit or "item").strip() or "item",
        "quantity": qty,
        "unit_rate": rate,
        "work_value": work_value,
        "units_per_day_low": units_per_day(settings["value_low"]),
        "units_per_day_target": units_per_day(settings["value_target"]),
        "units_per_day_high": units_per_day(settings["value_high"]),
        "painter_days_at_low": painter_days(settings["value_low"]),
        "painter_days_at_target": painter_days(settings["value_target"]),
        "painter_days_at_high": painter_days(settings["value_high"]),
        "labour_hours_at_low": painter_days(settings["value_low"]) * settings["day_hours"],
        "labour_hours_at_target": painter_days(settings["value_target"]) * settings["day_hours"],
        "labour_hours_at_high": painter_days(settings["value_high"]) * settings["day_hours"],
    }


def expected_progress(actual_hours: Any, budget_hours: Any) -> dict[str, float]:
    """Return the completion percentage that used labour hours should have earned."""
    actual = _non_negative_number(actual_hours, "Actual hours")
    budget = _non_negative_number(budget_hours, "Budget hours")
    raw_percent = actual / budget * 100.0 if budget > 0 else 0.0
    return {
        "actual_hours": actual,
        "budget_hours": budget,
        "remaining_hours": max(0.0, budget - actual),
        "expected_percent": min(100.0, raw_percent),
        "raw_expected_percent": raw_percent,
        "hours_over_budget": max(0.0, actual - budget),
    }


def crew_duration_days(
    budget_hours: Any,
    crew_size: Any,
    day_hours: Any = DEFAULT_DAY_HOURS,
) -> float:
    """Convert total painter-hours into working days for a selected crew size."""
    budget = _non_negative_number(budget_hours, "Budget hours")
    crew = _non_negative_number(crew_size, "Crew size")
    hours = _non_negative_number(day_hours, "Day hours")
    if crew <= 0 or hours <= 0:
        raise ValueError("Crew size and day hours must be greater than zero.")
    return budget / (crew * hours)


def budget_production_allowance(
    *,
    contract_value: Any,
    material_allowance: Any = 0,
    sundries_allowance: Any = 0,
    access_allowance: Any = 0,
    subcontractor_allowance: Any = 0,
    measured_quantity: Any = 0,
    measurement_unit: str = "m²",
    day_hours: Any = DEFAULT_DAY_HOURS,
    value_target: Any = DEFAULT_VALUE_TARGET,
    planning_hourly_rate: Any = 60,
) -> dict[str, float | str]:
    """Cross-check a locked contract against the profit-inclusive production target."""
    settings = validate_production_targets(
        day_hours=day_hours,
        value_low=value_target,
        value_target=value_target,
        value_high=value_target,
    )
    contract = _non_negative_number(contract_value, "Contract value")
    materials = _non_negative_number(material_allowance, "Material allowance")
    sundries = _non_negative_number(sundries_allowance, "Sundries allowance")
    access = _non_negative_number(access_allowance, "Access allowance")
    subcontractors = _non_negative_number(
        subcontractor_allowance, "Subcontractor allowance"
    )
    quantity = _non_negative_number(measured_quantity, "Measured quantity")
    planning_rate = _non_negative_number(planning_hourly_rate, "Planning hourly rate")
    allowances = materials + sundries + access + subcontractors
    painter_value = max(0.0, contract - allowances)
    painter_days = painter_value / settings["value_target"] if painter_value else 0.0
    allowed_hours = painter_days * settings["day_hours"]
    net_rate = painter_value / quantity if quantity else 0.0
    return {
        "contract_value": contract,
        "non_painter_allowances": allowances,
        "painter_production_value": painter_value,
        "allowed_painter_days": painter_days,
        "allowed_painter_hours": allowed_hours,
        "planning_labour_cost": allowed_hours * planning_rate,
        "measured_quantity": quantity,
        "measurement_unit": str(measurement_unit or "m²"),
        "net_sell_value_per_unit": net_rate,
        "target_units_per_day": settings["value_target"] / net_rate if net_rate > 0 else 0.0,
        "target_units_per_hour": (
            settings["value_target"] / net_rate / settings["day_hours"]
            if net_rate > 0 else 0.0
        ),
    }


def remaining_contract_labour(
    *,
    contract_value: Any,
    actual_labour_hours: Any = 0,
    material_commitment: Any = 0,
    sundries_allowance: Any = 0,
    access_allowance: Any = 0,
    subcontractor_allowance: Any = 0,
    day_hours: Any = DEFAULT_DAY_HOURS,
    value_target: Any = DEFAULT_VALUE_TARGET,
) -> dict[str, float]:
    """Calculate labour hours left from the live contract position.

    Premier Brushworks' $1,000 painter-day target already includes profit. The
    contract value remaining after known non-labour commitments is therefore
    converted at $125 of completed work per painter-hour. Timesheet hours used
    are then deducted from that allowance.
    """
    settings = validate_production_targets(
        day_hours=day_hours,
        value_low=value_target,
        value_target=value_target,
        value_high=value_target,
    )
    contract = _non_negative_number(contract_value, "Contract value")
    actual_hours = _non_negative_number(actual_labour_hours, "Actual labour hours")
    materials = _non_negative_number(material_commitment, "Material commitment")
    sundries = _non_negative_number(sundries_allowance, "Sundries allowance")
    access = _non_negative_number(access_allowance, "Access allowance")
    subcontractors = _non_negative_number(
        subcontractor_allowance, "Subcontractor allowance"
    )
    value_per_hour = settings["value_target"] / settings["day_hours"]
    non_labour_commitment = materials + sundries + access + subcontractors
    labour_work_value = max(0.0, contract - non_labour_commitment)
    allowed_hours = labour_work_value / value_per_hour
    used_work_value = actual_hours * value_per_hour
    remaining_work_value = max(0.0, labour_work_value - used_work_value)
    return {
        "contract_value": contract,
        "material_commitment": materials,
        "non_labour_commitment": non_labour_commitment,
        "labour_work_value": labour_work_value,
        "production_value_per_hour": value_per_hour,
        "allowed_labour_hours": allowed_hours,
        "actual_labour_hours": actual_hours,
        "used_labour_work_value": used_work_value,
        "remaining_labour_work_value": remaining_work_value,
        "remaining_labour_hours": remaining_work_value / value_per_hour,
        "hours_over_allowance": max(0.0, actual_hours - allowed_hours),
    }


def overhead_recovery_metrics(
    *,
    monthly_overhead: Any,
    painter_count: Any,
    paid_hours_per_week: Any,
    productive_utilisation_percent: Any = 100,
    production_value_target: Any = DEFAULT_VALUE_TARGET,
    day_hours: Any = DEFAULT_DAY_HOURS,
    planning_hourly_rate: Any = 60,
) -> dict[str, float]:
    """Return overhead recovery and the resulting production profit bridge."""
    overhead = _non_negative_number(monthly_overhead, "Monthly overhead")
    painters = _non_negative_number(painter_count, "Painter count")
    weekly_hours = _non_negative_number(paid_hours_per_week, "Paid hours per week")
    utilisation = _non_negative_number(
        productive_utilisation_percent, "Productive utilisation"
    )
    target = _non_negative_number(production_value_target, "Production value target")
    hours_per_day = _non_negative_number(day_hours, "Day hours")
    planning_rate = _non_negative_number(planning_hourly_rate, "Planning hourly rate")
    if painters <= 0 or weekly_hours <= 0 or hours_per_day <= 0:
        raise ValueError("Painters, paid weekly hours and day hours must be greater than zero.")
    if utilisation <= 0 or utilisation > 100:
        raise ValueError("Productive utilisation must be greater than zero and at most 100%.")

    paid_hours_per_month = painters * weekly_hours * 52.0 / 12.0
    productive_hours_per_month = paid_hours_per_month * utilisation / 100.0
    paid_hour_recovery = overhead / paid_hours_per_month
    productive_hour_recovery = overhead / productive_hours_per_month
    recommended_recovery = math.ceil(productive_hour_recovery * 2.0) / 2.0
    production_value_per_hour = target / hours_per_day
    profit_after_paid_hour_overhead = (
        production_value_per_hour - planning_rate - paid_hour_recovery
    )
    profit_after_productive_hour_overhead = (
        production_value_per_hour - planning_rate - productive_hour_recovery
    )
    profit_after_recommended_overhead = (
        production_value_per_hour - planning_rate - recommended_recovery
    )

    def margin(profit: float) -> float:
        return profit / production_value_per_hour * 100.0 if production_value_per_hour else 0.0

    return {
        "monthly_overhead": overhead,
        "paid_hours_per_month": paid_hours_per_month,
        "productive_hours_per_month": productive_hours_per_month,
        "paid_hour_overhead_recovery": paid_hour_recovery,
        "productive_hour_overhead_recovery": productive_hour_recovery,
        "recommended_overhead_recovery": recommended_recovery,
        "production_value_per_hour": production_value_per_hour,
        "planning_hourly_rate": planning_rate,
        "profit_per_hour_paid_basis": profit_after_paid_hour_overhead,
        "profit_per_day_paid_basis": profit_after_paid_hour_overhead * hours_per_day,
        "profit_margin_paid_basis": margin(profit_after_paid_hour_overhead),
        "profit_per_hour_productive_basis": profit_after_productive_hour_overhead,
        "profit_per_day_productive_basis": profit_after_productive_hour_overhead * hours_per_day,
        "profit_margin_productive_basis": margin(profit_after_productive_hour_overhead),
        "profit_per_hour_recommended": profit_after_recommended_overhead,
        "profit_per_day_recommended": profit_after_recommended_overhead * hours_per_day,
        "profit_margin_recommended": margin(profit_after_recommended_overhead),
    }


def measured_progress(completed_quantity: Any, target_quantity: Any) -> dict[str, float]:
    """Compare cumulative measured quantity with the locked target quantity."""
    completed = _non_negative_number(completed_quantity, "Completed quantity")
    target = _non_negative_number(target_quantity, "Target quantity")
    raw_percent = completed / target * 100.0 if target > 0 else 0.0
    return {
        "completed_quantity": completed,
        "target_quantity": target,
        "remaining_quantity": max(0.0, target - completed),
        "actual_percent": min(100.0, raw_percent),
        "raw_actual_percent": raw_percent,
    }


def production_variance(
    actual_percent: Any,
    expected_percent: Any,
    *,
    warning_points: Any = 10,
    critical_points: Any = 20,
) -> dict[str, float | str]:
    """Classify measured progress against the progress earned by used hours."""
    actual = _non_negative_number(actual_percent, "Actual progress")
    expected = _non_negative_number(expected_percent, "Expected progress")
    warning = _non_negative_number(warning_points, "Warning points")
    critical = _non_negative_number(critical_points, "Critical points")
    if critical < warning:
        raise ValueError("Critical variance must be at least the warning variance.")
    variance = actual - expected
    behind = max(0.0, -variance)
    if behind >= critical:
        status = "Critical"
    elif behind >= warning:
        status = "Behind"
    elif variance > warning:
        status = "Ahead"
    else:
        status = "On track"
    return {
        "actual_percent": actual,
        "expected_percent": expected,
        "variance_points": variance,
        "behind_points": behind,
        "status": status,
    }


def claimable_value(
    stage_value: Any,
    progress_percent: Any,
    previously_claimed: Any = 0,
) -> dict[str, float]:
    """Calculate earned and currently claimable stage value without overclaiming."""
    value = _non_negative_number(stage_value, "Stage value")
    progress = _non_negative_number(progress_percent, "Progress percent")
    prior = _non_negative_number(previously_claimed, "Previously claimed")
    capped_progress = min(100.0, progress)
    earned = value * capped_progress / 100.0
    claimable = max(0.0, earned - prior)
    return {
        "stage_value": value,
        "progress_percent": capped_progress,
        "earned_value": earned,
        "previously_claimed": prior,
        "claimable_value": claimable,
        "unearned_value": max(0.0, value - earned),
    }


def actual_production_rate(
    completed_quantity: Any,
    crew_hours: Any,
    day_hours: Any = DEFAULT_DAY_HOURS,
) -> dict[str, float]:
    """Return actual units completed per painter-day from field update data."""
    quantity = _non_negative_number(completed_quantity, "Completed quantity")
    hours = _non_negative_number(crew_hours, "Crew hours")
    day = _non_negative_number(day_hours, "Day hours")
    if day <= 0:
        raise ValueError("Day hours must be greater than zero.")
    painter_days = hours / day
    return {
        "completed_quantity": quantity,
        "crew_hours": hours,
        "painter_days": painter_days,
        "units_per_painter_day": quantity / painter_days if painter_days > 0 else 0.0,
    }


def production_sell_pricing(
    *,
    line_total: Any,
    labour_hours: Any,
    material_allowance: Any = 0,
    access_equipment_allowance: Any = 0,
    subcontractor_allowance: Any = 0,
    sundries_allowance: Any = 0,
    contingency_percent: Any = 0,
    gst_percent: Any = 10,
    day_hours: Any = DEFAULT_DAY_HOURS,
    value_target: Any = DEFAULT_VALUE_TARGET,
) -> dict[str, float]:
    """Price work from the profit-inclusive painter-day target with no margin input.

    Take-off line totals are already selling values.  Where an estimate is built
    from labour hours instead, the equivalent selling value is derived from the
    configured painter-day target.  Taking the larger of the two prevents labour
    from being counted twice when hours were calculated from the take-off.
    """
    settings = validate_production_targets(
        day_hours=day_hours,
        value_low=value_target,
        value_target=value_target,
        value_high=value_target,
    )
    lines = _non_negative_number(line_total, "Line total")
    hours = _non_negative_number(labour_hours, "Labour hours")
    labour_sell_value = hours / settings["day_hours"] * settings["value_target"]
    work_sell_value = max(lines, labour_sell_value)
    allowances = sum(
        _non_negative_number(value, name)
        for value, name in (
            (material_allowance, "Material allowance"),
            (access_equipment_allowance, "Access allowance"),
            (subcontractor_allowance, "Subcontractor allowance"),
            (sundries_allowance, "Sundries allowance"),
        )
    )
    gst = _non_negative_number(gst_percent, "GST percent")
    subtotal = work_sell_value + allowances
    # Kept in the call signature so older Job Packs remain importable, but the
    # profit-inclusive painter-day target is the only pricing uplift JobHub uses.
    # No separate contingency percentage is applied.
    _ = contingency_percent
    contingency_amount = 0.0
    total_ex_gst = subtotal
    gst_amount = total_ex_gst * gst / 100.0
    return {
        "line_total": round(lines, 2),
        "labour_sell_value": round(labour_sell_value, 2),
        "work_sell_value": round(work_sell_value, 2),
        "allowances_total": round(allowances, 2),
        "direct_total": round(subtotal, 2),
        "contingency_amount": round(contingency_amount, 2),
        "subtotal_before_pricing": round(total_ex_gst, 2),
        "margin_amount": 0.0,
        "achieved_margin_percent": 0.0,
        "total_ex_gst": round(total_ex_gst, 2),
        "gst_amount": round(gst_amount, 2),
        "total_inc_gst": round(total_ex_gst + gst_amount, 2),
    }
