"""Production-target calculations for Premier Brushworks estimates and progress.

The operating target is expressed as completed sell value per painter-day.  A
measured line's unit rate converts that value target back into the m², lineal
metres, items or other units that should be completed in an eight-hour day.
"""

from __future__ import annotations

import math
from typing import Any


DEFAULT_DAY_HOURS = 8.0
DEFAULT_VALUE_LOW = 800.0
DEFAULT_VALUE_TARGET = 900.0
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
    contingency = _non_negative_number(contingency_percent, "Contingency percent")
    gst = _non_negative_number(gst_percent, "GST percent")
    subtotal = work_sell_value + allowances
    contingency_amount = subtotal * contingency / 100.0
    total_ex_gst = subtotal + contingency_amount
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
