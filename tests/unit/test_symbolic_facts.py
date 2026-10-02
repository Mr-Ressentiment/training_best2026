"""Unit tests for app.symbolic.facts: draft_to_facts adapter.

Validates the pure ORM OrderDraft -> flat symbolic facts mapping:
- clean_acme and discrepancy_apex fixture expectations
- Positive MOQ-gap calculation on resolved SKU
- Purity and read-only behavior (no DB writes, no dirty session, no SQL queries on pre-loaded graphs)
- Determinism, scalar-only output typing, and State(facts=...) compatibility
- Line and draft arithmetic delta sign conventions and None semantics
- Exclusion of removed lines
- Edge cases for missing, zero, or invalid values
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session, joinedload

from app.cli import seed_baseline
from app.models.entities import (
    CatalogProduct,
    DraftLineItem,
    OrderDraft,
    PurchaseOrderDocument,
)
from app.services.ai_provider import FixtureAIProvider
from app.services.document_parser import parse_document
from app.services.order_service import ingest_order
from app.symbolic.engine import Rule, State, rules_stage
from app.symbolic.facts import FactValue, draft_to_facts


# -----------------------------------------------------------------------------
# Test Helpers
# -----------------------------------------------------------------------------

def _load_persisted_draft(db: Session, draft_id: str) -> OrderDraft:
    """Load an OrderDraft with eager-loaded line items and catalog products."""
    return db.scalars(
        select(OrderDraft)
        .options(
            joinedload(OrderDraft.line_items).joinedload(DraftLineItem.product)
        )
        .where(OrderDraft.id == draft_id)
    ).unique().one()


def _is_valid_scalar(value: Any) -> bool:
    """Check that value is strictly a scalar (int, float, str, bool) or None."""
    if value is None:
        return True
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float, str)):
        return True
    return False


# -----------------------------------------------------------------------------
# Acceptance Tests on Existing Fixtures
# -----------------------------------------------------------------------------

def test_draft_to_facts_clean_acme(db_session: Session, po_clean_acme_path):
    """Test facts extraction on clean Acme fixture with 0 discrepancies."""
    seed_baseline(db_session)
    db_session.commit()

    doc = parse_document(
        po_clean_acme_path.read_bytes(),
        filename=po_clean_acme_path.name,
        content_type="text/plain",
    )
    provider = FixtureAIProvider(fixture_id="fixture-clean-acme")
    draft = ingest_order(db_session, document=doc, provider=provider)

    loaded_draft = _load_persisted_draft(db_session, draft.id)
    facts = draft_to_facts(loaded_draft)

    # Draft-level facts
    assert facts["draft.id"] == loaded_draft.id
    assert facts["draft.customer_id"] == "CUST-ACME"
    assert facts["draft.status"] == "Ready for Approval"
    assert facts["draft.active_line_count"] == 2
    assert facts["draft.extracted_order_total_cents"] is None
    # No stated order total in clean_acme fixture -> None (not calculated from calculated_subtotal_cents)
    assert facts["draft.arith_delta_cents"] is None

    # Line 1: SKU-WRAP-18, Qty 10, stated unit 2500c, contract 2500c
    assert facts["line.1.line_number"] == 1
    assert facts["line.1.status"] == "Active"
    assert facts["line.1.matched_sku"] == "SKU-WRAP-18"
    assert facts["line.1.sku_confidence"] == "High"
    assert facts["line.1.sku_resolution_source"] == "AI_HIGH_CONFIDENCE"
    assert facts["line.1.extracted_quantity"] == 10
    assert facts["line.1.extracted_unit_price_cents"] == 2500
    assert facts["line.1.contract_price_cents"] == 2500
    assert facts["line.1.extracted_line_total_cents"] == 25000
    assert facts["line.1.price_deviation_pct"] == 0.0
    assert facts["line.1.moq_gap"] == 0  # MOQ 5 <= 10
    assert facts["line.1.package_increment"] == 1
    assert facts["line.1.arith_delta_cents"] == 0  # 25000 - (10 * 2500) == 0

    # Line 2: SKU-WRAP-15, Qty 5, stated unit 2000c, contract 2000c
    assert facts["line.2.line_number"] == 2
    assert facts["line.2.status"] == "Active"
    assert facts["line.2.matched_sku"] == "SKU-WRAP-15"
    assert facts["line.2.sku_confidence"] == "High"
    assert facts["line.2.sku_resolution_source"] == "AI_HIGH_CONFIDENCE"
    assert facts["line.2.extracted_quantity"] == 5
    assert facts["line.2.extracted_unit_price_cents"] == 2000
    assert facts["line.2.contract_price_cents"] == 2000
    assert facts["line.2.extracted_line_total_cents"] == 10000
    assert facts["line.2.price_deviation_pct"] == 0.0
    assert facts["line.2.moq_gap"] == 0  # MOQ 5 <= 5
    assert facts["line.2.package_increment"] == 1
    assert facts["line.2.arith_delta_cents"] == 0  # 10000 - (5 * 2000) == 0


def test_draft_to_facts_discrepancy_apex(db_session: Session, po_discrepancy_apex_path):
    """Test facts extraction on discrepancy Apex fixture with PriceMismatch and Ambiguous line."""
    seed_baseline(db_session)
    db_session.commit()

    doc = parse_document(
        po_discrepancy_apex_path.read_bytes(),
        filename=po_discrepancy_apex_path.name,
        content_type="text/plain",
    )
    provider = FixtureAIProvider(fixture_id="fixture-discrepancy-apex")
    draft = ingest_order(db_session, document=doc, provider=provider)

    loaded_draft = _load_persisted_draft(db_session, draft.id)
    facts = draft_to_facts(loaded_draft)

    # Draft-level facts
    assert facts["draft.id"] == loaded_draft.id
    assert facts["draft.customer_id"] == "CUST-APEX"
    assert facts["draft.status"] == "Needs Review"
    assert facts["draft.active_line_count"] == 2
    assert facts["draft.extracted_order_total_cents"] is None
    assert facts["draft.arith_delta_cents"] is None

    # Line 1: SKU-WRAP-18, Qty 10, stated unit 1800c, contract 2200c
    assert facts["line.1.line_number"] == 1
    assert facts["line.1.status"] == "Active"
    assert facts["line.1.matched_sku"] == "SKU-WRAP-18"
    assert facts["line.1.sku_confidence"] == "High"
    assert facts["line.1.sku_resolution_source"] == "AI_HIGH_CONFIDENCE"
    assert facts["line.1.extracted_quantity"] == 10
    assert facts["line.1.extracted_unit_price_cents"] == 1800
    assert facts["line.1.contract_price_cents"] == 2200
    assert facts["line.1.extracted_line_total_cents"] == 18000
    # 100 * (1800 - 2200) / 2200 == -18.181818...
    assert facts["line.1.price_deviation_pct"] == pytest.approx(-18.181818, rel=1e-4)
    assert facts["line.1.moq_gap"] == 0  # MOQ 5 <= 10
    assert facts["line.1.package_increment"] == 1
    assert facts["line.1.arith_delta_cents"] == 0  # 18000 - (10 * 1800) == 0

    # Line 2: Ambiguous, Qty 2, stated unit 2000c, total 4000c, no resolved SKU
    assert facts["line.2.line_number"] == 2
    assert facts["line.2.status"] == "Active"
    assert facts["line.2.matched_sku"] is None
    assert facts["line.2.sku_confidence"] == "Ambiguous"
    assert facts["line.2.sku_resolution_source"] == "NONE"
    assert facts["line.2.extracted_quantity"] == 2
    assert facts["line.2.extracted_unit_price_cents"] == 2000
    assert facts["line.2.contract_price_cents"] is None
    assert facts["line.2.extracted_line_total_cents"] == 4000
    # SKU unresolved -> price_deviation_pct and moq_gap MUST be None (never guessed from candidates)
    assert facts["line.2.price_deviation_pct"] is None
    assert facts["line.2.moq_gap"] is None
    assert facts["line.2.package_increment"] is None
    # Stated line arithmetic can still be evaluated: 4000 - (2 * 2000) == 0
    assert facts["line.2.arith_delta_cents"] == 0


def test_draft_to_facts_discrepancy_apex_positive_moq_gap(db_session: Session, po_discrepancy_apex_path):
    """Test positive MOQ gap (moq_gap == 3) when Line 2 in Apex is resolved to SKU-WRAP-15 (MOQ 5, Qty 2)."""
    seed_baseline(db_session)
    db_session.commit()

    doc = parse_document(
        po_discrepancy_apex_path.read_bytes(),
        filename=po_discrepancy_apex_path.name,
        content_type="text/plain",
    )
    provider = FixtureAIProvider(fixture_id="fixture-discrepancy-apex")
    draft = ingest_order(db_session, document=doc, provider=provider)

    loaded_draft = _load_persisted_draft(db_session, draft.id)

    # In test setup ONLY: explicitly resolve Line 2 to SKU-WRAP-15
    line2 = next(l for l in loaded_draft.line_items if l.line_number == 2)
    prod_wrap_15 = db_session.get(CatalogProduct, "SKU-WRAP-15")
    assert prod_wrap_15 is not None
    assert prod_wrap_15.min_order_quantity == 5

    line2.matched_sku = "SKU-WRAP-15"
    line2.sku_resolution_source = "OPERATOR_SELECTED"
    line2.sku_confidence = "High"
    line2.product = prod_wrap_15

    # Run adapter
    facts = draft_to_facts(loaded_draft)

    assert facts["line.2.matched_sku"] == "SKU-WRAP-15"
    assert facts["line.2.extracted_quantity"] == 2
    # MOQ 5 - Qty 2 = 3
    assert facts["line.2.moq_gap"] == 3
    assert facts["line.2.package_increment"] == 1


# -----------------------------------------------------------------------------
# Purity & Non-Mutation Tests
# -----------------------------------------------------------------------------

def test_draft_to_facts_purity_no_db_changes_and_no_sql(db_session: Session, po_clean_acme_path):
    """Test that draft_to_facts emits NO SQL queries and leaves DB session clean."""
    seed_baseline(db_session)
    db_session.commit()

    doc = parse_document(
        po_clean_acme_path.read_bytes(),
        filename=po_clean_acme_path.name,
        content_type="text/plain",
    )
    draft = ingest_order(db_session, document=doc, provider=FixtureAIProvider(fixture_id="fixture-clean-acme"))
    loaded_draft = _load_persisted_draft(db_session, draft.id)

    # Snapshot session state prior to adapter call
    assert len(db_session.new) == 0
    assert len(db_session.dirty) == 0
    assert len(db_session.deleted) == 0

    # Attach SQL execution listener to verify zero queries during adapter execution
    queries_executed: list[str] = []

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        queries_executed.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", before_cursor_execute)

    try:
        facts = draft_to_facts(loaded_draft)
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)

    # Assert zero SQL statements were executed
    assert queries_executed == [], f"Unexpected SQL executed: {queries_executed}"

    # Assert session remains untouched
    assert len(db_session.new) == 0
    assert len(db_session.dirty) == 0
    assert len(db_session.deleted) == 0

    # Assert input draft attributes were not mutated
    assert loaded_draft.customer_id == "CUST-ACME"
    assert loaded_draft.status == "Ready for Approval"
    assert len(loaded_draft.line_items) == 2


def test_draft_to_facts_deterministic_and_flat_scalars(db_session: Session, po_clean_acme_path):
    """Test that adapter output is strictly a flat dictionary of scalar values and idempotent."""
    seed_baseline(db_session)
    db_session.commit()

    doc = parse_document(
        po_clean_acme_path.read_bytes(),
        filename=po_clean_acme_path.name,
        content_type="text/plain",
    )
    draft = ingest_order(db_session, document=doc, provider=FixtureAIProvider(fixture_id="fixture-clean-acme"))
    loaded_draft = _load_persisted_draft(db_session, draft.id)

    facts1 = draft_to_facts(loaded_draft)
    facts2 = draft_to_facts(loaded_draft)

    # Idempotent & deterministic
    assert facts1 == facts2

    # Strictly flat dictionary of scalar values
    for k, v in facts1.items():
        assert isinstance(k, str), f"Key {k!r} is not str"
        assert _is_valid_scalar(v), f"Value {v!r} for key {k!r} is not a valid scalar"
        assert not isinstance(v, (dict, list, tuple, set, Decimal)), f"Non-scalar {type(v)} for key {k!r}"


def test_draft_to_facts_state_engine_compatibility(db_session: Session, po_clean_acme_path):
    """Test that returned facts are directly usable by State and Rule forward chaining."""
    seed_baseline(db_session)
    db_session.commit()

    doc = parse_document(
        po_clean_acme_path.read_bytes(),
        filename=po_clean_acme_path.name,
        content_type="text/plain",
    )
    draft = ingest_order(db_session, document=doc, provider=FixtureAIProvider(fixture_id="fixture-clean-acme"))
    loaded_draft = _load_persisted_draft(db_session, draft.id)

    facts = draft_to_facts(loaded_draft)

    # Initialize symbolic State
    state = State(facts=facts)
    assert state.facts == facts
    assert state.verdict is None

    # Test Rule evaluation against facts
    rule_clean = Rule(
        id="R_CLEAN_CHECK",
        when=lambda f: f.get("line.1.price_deviation_pct") == 0.0 and f.get("line.1.moq_gap") == 0,
        then={"clean_line_1": True},
        priority=10,
    )
    stage = rules_stage("audit", [rule_clean])
    new_state = stage(state)

    assert new_state.facts.get("clean_line_1") is True
    assert len(new_state.trace) == 1
    assert new_state.trace[0].rule_id == "R_CLEAN_CHECK"


# -----------------------------------------------------------------------------
# Formula, Sign Convention & Semantic Tests
# -----------------------------------------------------------------------------

def test_price_deviation_pct_sign_conventions():
    """Verify price_deviation_pct formulas and sign conventions:

    100 * (stated - contract) / contract
    - stated > contract => positive
    - stated < contract => negative
    - stated == contract => 0.0
    - missing/invalid/contract <= 0 => None
    """
    # Exact match
    line_exact = DraftLineItem(
        status="Active", line_number=1,
        extracted_unit_price_cents=2500, contract_price_cents=2500,
    )
    draft = OrderDraft(line_items=[line_exact])
    assert draft_to_facts(draft)["line.1.price_deviation_pct"] == 0.0

    # Customer price higher than contract (stated 3000 vs contract 2500 => +20.0%)
    line_higher = DraftLineItem(
        status="Active", line_number=1,
        extracted_unit_price_cents=3000, contract_price_cents=2500,
    )
    draft = OrderDraft(line_items=[line_higher])
    assert draft_to_facts(draft)["line.1.price_deviation_pct"] == pytest.approx(20.0)

    # Customer price lower than contract (stated 2000 vs contract 2500 => -20.0%)
    line_lower = DraftLineItem(
        status="Active", line_number=1,
        extracted_unit_price_cents=2000, contract_price_cents=2500,
    )
    draft = OrderDraft(line_items=[line_lower])
    assert draft_to_facts(draft)["line.1.price_deviation_pct"] == pytest.approx(-20.0)

    # Missing contract price => None
    line_no_contract = DraftLineItem(
        status="Active", line_number=1,
        extracted_unit_price_cents=2000, contract_price_cents=None,
    )
    draft = OrderDraft(line_items=[line_no_contract])
    assert draft_to_facts(draft)["line.1.price_deviation_pct"] is None

    # Zero contract price (prevent division by zero) => None
    line_zero_contract = DraftLineItem(
        status="Active", line_number=1,
        extracted_unit_price_cents=2000, contract_price_cents=0,
    )
    draft = OrderDraft(line_items=[line_zero_contract])
    assert draft_to_facts(draft)["line.1.price_deviation_pct"] is None

    # Missing stated unit price => None
    line_no_stated = DraftLineItem(
        status="Active", line_number=1,
        extracted_unit_price_cents=None, contract_price_cents=2000,
    )
    draft = OrderDraft(line_items=[line_no_stated])
    assert draft_to_facts(draft)["line.1.price_deviation_pct"] is None


def test_moq_gap_semantics_and_candidate_sku_exclusion():
    """Verify moq_gap semantics and that candidate SKUs are never used for MOQ gap."""
    product = CatalogProduct(sku="SKU-1", min_order_quantity=10)

    # Resolved SKU with quantity >= MOQ => moq_gap == 0
    line_ok = DraftLineItem(
        status="Active", line_number=1, matched_sku="SKU-1",
        extracted_quantity=15, product=product,
    )
    draft = OrderDraft(line_items=[line_ok])
    assert draft_to_facts(draft)["line.1.moq_gap"] == 0

    # Resolved SKU with quantity < MOQ => moq_gap == shortfall
    line_short = DraftLineItem(
        status="Active", line_number=1, matched_sku="SKU-1",
        extracted_quantity=7, product=product,
    )
    draft = OrderDraft(line_items=[line_short])
    assert draft_to_facts(draft)["line.1.moq_gap"] == 3

    # Ambiguous candidate SKU without operator resolution => moq_gap MUST be None
    line_ambiguous = DraftLineItem(
        status="Active", line_number=1, matched_sku=None,
        sku_confidence="Ambiguous", sku_resolution_source="NONE",
        extracted_quantity=5,
        candidate_skus_json='[{"sku": "SKU-1", "score": 0.9}]',
    )
    draft = OrderDraft(line_items=[line_ambiguous])
    assert draft_to_facts(draft)["line.1.moq_gap"] is None

    # Missing product unloaded => moq_gap is None
    line_no_prod = DraftLineItem(
        status="Active", line_number=1, matched_sku="SKU-1",
        extracted_quantity=5, product=None,
    )
    draft = OrderDraft(line_items=[line_no_prod])
    assert draft_to_facts(draft)["line.1.moq_gap"] is None

    # Missing quantity => moq_gap is None
    line_no_qty = DraftLineItem(
        status="Active", line_number=1, matched_sku="SKU-1",
        extracted_quantity=None, product=product,
    )
    draft = OrderDraft(line_items=[line_no_qty])
    assert draft_to_facts(draft)["line.1.moq_gap"] is None


def test_line_and_order_arith_delta_cents():
    """Verify line and order level arithmetic delta formulas and sign conventions.

    line.arith_delta_cents = extracted_line_total_cents - (quantity * unit_price)
    draft.arith_delta_cents = extracted_order_total_cents - sum(line_totals)
    """
    # Line arithmetic matches exactly => 0
    line1 = DraftLineItem(
        status="Active", line_number=1,
        extracted_quantity=10, extracted_unit_price_cents=2500,
        extracted_line_total_cents=25000,
    )
    # Line arithmetic stated > calculated (stated 10500 vs 5 * 2000 = 10000 => +500)
    line2 = DraftLineItem(
        status="Active", line_number=2,
        extracted_quantity=5, extracted_unit_price_cents=2000,
        extracted_line_total_cents=10500,
    )
    # Line arithmetic stated < calculated (stated 3500 vs 2 * 2000 = 4000 => -500)
    line3 = DraftLineItem(
        status="Active", line_number=3,
        extracted_quantity=2, extracted_unit_price_cents=2000,
        extracted_line_total_cents=3500,
    )

    # Order total stated exactly matches sum of active line totals: 25000 + 10500 + 3500 = 39000
    draft_match = OrderDraft(
        extracted_order_total_cents=39000,
        line_items=[line1, line2, line3],
    )
    facts_match = draft_to_facts(draft_match)
    assert facts_match["line.1.arith_delta_cents"] == 0
    assert facts_match["line.2.arith_delta_cents"] == 500
    assert facts_match["line.3.arith_delta_cents"] == -500
    assert facts_match["draft.arith_delta_cents"] == 0

    # Order total stated higher: 40000 vs 39000 => +1000
    draft_over = OrderDraft(
        extracted_order_total_cents=40000,
        line_items=[line1, line2, line3],
    )
    assert draft_to_facts(draft_over)["draft.arith_delta_cents"] == 1000

    # Order total stated lower: 38000 vs 39000 => -1000
    draft_under = OrderDraft(
        extracted_order_total_cents=38000,
        line_items=[line1, line2, line3],
    )
    assert draft_to_facts(draft_under)["draft.arith_delta_cents"] == -1000

    # Order total absent => draft.arith_delta_cents MUST be None
    draft_no_total = OrderDraft(
        extracted_order_total_cents=None,
        calculated_subtotal_cents=39000,  # Must NOT fallback to calculated_subtotal_cents
        line_items=[line1, line2, line3],
    )
    assert draft_to_facts(draft_no_total)["draft.arith_delta_cents"] is None

    # One line total missing => draft.arith_delta_cents MUST be None
    line_incomplete = DraftLineItem(
        status="Active", line_number=4,
        extracted_quantity=1, extracted_unit_price_cents=1000,
        extracted_line_total_cents=None,
    )
    draft_incomplete = OrderDraft(
        extracted_order_total_cents=40000,
        line_items=[line1, line_incomplete],
    )
    assert draft_to_facts(draft_incomplete)["draft.arith_delta_cents"] is None


def test_removed_lines_are_excluded_from_facts():
    """Verify that lines with status == 'Removed' do not appear in facts and do not affect totals."""
    line1 = DraftLineItem(
        status="Active", line_number=1,
        extracted_quantity=10, extracted_unit_price_cents=2500,
        extracted_line_total_cents=25000,
    )
    line2_removed = DraftLineItem(
        status="Removed", line_number=2,
        extracted_quantity=99, extracted_unit_price_cents=9999,
        extracted_line_total_cents=999999,
    )
    line3 = DraftLineItem(
        status="Active", line_number=3,
        extracted_quantity=5, extracted_unit_price_cents=2000,
        extracted_line_total_cents=10000,
    )

    draft = OrderDraft(
        extracted_order_total_cents=35000,
        line_items=[line1, line2_removed, line3],
    )
    facts = draft_to_facts(draft)

    # Active line count is 2 (line 1 and line 3)
    assert facts["draft.active_line_count"] == 2

    # Line facts are indexed 1 and 2 for the active lines
    assert facts["line.1.line_number"] == 1
    assert facts["line.2.line_number"] == 3
    assert "line.3.line_number" not in facts

    # Order total arithmetic sums line 1 and line 3 only: 35000 - (25000 + 10000) == 0
    assert facts["draft.arith_delta_cents"] == 0


def test_empty_line_items_draft():
    """Verify draft_to_facts on a draft with no line items."""
    draft = OrderDraft(
        customer_id="CUST-EMPTY",
        status="Ingested",
        extracted_order_total_cents=None,
        line_items=[],
    )
    facts = draft_to_facts(draft)

    assert facts["draft.customer_id"] == "CUST-EMPTY"
    assert facts["draft.status"] == "Ingested"
    assert facts["draft.active_line_count"] == 0
    assert facts["draft.extracted_order_total_cents"] is None
    assert facts["draft.arith_delta_cents"] is None
    # No line facts
    assert not any(k.startswith("line.") for k in facts)
