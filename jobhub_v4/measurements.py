"""Measurement-basis rules shared by Painting Intelligence and tests."""

from __future__ import annotations

from typing import Any


INTERNAL_FLOOR_AREA = "Internal — Floor m²"
EXTERNAL_SUBSTRATE_AREA = "External — Substrate m²"

MEASUREMENT_BASIS_OPTIONS = (
    INTERNAL_FLOOR_AREA,
    EXTERNAL_SUBSTRATE_AREA,
    "Lineal m",
    "Item",
)


def normalised_work_unit(value: Any) -> str:
    """Map imported take-off units to a selectable production basis."""
    unit = str(value or "item").strip().casefold().replace("²", "2")
    compact = " ".join(unit.split())
    square_metre_unit = any(
        marker in compact for marker in ("m2", "sqm", "sq m", "square metre")
    )
    if "floor" in compact and square_metre_unit:
        return INTERNAL_FLOOR_AREA
    if square_metre_unit:
        return EXTERNAL_SUBSTRATE_AREA
    if compact in {
        "lm", "lin m", "linear m", "lineal m", "lineal metre", "lineal metres",
    }:
        return "Lineal m"
    return "Item"


def recommended_measurement_basis(
    unit: Any,
    *,
    stage_name: Any = "",
    context: Any = "",
) -> str:
    """Choose the Premier Brushworks basis while respecting an explicit source unit."""
    raw_unit = str(unit or "").strip().casefold().replace("²", "2")
    basis = normalised_work_unit(raw_unit)
    if basis in {INTERNAL_FLOOR_AREA, "Lineal m", "Item"}:
        return basis
    if "substrate" in raw_unit:
        return EXTERNAL_SUBSTRATE_AREA

    stage = str(stage_name or "").strip().casefold()
    detail = str(context or "").strip().casefold()
    if "internal" in stage or "interior" in stage:
        return INTERNAL_FLOOR_AREA
    if "external" in stage or "exterior" in stage:
        return EXTERNAL_SUBSTRATE_AREA
    if "internal" in detail or "interior" in detail:
        return INTERNAL_FLOOR_AREA
    return EXTERNAL_SUBSTRATE_AREA


def work_unit_for_measurement_basis(measurement_basis: str) -> str:
    """Return the concise unit stored against take-off and production lines."""
    return {
        INTERNAL_FLOOR_AREA: "floor m²",
        EXTERNAL_SUBSTRATE_AREA: "substrate m²",
        "Lineal m": "lineal m",
        "Item": "item",
    }[measurement_basis]
