"""T014: focused tier selection and exact integer-cents money contracts."""

from datetime import date
from decimal import Decimal, localcontext

import pytest

from app.models.entities import CatalogProduct, ContractPriceTier, CustomerContract
from app.models.schemas import cents_to_decimal, decimal_to_cents
from app.services.reconciliation import (
    PricingConflictError,
    calculate_line_total_cents,
    calculate_subtotal_cents,
    select_contract_price_tier,
)


@pytest.fixture
def pricing_db(db_session):
    """Independent, intentionally non-demo prices expose hard-coded seed lookups."""
    for sku in ("SKU-TEST", "SKU-OTHER"):
        db_session.add(CatalogProduct(
            sku=sku, name=sku, category="Test", unit_of_measure="Each",
            base_price_cents=9999, min_order_quantity=1, package_increment=1,
        ))
    for contract_id, customer in (("contract-a", "customer-a"), ("contract-b", "customer-b")):
        db_session.add(CustomerContract(
            id=contract_id, customer_id=customer, customer_name=customer,
            valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31),
        ))
    db_session.flush()
    return db_session


def _add_tiers(db, tiers, *, contract_id="contract-a", sku="SKU-TEST"):
    for threshold, price in tiers:
        db.add(ContractPriceTier(
            contract_id=contract_id, sku=sku,
            min_quantity=threshold, tier_price_cents=price,
        ))
    db.flush()


@pytest.mark.parametrize("quantity,threshold,price", [
    (1, 1, 2500), (10, 10, 2300), (37, 10, 2300),
    (50, 50, 2100), (100, 50, 2100),
])
def test_selects_maximum_eligible_quantity_threshold(pricing_db, quantity, threshold, price):
    # Deliberately insert out of order: selection must depend on quantity.
    _add_tiers(pricing_db, [(50, 2100), (1, 2500), (10, 2300)])

    tier = select_contract_price_tier(pricing_db, "customer-a", "SKU-TEST", quantity)

    assert tier is not None
    assert tier.min_quantity == threshold
    assert tier.tier_price_cents == price
    assert tier.contract_id == "contract-a"


@pytest.mark.parametrize("tiers", [[], [(10, 2300), (50, 2100)]], ids=["no-tiers", "below-all-tiers"])
def test_no_eligible_tier_returns_none_without_any_price_fallback(pricing_db, tiers):
    _add_tiers(pricing_db, tiers)
    # An eligible tier for another customer must not become a fallback either.
    _add_tiers(pricing_db, [(1, 100)], contract_id="contract-b")

    assert select_contract_price_tier(pricing_db, "customer-a", "SKU-TEST", 1) is None


def test_other_customer_cannot_influence_selected_tier(pricing_db):
    _add_tiers(pricing_db, [(1, 2500), (10, 2300)])
    _add_tiers(pricing_db, [(37, 1)], contract_id="contract-b")

    tier = select_contract_price_tier(pricing_db, "customer-a", "SKU-TEST", 37)

    assert tier.contract_id == "contract-a"
    assert (tier.min_quantity, tier.tier_price_cents) == (10, 2300)


def test_other_sku_cannot_influence_selected_tier(pricing_db):
    _add_tiers(pricing_db, [(1, 2500), (10, 2300)])
    _add_tiers(pricing_db, [(37, 1)], sku="SKU-OTHER")

    tier = select_contract_price_tier(pricing_db, "customer-a", "SKU-TEST", 37)

    assert tier.sku == "SKU-TEST"
    assert (tier.min_quantity, tier.tier_price_cents) == (10, 2300)


@pytest.mark.parametrize("other_threshold,other_price", [
    (20, 2100), (10, 2100), (10, 2300),
], ids=["different-threshold", "same-threshold-different-price", "same-threshold-same-price"])
def test_eligible_tiers_in_multiple_contracts_fail_closed(
    pricing_db, other_threshold, other_price,
):
    pricing_db.add(CustomerContract(
        id="contract-overlap", customer_id="customer-a", customer_name="customer-a",
        valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31),
    ))
    pricing_db.flush()
    _add_tiers(pricing_db, [(10, 2300)])
    _add_tiers(pricing_db, [(other_threshold, other_price)], contract_id="contract-overlap")

    with pytest.raises(PricingConflictError):
        select_contract_price_tier(pricing_db, "customer-a", "SKU-TEST", 37)


def test_integer_cents_line_and_subtotal_arithmetic_is_exact():
    line_total = calculate_line_total_cents(10, 2500)
    subtotal = calculate_subtotal_cents([25000, 10000, 499])

    assert line_total == 25000
    assert subtotal == 35499
    assert type(line_total) is int
    assert type(subtotal) is int
    assert calculate_line_total_cents(3, 1) == 3


@pytest.mark.parametrize("amount,cents", [("25.00", 2500), ("0.01", 1), ("0.29", 29)])
def test_decimal_cents_boundary_is_exact_even_with_low_decimal_precision(amount, cents):
    with localcontext() as context:
        context.prec = 2
        actual_cents = decimal_to_cents(Decimal(amount))
        actual_decimal = cents_to_decimal(cents)

    assert actual_cents == cents
    assert type(actual_cents) is int
    assert actual_decimal == Decimal(amount)
    assert isinstance(actual_decimal, Decimal)
    assert actual_decimal.as_tuple().exponent == -2


@pytest.mark.parametrize("invalid", [25.0, 0.1 + 0.2, True, Decimal("0.001")])
def test_decimal_boundary_rejects_floats_booleans_and_subcent_money(invalid):
    with pytest.raises(ValueError):
        decimal_to_cents(invalid)


@pytest.mark.parametrize("invalid", [12.5, True, -1])
def test_cents_boundary_rejects_noninteger_or_negative_money(invalid):
    with pytest.raises(ValueError):
        cents_to_decimal(invalid)


@pytest.mark.parametrize("quantity,error", [(0, ValueError), (-1, ValueError), (True, TypeError)])
def test_invalid_quantity_is_rejected_by_selection_and_arithmetic(pricing_db, quantity, error):
    with pytest.raises(error):
        select_contract_price_tier(pricing_db, "customer-a", "SKU-TEST", quantity)
    with pytest.raises(error):
        calculate_line_total_cents(quantity, 2500)


@pytest.mark.parametrize("price,error", [(-1, ValueError), (12.5, TypeError), (True, TypeError)])
def test_line_arithmetic_rejects_invalid_price(price, error):
    with pytest.raises(error):
        calculate_line_total_cents(10, price)


@pytest.mark.parametrize("invalid,error", [(-1, ValueError), (True, TypeError), (12.5, TypeError)])
def test_subtotal_rejects_invalid_elements(invalid, error):
    with pytest.raises(error):
        calculate_subtotal_cents([25000, invalid])
