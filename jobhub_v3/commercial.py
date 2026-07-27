"""Commercial workflow rules for progress claims, retention and EOTs."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


MONEY = Decimal("0.01")


def _decimal(value: Any, name: str) -> Decimal:
    try:
        result = Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{name} must be a number.") from None
    if not result.is_finite():
        raise ValueError(f"{name} must be finite.")
    return result


def _money(value: Decimal) -> float:
    return float(value.quantize(MONEY, rounding=ROUND_HALF_UP))


def calculate_progress_claim(
    *,
    contract_value: Any,
    work_complete_percent: Any,
    previous_claims_ex_gst: Any = 0,
    approved_variations_ex_gst: Any = 0,
    retention_rate_percent: Any = 0,
    retention_held_to_date: Any = 0,
    retention_cap_percent: Any = 5,
) -> dict[str, float]:
    """Calculate a cumulative progress claim with a retention cap."""
    contract = _decimal(contract_value, "Contract value")
    percent = _decimal(work_complete_percent, "Work complete percentage")
    previous = _decimal(previous_claims_ex_gst, "Previous claims")
    variations = _decimal(approved_variations_ex_gst, "Approved variations")
    retention_rate = _decimal(retention_rate_percent, "Retention rate")
    retention_held = _decimal(retention_held_to_date, "Retention held to date")
    cap_percent = _decimal(retention_cap_percent, "Retention cap")

    if contract < 0 or previous < 0 or variations < 0 or retention_held < 0:
        raise ValueError("Commercial amounts cannot be negative.")
    if percent < 0 or percent > 100:
        raise ValueError("Work complete percentage must be between 0 and 100.")
    if retention_rate < 0 or retention_rate > 100:
        raise ValueError("Retention rate must be between 0 and 100.")
    if cap_percent < 0 or cap_percent > 100:
        raise ValueError("Retention cap must be between 0 and 100.")

    earned_to_date = contract * percent / Decimal("100") + variations
    gross_claim = max(Decimal("0"), earned_to_date - previous)
    retention_cap = contract * cap_percent / Decimal("100")
    remaining_cap = max(Decimal("0"), retention_cap - retention_held)
    retention_this_claim = min(
        gross_claim * retention_rate / Decimal("100"),
        remaining_cap,
    )
    net_claim = gross_claim - retention_this_claim
    return {
        "earned_to_date_ex_gst": _money(earned_to_date),
        "gross_claim_ex_gst": _money(gross_claim),
        "retention_cap_ex_gst": _money(retention_cap),
        "retention_this_claim_ex_gst": _money(retention_this_claim),
        "retention_held_to_date_ex_gst": _money(retention_held + retention_this_claim),
        "net_claim_ex_gst": _money(net_claim),
    }


WORKFLOW_TRANSITIONS = {
    "progress_claim": {
        "draft": {"submitted", "void"},
        "submitted": {"approved", "rejected", "draft"},
        "approved": {"invoiced", "void"},
        "invoiced": {"part_paid", "paid", "void"},
        "part_paid": {"paid"},
        "rejected": {"draft", "void"},
        "paid": set(),
        "void": set(),
    },
    "supplier_bill": {
        "draft": {"submitted", "void"},
        "submitted": {"approved", "rejected", "draft"},
        "approved": {"sent_to_xero", "void"},
        "sent_to_xero": {"part_paid", "paid", "void"},
        "part_paid": {"paid"},
        "rejected": {"draft", "void"},
        "paid": set(),
        "void": set(),
    },
    "extension_of_time": {
        "draft": {"submitted", "void"},
        "submitted": {"approved", "rejected", "withdrawn"},
        "rejected": {"draft", "void"},
        "approved": set(),
        "withdrawn": set(),
        "void": set(),
    },
}


def validate_transition(entity_type: str, current_status: str, target_status: str) -> None:
    entity = str(entity_type or "").strip().casefold()
    current = str(current_status or "").strip().casefold()
    target = str(target_status or "").strip().casefold()
    if entity not in WORKFLOW_TRANSITIONS:
        raise ValueError(f"Unsupported commercial workflow: {entity_type}")
    if current not in WORKFLOW_TRANSITIONS[entity]:
        raise ValueError(f"Unsupported {entity} status: {current_status}")
    if target not in WORKFLOW_TRANSITIONS[entity][current]:
        raise ValueError(f"{entity} cannot move from {current} to {target}.")


def calculate_eot_due_date_extension(
    *,
    approved_days: Any,
    concurrent_delay_days: Any = 0,
) -> int:
    approved = _decimal(approved_days, "Approved EOT days")
    concurrent = _decimal(concurrent_delay_days, "Concurrent delay days")
    if approved < 0 or concurrent < 0:
        raise ValueError("EOT days cannot be negative.")
    if approved != approved.to_integral_value() or concurrent != concurrent.to_integral_value():
        raise ValueError("EOT days must be whole numbers.")
    return max(0, int(approved - concurrent))
