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

from app.models.entities import CatalogProduct, ContractPriceTier, CustomerContract, OrderDraft


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


def evaluate_clean_draft(db: Session, draft: OrderDraft) -> OrderDraft:
    """Evaluate P1 clean readiness using T010 prices and integer arithmetic.

    Mutate derived line values and draft status only; the caller owns flush,
    commit and rollback. No discrepancy rows, date-based contract selection,
    MOQ/packaging checks or stated-total discrepancy checks are implemented here.
    Non-clean input remains Needs Review even when no discrepancy rows exist.
    """
    if draft.status in ("Approved", "Rejected"):
        raise ValueError("Cannot evaluate a terminal draft")

    def present(value: str | None) -> bool:
        return isinstance(value, str) and bool(value.strip())

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
            resolvable = (
                known_customer and type(line.extracted_quantity) is int and line.extracted_quantity >= 1
                and present(line.matched_sku) and line.sku_confidence == "High"
                and line.sku_resolution_source == "AI_HIGH_CONFIDENCE"
                and db.get(CatalogProduct, line.matched_sku) is not None
            )
            if not resolvable:
                clean = False
                continue
            try:
                tier = select_contract_price_tier(
                    db, draft.customer_id, line.matched_sku, line.extracted_quantity,
                )
            except PricingConflictError:
                # No contract choice or commercial fallback is made in P1.
                clean = False
                continue
            if tier is None:
                clean = False
                continue
            line.contract_price_cents = tier.tier_price_cents
            line.calculated_line_total_cents = calculate_line_total_cents(
                line.extracted_quantity, line.contract_price_cents,
            )
            if (
                not present(line.customer_description) or line.extracted_line_total_cents is None
                or line.extracted_unit_price_cents != line.contract_price_cents
            ):
                clean = False
        draft.calculated_subtotal_cents = calculate_subtotal_cents(
            line.calculated_line_total_cents for line in active_lines
        )
        if any(flag.resolution_state == "Unresolved" for flag in draft.discrepancy_flags):
            clean = False
        draft.status = "Ready for Approval" if clean else "Needs Review"
    return draft
