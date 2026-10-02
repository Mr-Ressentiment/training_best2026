"""Adapter converting ORM OrderDraft entities into flat symbolic facts.

Converts an ORM OrderDraft graph into a flat, deterministic dictionary of scalar
facts for consumption by the symbolic execution engine (State.facts).
"""
from __future__ import annotations

from decimal import Decimal
import sys
from typing import Any, Mapping

try:
    from sqlalchemy import inspect
except ImportError:  # pragma: no cover
    inspect = None  # type: ignore

from app.models.entities import OrderDraft

FactValue = int | float | str | bool | None


def _is_unloaded(entity: Any, attribute: str) -> bool:
    """Return True if an ORM relationship attribute is unloaded."""
    if inspect is None:
        return False
    try:
        insp = inspect(entity)
        if insp is not None and attribute in insp.unloaded:
            return True
    except Exception:
        pass
    return False


def _as_scalar_str(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, str):
        return val
    return str(val)


def _as_scalar_int(val: Any) -> int | None:
    if isinstance(val, bool) or val is None:
        return None
    if isinstance(val, int):
        return val
    return None


def _calculate_price_deviation_pct(line: Any) -> float | None:
    """Calculate price deviation percentage between customer and contract unit price.

    Formula:
        price_deviation_pct = 100 * (extracted_unit_price_cents - contract_price_cents) / contract_price_cents

    Sign convention:
        - Positive (> 0): Customer-stated price is higher than contract price.
        - Negative (< 0): Customer-stated price is lower than contract price.
        - Zero (0.0): Customer-stated price exactly matches contract price.
        - None: Either price is absent/invalid, or contract_price_cents <= 0.
    """
    extracted_price = getattr(line, "extracted_unit_price_cents", None)
    contract_price = getattr(line, "contract_price_cents", None)

    if (
        isinstance(extracted_price, bool)
        or not isinstance(extracted_price, int)
        or extracted_price < 0
    ):
        return None

    if (
        isinstance(contract_price, bool)
        or not isinstance(contract_price, int)
        or contract_price <= 0
    ):
        return None

    delta = Decimal(extracted_price) - Decimal(contract_price)
    pct = (Decimal(100) * delta) / Decimal(contract_price)
    return float(pct)


def _calculate_moq_gap(line: Any) -> int | None:
    """Calculate the shortfall in quantity to meet the catalog Minimum Order Quantity (MOQ).

    Formula:
        moq_gap = max(0, CatalogProduct.min_order_quantity - extracted_quantity)

    Sign convention:
        - 0: MOQ is satisfied (extracted_quantity >= min_order_quantity).
        - Positive (> 0): Missing units required to satisfy MOQ.
        - None: SKU is unresolved, ambiguity is present without operator selection,
          catalog product is missing/unloaded, min_order_quantity is unavailable,
          or extracted_quantity is absent/invalid.
    """
    matched_sku = getattr(line, "matched_sku", None)
    if not matched_sku or not isinstance(matched_sku, str) or not matched_sku.strip():
        return None

    sku_conf = getattr(line, "sku_confidence", None)
    sku_src = getattr(line, "sku_resolution_source", None)
    if sku_conf in ("Ambiguous", "Unrecognized") and sku_src != "OPERATOR_SELECTED":
        return None

    qty = getattr(line, "extracted_quantity", None)
    if isinstance(qty, bool) or not isinstance(qty, int) or qty < 0:
        return None

    if _is_unloaded(line, "product"):
        return None

    product = getattr(line, "product", None)
    if product is None:
        return None

    moq = getattr(product, "min_order_quantity", None)
    if isinstance(moq, bool) or not isinstance(moq, int) or moq < 1:
        return None

    return max(0, moq - qty)


def _calculate_line_arith_delta_cents(line: Any) -> int | None:
    """Calculate the arithmetic discrepancy for an individual line item.

    Formula:
        line.arith_delta_cents = extracted_line_total_cents - (extracted_quantity * extracted_unit_price_cents)

    Sign convention:
        - Positive (> 0): Customer-stated line total is greater than arithmetic product.
        - Negative (< 0): Customer-stated line total is less than arithmetic product.
        - Zero (0): Customer-stated line total exactly matches arithmetic product.
        - None: Any of the three customer-stated input values is absent or invalid.
    """
    line_total = getattr(line, "extracted_line_total_cents", None)
    qty = getattr(line, "extracted_quantity", None)
    unit_price = getattr(line, "extracted_unit_price_cents", None)

    if (
        isinstance(line_total, bool)
        or not isinstance(line_total, int)
        or isinstance(qty, bool)
        or not isinstance(qty, int)
        or isinstance(unit_price, bool)
        or not isinstance(unit_price, int)
    ):
        return None

    return line_total - (qty * unit_price)


