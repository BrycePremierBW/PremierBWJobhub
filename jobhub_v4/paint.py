"""Paint quantity and pack-size optimisation rules.

The functions in this module are intentionally UI- and database-independent so
estimators can test the commercial rules without starting Streamlit.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
from typing import Any, Mapping, Sequence


LITRE_PLACES = Decimal("0.01")
MONEY_PLACES = Decimal("0.01")
DEFAULT_PACK_SIZES = (4, 10, 15)


def _decimal(value: Any, field_name: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{field_name} must be a number.") from None
    if not number.is_finite():
        raise ValueError(f"{field_name} must be finite.")
    return number


def calculate_paint_quantity(
    *,
    area_sqm: Any,
    coats: Any,
    coverage_sqm_per_litre: Any,
    waste_percent: Any = 10,
) -> dict[str, float]:
    """Return base and waste-adjusted litres for one coating system."""
    area = _decimal(area_sqm, "Area")
    coat_count = _decimal(coats, "Coat count")
    coverage = _decimal(coverage_sqm_per_litre, "Coverage")
    waste = _decimal(waste_percent, "Waste percentage")

    if area < 0:
        raise ValueError("Area cannot be negative.")
    if coat_count <= 0:
        raise ValueError("Coat count must be greater than zero.")
    if coat_count != coat_count.to_integral_value():
        raise ValueError("Coat count must be a whole number.")
    if coverage <= 0:
        raise ValueError("Coverage must be greater than zero.")
    if waste < 0 or waste > 100:
        raise ValueError("Waste percentage must be between 0 and 100.")

    base = (area * coat_count) / coverage
    waste_litres = base * waste / Decimal("100")
    required = base + waste_litres
    return {
        "area_sqm": float(area.quantize(LITRE_PLACES, rounding=ROUND_HALF_UP)),
        "coats": int(coat_count),
        "coverage_sqm_per_litre": float(
            coverage.quantize(LITRE_PLACES, rounding=ROUND_HALF_UP)
        ),
        "base_litres": float(base.quantize(LITRE_PLACES, rounding=ROUND_HALF_UP)),
        "waste_litres": float(
            waste_litres.quantize(LITRE_PLACES, rounding=ROUND_HALF_UP)
        ),
        "required_litres": float(
            required.quantize(LITRE_PLACES, rounding=ROUND_CEILING)
        ),
    }


@dataclass(frozen=True)
class _Plan:
    cost: Decimal
    stock_counts: tuple[int, ...]
    purchase_counts: tuple[int, ...]

    @property
    def purchased_packs(self) -> int:
        return sum(self.purchase_counts)

    @property
    def stock_packs(self) -> int:
        return sum(self.stock_counts)


def _normalised_integer_mapping(
    source: Mapping[int | str, Any] | None,
    pack_sizes: Sequence[int],
    *,
    field_name: str,
) -> dict[int, int]:
    source = source or {}
    result: dict[int, int] = {}
    for size in pack_sizes:
        raw = source.get(size, source.get(str(size), 0))
        number = _decimal(raw, f"{field_name} for {size} L")
        if number < 0 or number != number.to_integral_value():
            raise ValueError(f"{field_name} for {size} L must be a non-negative whole number.")
        result[size] = int(number)
    return result


def _normalised_prices(
    source: Mapping[int | str, Any],
    pack_sizes: Sequence[int],
) -> dict[int, Decimal | None]:
    result: dict[int, Decimal | None] = {}
    for size in pack_sizes:
        raw = source.get(size, source.get(str(size)))
        if raw in (None, ""):
            result[size] = None
            continue
        price = _decimal(raw, f"Supplier price for {size} L")
        if price < 0:
            raise ValueError(f"Supplier price for {size} L cannot be negative.")
        result[size] = price.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)
    return result


def _prefer_exact_volume(candidate: _Plan, current: _Plan | None) -> bool:
    if current is None:
        return True
    candidate_key = (
        candidate.cost,
        candidate.purchased_packs,
        candidate.stock_packs,
    )
    current_key = (current.cost, current.purchased_packs, current.stock_packs)
    return candidate_key < current_key


def optimise_pack_mix(
    *,
    required_litres: Any,
    supplier_prices: Mapping[int | str, Any],
    warehouse_stock: Mapping[int | str, Any] | None = None,
    pack_sizes: Sequence[int] = DEFAULT_PACK_SIZES,
) -> dict[str, Any]:
    """Find the lowest-purchase-cost mix that covers the required litres.

    Warehouse packs are treated as already paid for. Within equal purchase cost
    the optimiser minimises excess litres, purchased packs and total packs.
    """
    required = _decimal(required_litres, "Required litres")
    if required <= 0:
        raise ValueError("Required litres must be greater than zero.")
    sizes = tuple(sorted({int(size) for size in pack_sizes}))
    if not sizes or any(size <= 0 for size in sizes):
        raise ValueError("Pack sizes must contain positive whole litres.")

    stock = _normalised_integer_mapping(
        warehouse_stock,
        sizes,
        field_name="Warehouse stock",
    )
    prices = _normalised_prices(supplier_prices, sizes)
    if not any(stock.values()) and not any(price is not None for price in prices.values()):
        raise ValueError("At least one warehouse pack or supplier price is required.")

    target = int(required.to_integral_value(rounding=ROUND_CEILING))
    volume_limit = target + max(sizes) - 1
    empty_counts = tuple(0 for _ in sizes)
    plans: list[_Plan | None] = [None] * (volume_limit + 1)
    plans[0] = _Plan(Decimal("0"), empty_counts, empty_counts)

    # Bounded, zero-cost warehouse packs.
    for pack_index, size in enumerate(sizes):
        for _ in range(stock[size]):
            for volume in range(volume_limit, size - 1, -1):
                previous = plans[volume - size]
                if previous is None:
                    continue
                counts = list(previous.stock_counts)
                counts[pack_index] += 1
                candidate = _Plan(
                    previous.cost,
                    tuple(counts),
                    previous.purchase_counts,
                )
                if _prefer_exact_volume(candidate, plans[volume]):
                    plans[volume] = candidate

    # Unbounded supplier packs.
    for pack_index, size in enumerate(sizes):
        price = prices[size]
        if price is None:
            continue
        for volume in range(size, volume_limit + 1):
            previous = plans[volume - size]
            if previous is None:
                continue
            counts = list(previous.purchase_counts)
            counts[pack_index] += 1
            candidate = _Plan(
                previous.cost + price,
                previous.stock_counts,
                tuple(counts),
            )
            if _prefer_exact_volume(candidate, plans[volume]):
                plans[volume] = candidate

    feasible = [
        (volume, plan)
        for volume, plan in enumerate(plans)
        if volume >= target and plan is not None
    ]
    if not feasible:
        raise ValueError("The available stock and supplier pack sizes cannot cover the requirement.")

    selected_volume, selected = min(
        feasible,
        key=lambda item: (
            item[1].cost,
            Decimal(item[0]) - required,
            item[1].purchased_packs,
            item[1].purchased_packs + item[1].stock_packs,
        ),
    )
    lines = []
    for index, size in enumerate(sizes):
        stock_count = selected.stock_counts[index]
        purchase_count = selected.purchase_counts[index]
        if not stock_count and not purchase_count:
            continue
        unit_price = prices[size] or Decimal("0")
        lines.append(
            {
                "pack_size_litres": size,
                "warehouse_packs": stock_count,
                "supplier_packs": purchase_count,
                "total_packs": stock_count + purchase_count,
                "supplier_unit_price": float(unit_price),
                "purchase_cost": float(
                    (unit_price * purchase_count).quantize(
                        MONEY_PLACES,
                        rounding=ROUND_HALF_UP,
                    )
                ),
            }
        )
    return {
        "required_litres": float(required.quantize(LITRE_PLACES, rounding=ROUND_CEILING)),
        "supplied_litres": selected_volume,
        "excess_litres": float(
            (Decimal(selected_volume) - required).quantize(
                LITRE_PLACES,
                rounding=ROUND_HALF_UP,
            )
        ),
        "purchase_cost": float(
            selected.cost.quantize(MONEY_PLACES, rounding=ROUND_HALF_UP)
        ),
        "warehouse_packs": selected.stock_packs,
        "supplier_packs": selected.purchased_packs,
        "lines": lines,
    }


def colour_order_allowed(
    approval_status: str,
    *,
    approved_by: str = "",
    approved_at: str = "",
) -> tuple[bool, str]:
    """Enforce the colour-approval gate before a material order is released."""
    status = str(approval_status or "").strip().casefold()
    if status != "approved":
        return False, "Colour approval is required before material ordering."
    if not str(approved_by or "").strip():
        return False, "Record who approved the colour before material ordering."
    if not str(approved_at or "").strip():
        return False, "Record the colour approval date before material ordering."
    return True, ""
