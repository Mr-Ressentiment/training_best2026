"""Deterministic contract pricing tier lookup and integer-cents arithmetic engine.

Provides foundational business rule primitives for T010:
- Deterministic customer/SKU quantity-tier lookup
- Exact integer-cents line arithmetic
- Exact integer-cents subtotal arithmetic

No AI participates in pricing, arithmetic, tier selection, or fallback decisions.
"""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import (
    CatalogProduct, ContractPriceTier, CustomerContract, DiscrepancyFlag,
    DraftLineItem, OrderDraft,
)


class PricingError(Exception):
    """Base exception for deterministic pricing engine errors."""


class PricingConflictError(PricingError):
    """Raised when multiple conflicting contract tiers exist at the same threshold."""


def calculate_line_total_cents(
    quantity: int,
    contract_price_cents: int,
) -> int:
    """Calculate the exact line total in integer cents.

    calculated_line_total_cents = extracted_quantity * contract_price_cents

    Uses pure integer arithmetic without floating-point intermediates.
    Rejects invalid types (e.g. bool, float) and invalid values (quantity < 1, price < 0).
    """
    if isinstance(quantity, bool) or type(quantity) is not int:
        raise TypeError(
            f"quantity must be an integer, got {type(quantity).__name__} ({quantity!r})"
        )
    if quantity < 1:
        raise ValueError(
            f"quantity must be a positive integer (>= 1), got {quantity}"
        )

    if isinstance(contract_price_cents, bool) or type(contract_price_cents) is not int:
        raise TypeError(
            f"contract_price_cents must be an integer, got {type(contract_price_cents).__name__} ({contract_price_cents!r})"
        )
    if contract_price_cents < 0:
        raise ValueError(
            f"contract_price_cents must be a nonnegative integer (>= 0), got {contract_price_cents}"
        )

    return quantity * contract_price_cents


def calculate_subtotal_cents(
    line_totals_cents: Iterable[int],
) -> int:
    """Calculate the exact order subtotal in integer cents by summing line totals.

    subtotal_cents = sum(line_total_cents)

    Uses pure integer arithmetic. Rejects non-integers, booleans, and negative values.
    Empty iterable yields 0 cents.
    """
    if isinstance(line_totals_cents, (str, bytes)):
        raise TypeError("line_totals_cents cannot be a string or bytes")
    try:
        iterator = iter(line_totals_cents)
    except TypeError as exc:
        raise TypeError(
            f"line_totals_cents must be iterable, got {type(line_totals_cents).__name__}"
        ) from exc

    subtotal = 0
    for idx, val in enumerate(iterator):
        if isinstance(val, bool) or type(val) is not int:
            raise TypeError(
                f"line_total_cents at index {idx} must be an integer, got {type(val).__name__} ({val!r})"
            )
        if val < 0:
            raise ValueError(
                f"line_total_cents at index {idx} must be a nonnegative integer (>= 0), got {val}"
            )
        subtotal += val

    return subtotal


def select_contract_price_tier(
    db: Session,
    customer_id: str,
    sku: str,
    quantity: int,
) -> ContractPriceTier | None:
    """Select the eligible contract price tier for a customer and SKU based on quantity.

    Frozen Tier Selection Rule:
    Given:
      - customer C
      - SKU S
      - requested quantity Q

    1. Query ContractPriceTier records satisfying:
         contract.customer_id == C
         sku == S
         min_quantity <= Q
    2. Fail closed if eligible tiers belong to more than one contract (PricingConflictError).
    3. Select the eligible tier having the maximum min_quantity within the single contract.
    4. If no tier has min_quantity <= Q, returns None (explicit no-tier result).

    Does not apply automatic fallback to base price or lower tiers.
    """
    if not isinstance(db, Session):
        raise TypeError(f"db must be a sqlalchemy.orm.Session, got {type(db).__name__}")

    if isinstance(customer_id, bool) or not isinstance(customer_id, str):
        raise TypeError(
            f"customer_id must be a string, got {type(customer_id).__name__}"
        )
    if len(customer_id) == 0:
        raise ValueError("customer_id cannot be empty")

    if isinstance(sku, bool) or not isinstance(sku, str):
        raise TypeError(f"sku must be a string, got {type(sku).__name__}")
    if len(sku) == 0:
        raise ValueError("sku cannot be empty")

    if isinstance(quantity, bool) or type(quantity) is not int:
        raise TypeError(
            f"quantity must be an integer, got {type(quantity).__name__} ({quantity!r})"
        )
    if quantity < 1:
        raise ValueError(
            f"quantity must be a positive integer (>= 1), got {quantity}"
        )

    stmt = (
        select(ContractPriceTier)
        .join(CustomerContract, ContractPriceTier.contract_id == CustomerContract.id)
        .where(
            CustomerContract.customer_id == customer_id,
            ContractPriceTier.sku == sku,
            ContractPriceTier.min_quantity <= quantity,
        )
        .order_by(ContractPriceTier.min_quantity.desc())
    )

    eligible_tiers = db.scalars(stmt).all()
    if not eligible_tiers:
        return None

    contract_ids = {tier.contract_id for tier in eligible_tiers}
    if len(contract_ids) > 1:
        formatted_ids = ", ".join(sorted(contract_ids))
        raise PricingConflictError(
            f"Ambiguous contract pricing: multiple contracts ({formatted_ids}) "
            f"found for customer '{customer_id}' and SKU '{sku}'"
        )

    return eligible_tiers[0]