def _calculate_draft_arith_delta_cents(
    draft: Any, active_lines: list[Any]
) -> int | None:
    """Calculate the order-level arithmetic discrepancy.

    Formula:
        draft.arith_delta_cents = extracted_order_total_cents - sum(extracted_line_total_cents for active lines)

    Sign convention:
        - Positive (> 0): Customer-stated order total is greater than sum of active line totals.
        - Negative (< 0): Customer-stated order total is less than sum of active line totals.
        - Zero (0): Customer-stated order total exactly matches sum of active line totals.
        - None: extracted_order_total_cents is absent or any active line total is missing/invalid.
    """
    order_total = getattr(draft, "extracted_order_total_cents", None)
    if isinstance(order_total, bool) or not isinstance(order_total, int):
        return None

    if not active_lines:
        return None

    sum_lines = 0
    for line in active_lines:
        lt = getattr(line, "extracted_line_total_cents", None)
        if isinstance(lt, bool) or not isinstance(lt, int):
            return None
        sum_lines += lt

    return order_total - sum_lines


def draft_to_facts(draft: OrderDraft) -> dict[str, FactValue]:
    """Convert an ORM OrderDraft entity into a flat dictionary of scalar facts.

    This is a pure, deterministic, read-only function that extracts scalar facts
    and calculates derived values (price deviation, MOQ gap, arithmetic deltas).
    It does not execute database queries, modify ORM entities, or trigger side effects.

    Args:
        draft: The OrderDraft entity (with pre-loaded relationships if needed).

    Returns:
        dict[str, FactValue]: A flat dictionary of scalar fact values (int, float, str, bool, None).
    """
    raw_lines = []
    if not _is_unloaded(draft, "line_items"):
        raw_lines = getattr(draft, "line_items", None) or []

    active_lines = [
        line for line in raw_lines
        if getattr(line, "status", None) == "Active"
    ]

    def _sort_key(line: Any) -> tuple[int, str]:
        num = getattr(line, "line_number", None)
        line_num = num if isinstance(num, int) and not isinstance(num, bool) else sys.maxsize
        line_id = str(getattr(line, "id", None) or "")
        return (line_num, line_id)

    active_lines.sort(key=_sort_key)

    facts: dict[str, FactValue] = {
        "draft.id": _as_scalar_str(getattr(draft, "id", None)),
        "draft.status": _as_scalar_str(getattr(draft, "status", None)),
        "draft.customer_id": _as_scalar_str(getattr(draft, "customer_id", None)),
        "draft.active_line_count": len(active_lines),
        "draft.extracted_order_total_cents": _as_scalar_int(getattr(draft, "extracted_order_total_cents", None)),
        "draft.arith_delta_cents": _calculate_draft_arith_delta_cents(draft, active_lines),
    }

    for idx, line in enumerate(active_lines, start=1):
        facts[f"line.{idx}.line_number"] = _as_scalar_int(getattr(line, "line_number", None))
        facts[f"line.{idx}.status"] = _as_scalar_str(getattr(line, "status", None))
        facts[f"line.{idx}.sku_confidence"] = _as_scalar_str(getattr(line, "sku_confidence", None))
        facts[f"line.{idx}.sku_resolution_source"] = _as_scalar_str(getattr(line, "sku_resolution_source", None))
        facts[f"line.{idx}.matched_sku"] = _as_scalar_str(getattr(line, "matched_sku", None))
        facts[f"line.{idx}.extracted_quantity"] = _as_scalar_int(getattr(line, "extracted_quantity", None))
        facts[f"line.{idx}.extracted_unit_price_cents"] = _as_scalar_int(getattr(line, "extracted_unit_price_cents", None))
        facts[f"line.{idx}.contract_price_cents"] = _as_scalar_int(getattr(line, "contract_price_cents", None))
        facts[f"line.{idx}.extracted_line_total_cents"] = _as_scalar_int(getattr(line, "extracted_line_total_cents", None))
        facts[f"line.{idx}.price_deviation_pct"] = _calculate_price_deviation_pct(line)
        facts[f"line.{idx}.moq_gap"] = _calculate_moq_gap(line)
        facts[f"line.{idx}.arith_delta_cents"] = _calculate_line_arith_delta_cents(line)

    return facts
