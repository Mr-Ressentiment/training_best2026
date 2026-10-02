"""Unit tests for symbolic reconciliation rules in app.symbolic.rules.reconciliation.

Validates the deterministic Rule evaluators for:
- ADV-PRC-01 (PriceMismatch)
- ADV-PKG-01 (QuantityOrPackagingBreach)
- ADV-ARITH-LINE-01 (Line ArithmeticMismatch)
- ADV-ARITH-ORDER-01 (Order ArithmeticMismatch)

Test matrix:
- Clean Acme PO: no rules fire, trace is empty
- PriceMismatch: fires on discrepancy_apex (1800 vs 2200 cents), 1-cent exactness, None handling
- QuantityOrPackagingBreach: MOQ gap vs package increment multiplicity, unresolved SKU exclusion
- ArithmeticMismatch: line-level and order-level scope, 1-cent exactness, None handling
- Concurrent firing of all rules in single stage without halting
- Non-empty why and exact TraceStep matching
- Immutability of input facts and scalar-only output
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.cli import seed_baseline
from app.models.entities import CatalogProduct, DraftLineItem, OrderDraft
from app.services.ai_provider import FixtureAIProvider
from app.services.document_parser import parse_document
from app.services.order_service import ingest_order
from app.symbolic.engine import State, rules_stage
from app.symbolic.facts import draft_to_facts
from app.symbolic.rules.reconciliation import (
    ADV_ARITH_LINE_01,
    ADV_ARITH_ORDER_01,
    ADV_PKG_01,
    ADV_PRC_01,
    RECONCILIATION_RULES,
)


# -----------------------------------------------------------------------------
# Test Helpers
# -----------------------------------------------------------------------------

def _load_persisted_draft(db: Session, draft_id: str) -> OrderDraft:
    return db.scalars(
        select(OrderDraft)
        .options(
            joinedload(OrderDraft.line_items).joinedload(DraftLineItem.product)
        )
        .where(OrderDraft.id == draft_id)
    ).unique().one()


def _run_reconciliation(facts: dict) -> State:
    stage = rules_stage("reconciliation", list(RECONCILIATION_RULES))
    return stage(State(facts=facts))


# -----------------------------------------------------------------------------
# Acceptance Tests on Existing Fixtures
# -----------------------------------------------------------------------------

def test_rules_clean_acme_fixture_no_mismatches(db_session: Session, po_clean_acme_path):
    """Clean Acme fixture has 0 discrepancies; no reconciliation rules fire."""
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

    state = _run_reconciliation(facts)

    # No rule should fire
    assert len(state.trace) == 0
    assert not state.facts.get("reconciliation.price_mismatch")
    assert not state.facts.get("reconciliation.quantity_or_packaging_breach")
    assert not state.facts.get("reconciliation.arithmetic_mismatch")
    assert not state.facts.get("reconciliation.line_arithmetic_mismatch")
    assert not state.facts.get("reconciliation.order_arithmetic_mismatch")
    assert state.halted is False
    assert state.verdict is None


def test_rules_discrepancy_apex_fixture(db_session: Session, po_discrepancy_apex_path):
    """Discrepancy Apex fixture triggers ADV-PRC-01 (1800 vs 2200 cents), but not packaging or arithmetic."""
    seed_baseline(db_session)
    db_session.commit()

    doc = parse_document(
        po_discrepancy_apex_path.read_bytes(),
        filename=po_discrepancy_apex_path.name,
        content_type="text/plain",
    )
    draft = ingest_order(db_session, document=doc, provider=FixtureAIProvider(fixture_id="fixture-discrepancy-apex"))
    loaded_draft = _load_persisted_draft(db_session, draft.id)
    facts = draft_to_facts(loaded_draft)

    state = _run_reconciliation(facts)

    # Exactly ADV-PRC-01 fires
    fired_ids = [step.rule_id for step in state.trace]
    assert fired_ids == [ADV_PRC_01]

    assert state.facts.get("reconciliation.price_mismatch") is True
    # Line 2 is ambiguous -> moq_gap and package_increment are None, no false packaging breach
    assert not state.facts.get("reconciliation.quantity_or_packaging_breach")
    assert not state.facts.get("reconciliation.arithmetic_mismatch")
    assert not state.facts.get("reconciliation.line_arithmetic_mismatch")
    assert not state.facts.get("reconciliation.order_arithmetic_mismatch")


def test_rules_discrepancy_apex_with_resolved_line2(db_session: Session, po_discrepancy_apex_path):
    """When Line 2 in Apex is resolved to SKU-WRAP-15 (Qty 2, MOQ 5), both ADV-PRC-01 and ADV-PKG-01 fire."""
    seed_baseline(db_session)
    db_session.commit()

    doc = parse_document(
        po_discrepancy_apex_path.read_bytes(),
        filename=po_discrepancy_apex_path.name,
        content_type="text/plain",
    )
    draft = ingest_order(db_session, document=doc, provider=FixtureAIProvider(fixture_id="fixture-discrepancy-apex"))
    loaded_draft = _load_persisted_draft(db_session, draft.id)

    # Resolve Line 2
    line2 = next(l for l in loaded_draft.line_items if l.line_number == 2)
    prod = db_session.get(CatalogProduct, "SKU-WRAP-15")
    line2.matched_sku = "SKU-WRAP-15"
    line2.sku_resolution_source = "OPERATOR_SELECTED"
    line2.sku_confidence = "High"
    line2.product = prod

    facts = draft_to_facts(loaded_draft)
    state = _run_reconciliation(facts)

    fired_ids = set(step.rule_id for step in state.trace)
    assert fired_ids == {ADV_PRC_01, ADV_PKG_01}

    assert state.facts.get("reconciliation.price_mismatch") is True
    assert state.facts.get("reconciliation.quantity_or_packaging_breach") is True
    assert not state.facts.get("reconciliation.arithmetic_mismatch")


# -----------------------------------------------------------------------------
# Detailed Rule Logic & Boundary Tests
# -----------------------------------------------------------------------------

def test_price_mismatch_rule_boundary_and_exact_one_cent():
    """ADV-PRC-01 compares integer cents directly, detecting differences down to 1 cent."""
    # Exact match -> False
    facts_match = {
        "draft.active_line_count": 1,
        "line.1.extracted_unit_price_cents": 2500,
        "line.1.contract_price_cents": 2500,
    }
    state = _run_reconciliation(facts_match)
    assert not state.facts.get("reconciliation.price_mismatch")
    assert len(state.trace) == 0

    # 1 cent higher -> True
    facts_plus_one = {
        "draft.active_line_count": 1,
        "line.1.extracted_unit_price_cents": 2501,
        "line.1.contract_price_cents": 2500,
    }
    state = _run_reconciliation(facts_plus_one)
    assert state.facts.get("reconciliation.price_mismatch") is True
    assert state.trace[0].rule_id == ADV_PRC_01

    # 1 cent lower -> True
    facts_minus_one = {
        "draft.active_line_count": 1,
        "line.1.extracted_unit_price_cents": 2499,
        "line.1.contract_price_cents": 2500,
    }
    state = _run_reconciliation(facts_minus_one)
    assert state.facts.get("reconciliation.price_mismatch") is True

    # Missing contract price -> False (no base price fallback, no false positive)
    facts_no_contract = {
        "draft.active_line_count": 1,
        "line.1.extracted_unit_price_cents": 2500,
        "line.1.contract_price_cents": None,
    }
    state = _run_reconciliation(facts_no_contract)
    assert not state.facts.get("reconciliation.price_mismatch")

    # Missing stated price -> False
    facts_no_stated = {
        "draft.active_line_count": 1,
        "line.1.extracted_unit_price_cents": None,
        "line.1.contract_price_cents": 2500,
    }
    state = _run_reconciliation(facts_no_stated)
    assert not state.facts.get("reconciliation.price_mismatch")


def test_packaging_rule_moq_shortfall_and_unresolved_sku():
    """ADV-PKG-01 triggers on positive moq_gap, but ignores moq_gap == 0 and moq_gap is None."""
    # moq_gap == 0, valid packaging -> False
    facts_ok = {
        "draft.active_line_count": 1,
        "line.1.moq_gap": 0,
        "line.1.extracted_quantity": 10,
        "line.1.package_increment": 5,
    }
    state = _run_reconciliation(facts_ok)
    assert not state.facts.get("reconciliation.quantity_or_packaging_breach")

    # moq_gap == 3 -> True
    facts_shortfall = {
        "draft.active_line_count": 1,
        "line.1.moq_gap": 3,
        "line.1.extracted_quantity": 2,
        "line.1.package_increment": 1,
    }
    state = _run_reconciliation(facts_shortfall)
    assert state.facts.get("reconciliation.quantity_or_packaging_breach") is True
    assert state.trace[0].rule_id == ADV_PKG_01

    # moq_gap is None (unresolved SKU) -> False
    facts_unresolved = {
        "draft.active_line_count": 1,
        "line.1.moq_gap": None,
        "line.1.extracted_quantity": 2,
        "line.1.package_increment": None,
    }
    state = _run_reconciliation(facts_unresolved)
    assert not state.facts.get("reconciliation.quantity_or_packaging_breach")


def test_packaging_rule_package_increment_multiplicity():
    """ADV-PKG-01 triggers on package increment violation even when MOQ is fulfilled."""
    # Quantity 7 with package increment 6 (MOQ fulfilled) -> True (7 % 6 != 0)
    facts_violating = {
        "draft.active_line_count": 1,
        "line.1.moq_gap": 0,
        "line.1.extracted_quantity": 7,
        "line.1.package_increment": 6,
    }
    state = _run_reconciliation(facts_violating)
    assert state.facts.get("reconciliation.quantity_or_packaging_breach") is True
    assert state.trace[0].rule_id == ADV_PKG_01

    # Quantity 12 with package increment 6 -> False (12 % 6 == 0)
    facts_multiple = {
        "draft.active_line_count": 1,
        "line.1.moq_gap": 0,
        "line.1.extracted_quantity": 12,
        "line.1.package_increment": 6,
    }
    state = _run_reconciliation(facts_multiple)
    assert not state.facts.get("reconciliation.quantity_or_packaging_breach")

    # Missing package_increment fact -> False (not assumed to be a breach)
    facts_missing_pkg = {
        "draft.active_line_count": 1,
        "line.1.moq_gap": 0,
        "line.1.extracted_quantity": 7,
        "line.1.package_increment": None,
    }
    state = _run_reconciliation(facts_missing_pkg)
    assert not state.facts.get("reconciliation.quantity_or_packaging_breach")


def test_line_arithmetic_mismatch_rule():
    """ADV-ARITH-LINE-01 triggers on non-zero line.N.arith_delta_cents, including 1-cent variance."""
    # Zero delta -> False
    facts_zero = {
        "draft.active_line_count": 1,
        "line.1.arith_delta_cents": 0,
    }
    state = _run_reconciliation(facts_zero)
    assert not state.facts.get("reconciliation.arithmetic_mismatch")
    assert not state.facts.get("reconciliation.line_arithmetic_mismatch")

    # +1 cent -> True
    facts_plus_one = {
        "draft.active_line_count": 1,
        "line.1.arith_delta_cents": 1,
    }
    state = _run_reconciliation(facts_plus_one)
    assert state.facts.get("reconciliation.arithmetic_mismatch") is True
    assert state.facts.get("reconciliation.line_arithmetic_mismatch") is True
    assert not state.facts.get("reconciliation.order_arithmetic_mismatch")
    assert state.trace[0].rule_id == ADV_ARITH_LINE_01

    # -500 cents -> True
    facts_minus = {
        "draft.active_line_count": 1,
        "line.1.arith_delta_cents": -500,
    }
    state = _run_reconciliation(facts_minus)
    assert state.facts.get("reconciliation.arithmetic_mismatch") is True
    assert state.facts.get("reconciliation.line_arithmetic_mismatch") is True

    # None delta -> False
    facts_none = {
        "draft.active_line_count": 1,
        "line.1.arith_delta_cents": None,
    }
    state = _run_reconciliation(facts_none)
    assert not state.facts.get("reconciliation.arithmetic_mismatch")
    assert not state.facts.get("reconciliation.line_arithmetic_mismatch")


def test_order_arithmetic_mismatch_rule():
    """ADV-ARITH-ORDER-01 triggers on non-zero draft.arith_delta_cents."""
    # Zero delta -> False
    facts_zero = {
        "draft.arith_delta_cents": 0,
    }
    state = _run_reconciliation(facts_zero)
    assert not state.facts.get("reconciliation.arithmetic_mismatch")
    assert not state.facts.get("reconciliation.order_arithmetic_mismatch")

    # Non-zero (+1000 cents) -> True
    facts_over = {
        "draft.arith_delta_cents": 1000,
    }
    state = _run_reconciliation(facts_over)
    assert state.facts.get("reconciliation.arithmetic_mismatch") is True
    assert state.facts.get("reconciliation.order_arithmetic_mismatch") is True
    assert not state.facts.get("reconciliation.line_arithmetic_mismatch")
    assert state.trace[0].rule_id == ADV_ARITH_ORDER_01

    # Non-zero (-1 cent) -> True
    facts_under = {
        "draft.arith_delta_cents": -1,
    }
    state = _run_reconciliation(facts_under)
    assert state.facts.get("reconciliation.arithmetic_mismatch") is True
    assert state.facts.get("reconciliation.order_arithmetic_mismatch") is True

    # None delta -> False
    facts_none = {
        "draft.arith_delta_cents": None,
    }
    state = _run_reconciliation(facts_none)
    assert not state.facts.get("reconciliation.arithmetic_mismatch")
    assert not state.facts.get("reconciliation.order_arithmetic_mismatch")


# -----------------------------------------------------------------------------
# Concurrent Rule Execution, Trace & Purity Tests
# -----------------------------------------------------------------------------

def test_concurrent_all_rules_firing_in_single_stage():
    """All 4 rules can fire concurrently in a single rules_stage without halting."""
    facts = {
        "draft.active_line_count": 3,
        # Line 1: triggers ADV-PRC-01
        "line.1.extracted_unit_price_cents": 1800,
        "line.1.contract_price_cents": 2200,
        "line.1.moq_gap": 0,
        "line.1.extracted_quantity": 10,
        "line.1.package_increment": 5,
        "line.1.arith_delta_cents": 0,
        # Line 2: triggers ADV-PKG-01 (7 % 6 != 0)
        "line.2.extracted_unit_price_cents": 1000,
        "line.2.contract_price_cents": 1000,
        "line.2.moq_gap": 0,
        "line.2.extracted_quantity": 7,
        "line.2.package_increment": 6,
        "line.2.arith_delta_cents": 0,
        # Line 3: triggers ADV-ARITH-LINE-01
        "line.3.extracted_unit_price_cents": 500,
        "line.3.contract_price_cents": 500,
        "line.3.moq_gap": 0,
        "line.3.extracted_quantity": 2,
        "line.3.package_increment": 1,
        "line.3.arith_delta_cents": 50,
        # Draft level: triggers ADV-ARITH-ORDER-01
        "draft.arith_delta_cents": 100,
    }

    state = _run_reconciliation(facts)

    # All 4 rules fired
    fired_ids = set(step.rule_id for step in state.trace)
    assert fired_ids == {
        ADV_PRC_01,
        ADV_PKG_01,
        ADV_ARITH_LINE_01,
        ADV_ARITH_ORDER_01,
    }

    # All output facts set
    assert state.facts.get("reconciliation.price_mismatch") is True
    assert state.facts.get("reconciliation.quantity_or_packaging_breach") is True
    assert state.facts.get("reconciliation.arithmetic_mismatch") is True
    assert state.facts.get("reconciliation.line_arithmetic_mismatch") is True
    assert state.facts.get("reconciliation.order_arithmetic_mismatch") is True

    # Pipeline did not halt
    assert state.halted is False
    assert state.verdict is None


def test_rule_metadata_why_and_trace_detail():
    """Every rule defines a non-empty why that matches TraceStep.detail."""
    for rule in RECONCILIATION_RULES:
        assert isinstance(rule.why, str) and len(rule.why.strip()) > 0
        assert rule.verdict is None  # Advisory rules do not halt

    facts = {
        "draft.active_line_count": 1,
        "line.1.extracted_unit_price_cents": 100,
        "line.1.contract_price_cents": 200,
    }
    state = _run_reconciliation(facts)
    assert len(state.trace) == 1
    step = state.trace[0]
    assert step.rule_id == ADV_PRC_01
    assert step.detail == next(r.why for r in RECONCILIATION_RULES if r.id == ADV_PRC_01)


def test_input_facts_immutability():
    """Rules execution does not mutate the original facts dictionary."""
    original_facts = {
        "draft.active_line_count": 1,
        "line.1.extracted_unit_price_cents": 1800,
        "line.1.contract_price_cents": 2200,
    }
    facts_copy = dict(original_facts)

    state = _run_reconciliation(original_facts)

    assert original_facts == facts_copy
    assert "reconciliation.price_mismatch" not in original_facts
    assert state.facts.get("reconciliation.price_mismatch") is True