def _format_money(cents: int) -> str:
    """Format integer cents deterministically without floating-point conversion."""
    sign = "-" if cents < 0 else ""
    dollars, remainder = divmod(abs(cents), 100)
    return f"{sign}${dollars}.{remainder:02d}"


def _ensure_unresolved_discrepancy(
    draft: OrderDraft,
    line: DraftLineItem | None,
    discrepancy_type: str,
    expected_value: str,
    requested_value: str,
    explanation: str,
) -> None:
    """Append a missing blocker by ORM line identity, preserving existing history.

    Relationship identity also distinguishes newly created lines before their
    primary keys are assigned. The caller keeps this operation under no_autoflush.
    """
    if any(
        flag.resolution_state == "Unresolved"
        and flag.discrepancy_type == discrepancy_type
        and flag.line_item is line
        for flag in draft.discrepancy_flags
    ):
        return
    draft.discrepancy_flags.append(DiscrepancyFlag(
        line_item=line, discrepancy_type=discrepancy_type,
        severity="Blocking", resolution_state="Unresolved",
        expected_value=expected_value, requested_value=requested_value,
        explanation=explanation,
    ))


def evaluate_clean_draft(db: Session, draft: OrderDraft) -> OrderDraft:
    """Evaluate P2 discrepancies and readiness with deterministic integer rules.

    Recompute contract pricing, append missing unresolved blockers and set draft
    readiness without modifying discrepancy history. Customer-source arithmetic
    stays independent from contract pricing. The caller owns flush, commit and
    rollback; evaluation never calls AI or external services. Incomplete input
    remains Needs Review even when no discrepancy can be established.
    """
    if draft.status in ("Approved", "Rejected"):
        raise ValueError("Cannot evaluate a terminal draft")

    def present(value: str | None) -> bool:
        return isinstance(value, str) and bool(value.strip())

    def valid_money(value: int | None) -> bool:
        return type(value) is int and value >= 0

    # Evaluation must not flush an unverified SKU hint into a foreign key.
    with db.no_autoflush:
        known_customer = present(draft.customer_id) and db.scalar(
            select(CustomerContract.id).where(CustomerContract.customer_id == draft.customer_id).limit(1)
        ) is not None
        active_lines = [line for line in draft.line_items if line.status == "Active"]
        clean = bool(
            known_customer and present(draft.customer_name_extracted)
            and present(draft.po_number_extracted) and active_lines
        )
        for line in active_lines:
            # Clear stale derived values before any failed lookup can reuse them.
            line.contract_price_cents = None
            line.calculated_line_total_cents = 0
            quantity = line.extracted_quantity
            valid_quantity = type(quantity) is int and quantity >= 1
            product = db.get(CatalogProduct, line.matched_sku) if present(line.matched_sku) else None
            catalog_resolved = product is not None and (
                line.sku_resolution_source == "OPERATOR_SELECTED"
                or (
                    line.sku_resolution_source == "AI_HIGH_CONFIDENCE"
                    and line.sku_confidence == "High"
                )
            )
            if not catalog_resolved:
                clean = False
                # Missing internal resolution metadata alone is incomplete,
                # rather than evidence of an ambiguous/unrecognized mapping.
                if product is None or line.sku_confidence in ("Ambiguous", "Unrecognized"):
                    _ensure_unresolved_discrepancy(
                        draft, line, "CatalogMatchingMismatch",
                        "Valid catalog SKU resolved by high-confidence AI or operator selection",
                        f"SKU: {line.matched_sku or 'missing'}; confidence: {line.sku_confidence or 'missing'}; "
                        f"resolution: {line.sku_resolution_source or 'missing'}",
                        "The SKU is missing, absent from the catalog, or ambiguous/unrecognized "
                        "without a valid operator selection. Explicit catalog resolution is required.",
                    )

            if product is not None and type(quantity) is int:
                violations = []
                if quantity < product.min_order_quantity:
                    violations.append(f"Quantity {quantity} is below MOQ {product.min_order_quantity}")
                if quantity % product.package_increment != 0:
                    violations.append(
                        f"Quantity {quantity} is not a multiple of package increment {product.package_increment}"
                    )
                if violations:
                    _ensure_unresolved_discrepancy(
                        draft, line, "QuantityOrPackagingBreach",
                        f"MOQ: {product.min_order_quantity}; package increment: {product.package_increment}",
                        f"Qty: {quantity}", "; ".join(violations) + ".",
                    )

            source_price_valid = valid_money(line.extracted_unit_price_cents)
            source_total_valid = valid_money(line.extracted_line_total_cents)
            if not (
                present(line.customer_description) and valid_quantity
                and source_price_valid and source_total_valid
            ):
                clean = False
            if valid_quantity and source_price_valid and source_total_valid:
                source_line_total = calculate_line_total_cents(quantity, line.extracted_unit_price_cents)
                if source_line_total != line.extracted_line_total_cents:
                    _ensure_unresolved_discrepancy(
                        draft, line, "ArithmeticMismatch",
                        _format_money(source_line_total), _format_money(line.extracted_line_total_cents),
                        f"Customer-stated line total differs from quantity {quantity} multiplied by "
                        f"customer-stated unit price {_format_money(line.extracted_unit_price_cents)}.",
                    )

            resolvable = (
                known_customer and valid_quantity and catalog_resolved
            )
            if not resolvable:
                clean = False
                continue
            try:
                tier = select_contract_price_tier(
                    db, draft.customer_id, line.matched_sku, line.extracted_quantity,
                )
            except PricingConflictError:
                # No arbitrary contract choice or commercial fallback is made.
                clean = False
                continue
            if tier is None:
                clean = False
                continue
            line.contract_price_cents = tier.tier_price_cents
            line.calculated_line_total_cents = calculate_line_total_cents(
                line.extracted_quantity, line.contract_price_cents,
            )
            if source_price_valid and line.extracted_unit_price_cents != line.contract_price_cents:
                _ensure_unresolved_discrepancy(
                    draft, line, "PriceMismatch",
                    _format_money(line.contract_price_cents), _format_money(line.extracted_unit_price_cents),
                    "Customer-stated unit price differs from the authoritative customer contract tier "
                    f"price for SKU {line.matched_sku} at quantity {quantity}.",
                )
        draft.calculated_subtotal_cents = calculate_subtotal_cents(
            line.calculated_line_total_cents for line in active_lines
        )
        if draft.extracted_order_total_cents is not None:
            if not valid_money(draft.extracted_order_total_cents) or not all(
                valid_money(line.extracted_line_total_cents) for line in active_lines
            ):
                clean = False
            elif active_lines:
                source_order_total = calculate_subtotal_cents(
                    line.extracted_line_total_cents for line in active_lines
                )
                if source_order_total != draft.extracted_order_total_cents:
                    _ensure_unresolved_discrepancy(
                        draft, None, "ArithmeticMismatch",
                        _format_money(source_order_total), _format_money(draft.extracted_order_total_cents),
                        "Customer-stated order total differs from the sum of customer-stated "
                        "line totals for active lines.",
                    )
        if any(flag.resolution_state == "Unresolved" for flag in draft.discrepancy_flags):
            clean = False
        draft.status = "Ready for Approval" if clean else "Needs Review"
    return draft
