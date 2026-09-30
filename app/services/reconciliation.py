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

from app.models.entities import ContractPriceTier, CustomerContract


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
    2. Select the eligible tier having the maximum min_quantity.
    3. If no tier has min_quantity <= Q, returns None (explicit no-tier result).

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

    top_tier = eligible_tiers[0]

    # Verify no conflicting tiers at the same maximum threshold across contracts
    for tier in eligible_tiers[1:]:
        if tier.min_quantity < top_tier.min_quantity:
            break
        if tier.tier_price_cents != top_tier.tier_price_cents:
            raise PricingConflictError(
                f"Conflicting pricing tiers found for customer '{customer_id}' and SKU '{sku}' "
                f"at min_quantity {top_tier.min_quantity}: "
                f"{top_tier.tier_price_cents} cents vs {tier.tier_price_cents} cents"
            )

    return top_tier
