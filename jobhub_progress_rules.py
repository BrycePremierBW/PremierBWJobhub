"""Pure progress-weighting rules used by JobHub's progress tracker."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence


INTERNAL_STAGES = (
    ("prepped_sealed", "Prepped and sealed", 30.0),
    ("prep_spray_finished", "Prep and spray finished", 30.0),
    ("cut_rolled", "Cut and rolled", 30.0),
    ("defects", "Defects", 10.0),
)

EXTERNAL_STAGES = (
    ("prep", "Preparation", 15.0),
    ("primer", "Primer / Sealer", 20.0),
    ("first_coat", "First Coat", 25.0),
    ("final_coat", "Final Coat", 30.0),
    ("touchups", "Touch-ups", 10.0),
)

STATUS_FACTOR = {"Not started": 0.0, "In progress": 0.5, "Complete": 1.0}
STATUS_OPTIONS = tuple(STATUS_FACTOR)


def status_factor(value: Any) -> float:
    return STATUS_FACTOR.get(str(value or "Not started"), 0.0)


def weighted_percent(
    row: Mapping[str, Any],
    stages: Sequence[tuple[str, str, float]],
) -> float:
    """Calculate progress from Not started/In progress/Complete selections."""
    weight_total = sum(float(stage[2]) for stage in stages) or 100.0
    earned = sum(status_factor(row.get(stage[0])) * float(stage[2]) for stage in stages)
    return round(earned / weight_total * 100.0, 2)


def combine_internal_progress(rows: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    """Combine floor-m² rows with separately weighted internal scope items.

    Custom items consume their nominated percentage of the internal scope. The
    regular floor-m² work consumes the remaining percentage, preventing stairs
    and similar quoted items from being counted twice.
    """
    records = list(rows)
    regular = [row for row in records if not bool(int(row.get("is_custom") or 0))]
    custom = [row for row in records if bool(int(row.get("is_custom") or 0))]

    floor_m2 = sum(max(0.0, float(row.get("floor_m2") or 0)) for row in regular)
    floor_done = sum(
        max(0.0, float(row.get("floor_m2") or 0))
        * min(100.0, max(0.0, float(row.get("progress_percent") or 0)))
        / 100.0
        for row in regular
    )
    floor_percent = floor_done / floor_m2 * 100.0 if floor_m2 else 0.0

    raw_custom_weight = sum(
        max(0.0, float(row.get("scope_percent") or 0)) for row in custom
    )
    custom_scale = min(1.0, 100.0 / raw_custom_weight) if raw_custom_weight else 1.0
    custom_weight = min(100.0, raw_custom_weight)
    custom_earned_points = sum(
        min(100.0, max(0.0, float(row.get("progress_percent") or 0)))
        * max(0.0, float(row.get("scope_percent") or 0))
        * custom_scale
        / 100.0
        for row in custom
    )

    if floor_m2:
        floor_share = max(0.0, 100.0 - custom_weight)
        internal_percent = floor_percent * floor_share / 100.0 + custom_earned_points
    elif custom_weight:
        internal_percent = custom_earned_points / custom_weight * 100.0
    else:
        internal_percent = 0.0

    return {
        "internal_m2": floor_m2,
        "internal_done": floor_done,
        "internal_floor_percent": floor_percent,
        "internal_percent": min(100.0, max(0.0, internal_percent)),
        "custom_item_count": float(len(custom)),
        "custom_weight_percent": custom_weight,
    }
