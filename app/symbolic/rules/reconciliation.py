"""Symbolic reconciliation rules for OrderShield.

Defines deterministic Rule evaluators for core business discrepancy types:
- PriceMismatch (ADV-PRC-01)
- QuantityOrPackagingBreach (ADV-PKG-01)
- Line ArithmeticMismatch (ADV-ARITH-LINE-01)
- Order ArithmeticMismatch (ADV-ARITH-ORDER-01)

All rules operate strictly on flat scalar facts (integer cents and quantities)
without floating-point conversions, database access, or state mutations.
"""
from __future__ import annotations

from typing import Any

from app.symbolic.engine import Facts, Rule

ADV_PRC_01 = "ADV-PRC-01"
ADV_PKG_01 = "ADV-PKG-01"
ADV_ARITH_LINE_01 = "ADV-ARITH-LINE-01"
ADV_ARITH_ORDER_01 = "ADV-ARITH-ORDER-01"


def _active_line_indices(facts: Facts) -> list[int]:
    """Retrieve 1-based indices for active lines from facts mapping."""
    count = facts.get("draft.active_line_count")
    if type(count) is int and count > 0:
        return list(range(1, count + 1))

    # Fallback to key scanning for test-local flat facts
    indices: set[int] = set()
    for k in facts:
        if k.startswith("line."):
            parts = k.split(".", 2)
            if len(parts) >= 2 and parts[1].isdigit():
                indices.add(int(parts[1]))
    return sorted(indices)


def _when_price_mismatch(facts: Facts) -> bool:
    """Trigger when customer-stated unit price differs from contract tier price.

    Requires valid integer cents for both stated and contract prices on at least one
    active line item.
    """
    for i in _active_line_indices(facts):
        stated = facts.get(f"line.{i}.extracted_unit_price_cents")
        contract = facts.get(f"line.{i}.contract_price_cents")
        if type(stated) is int and type(contract) is int:
            if stated != contract:
                return True
    return False


def _when_quantity_or_packaging_breach(facts: Facts) -> bool:
    """Trigger when order quantity breaches MOQ shortfall or package increment multiplicity.

    Triggers if for any active line:
    1. moq_gap is a positive integer (> 0).
    2. quantity and package_increment are positive integers and quantity is not an exact
       multiple of package_increment (quantity % package_increment != 0).
    """
    for i in _active_line_indices(facts):
        # 1. MOQ shortfall
        moq_gap = facts.get(f"line.{i}.moq_gap")
        if type(moq_gap) is int and moq_gap > 0:
            return True

        # 2. Package increment multiplicity violation
        qty = facts.get(f"line.{i}.extracted_quantity")
        pkg = facts.get(f"line.{i}.package_increment")
        if (
            type(qty) is int
            and type(pkg) is int
            and qty >= 1
            and pkg >= 1
            and (qty % pkg != 0)
        ):
            return True
    return False


def _when_line_arithmetic_mismatch(facts: Facts) -> bool:
    """Trigger when customer-stated line total differs from quantity * unit price."""
    for i in _active_line_indices(facts):
        delta = facts.get(f"line.{i}.arith_delta_cents")
        if type(delta) is int and delta != 0:
            return True
    return False


def _when_order_arithmetic_mismatch(facts: Facts) -> bool:
    """Trigger when customer-stated order total differs from sum of stated line totals."""
    delta = facts.get("draft.arith_delta_cents")
    return type(delta) is int and delta != 0


RECONCILIATION_RULES: tuple[Rule, ...] = (
    Rule(
        id=ADV_PRC_01,
        when=_when_price_mismatch,
        then={"reconciliation.price_mismatch": True},
        why="Customer-stated unit price differs from contract tier price on one or more lines",
    ),
    Rule(
        id=ADV_PKG_01,
        when=_when_quantity_or_packaging_breach,
        then={"reconciliation.quantity_or_packaging_breach": True},
        why="Quantity is below minimum order quantity (MOQ) or violates package increment on one or more lines",
    ),
    Rule(
        id=ADV_ARITH_LINE_01,
        when=_when_line_arithmetic_mismatch,
        then={
            "reconciliation.arithmetic_mismatch": True,
            "reconciliation.line_arithmetic_mismatch": True,
        },
        why="Customer-stated line total differs from quantity multiplied by unit price on one or more lines",
    ),
    Rule(
        id=ADV_ARITH_ORDER_01,
        when=_when_order_arithmetic_mismatch,
        then={
            "reconciliation.arithmetic_mismatch": True,
            "reconciliation.order_arithmetic_mismatch": True,
        },
        why="Customer-stated order total differs from sum of stated active line totals",
    ),
)
