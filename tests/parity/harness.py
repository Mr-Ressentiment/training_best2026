"""Shared harness for legacy reconciliation engine vs symbolic rules parity checks.

Provides:
- Evaluation of OrderDraft instances against both engines:
  * Legacy: app.services.reconciliation:evaluate_clean_draft
  * Symbolic: app.symbolic.rules.reconciliation:RECONCILIATION_RULES
- Fixture test cases loader and runner for suitable fixtures.
- Manifest of suitable and excluded fixture files with rationale.
- Stratified pseudo-random order generator (seed-controlled).
- Summary computation and Markdown parity report formatting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import random
from typing import Any, Sequence

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, joinedload, sessionmaker
from sqlalchemy.pool import StaticPool

from app.cli import seed_baseline
from app.models.entities import (
    Base,
    CatalogProduct,
    ContractPriceTier,
    CustomerContract,
    DraftLineItem,
    OrderDraft,
)
from app.services.ai_provider import FixtureAIProvider
from app.services.document_parser import parse_document
from app.services.order_service import ingest_order
from app.services.reconciliation import (
    evaluate_clean_draft,
    select_contract_price_tier,
)
from app.symbolic.engine import State, rules_stage
from app.symbolic.facts import draft_to_facts
from app.symbolic.rules.reconciliation import RECONCILIATION_RULES


# -----------------------------------------------------------------------------
# Constants & Manifests
# -----------------------------------------------------------------------------

CATEGORIES: tuple[str, ...] = (
    "PriceMismatch",
    "QuantityOrPackagingBreach",
    "ArithmeticMismatch.line",
    "ArithmeticMismatch.order",
)

SUITABLE_FIXTURES: tuple[tuple[str, str, str], ...] = (
    ("fixture-clean-acme", "tests/fixtures/po_clean_acme.txt", "clean"),
    ("fixture-discrepancy-apex", "tests/fixtures/po_discrepancy_apex.txt", "price_mismatch"),
    ("fixture-ambiguous-apex", "tests/fixtures/po_ambiguous_apex.txt", "unresolved_ambiguous"),
)

EXCLUDED_FIXTURES: tuple[dict[str, str], ...] = (
    {
        "filename": "po_unextractable.pdf",
        "path": "tests/fixtures/po_unextractable.pdf",
        "reason": (
            "Document parsing raises UnextractableTextError because the PDF contains "
            "no extractable text layers. Ingestion fails before an OrderDraft can be produced."
        ),
    },
)


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

@dataclass
class CaseResult:
    """Parity check result for an individual purchase order draft."""

    case_id: str
    source: str  # "fixture" or "random"
    strata: str  # e.g. "clean", "price_mismatch", "moq_shortfall", etc.
    oracle_flags: dict[str, bool]
    symbolic_flags: dict[str, bool]
    unmodeled_oracle_flags: list[str] = field(default_factory=list)
    matches: dict[str, bool] = field(default_factory=dict)
    all_matched: bool = True
    details: str = ""


@dataclass
class CategoryMetrics:
    """Confusion matrix and parity rate for a single discrepancy category."""

    category: str
    total: int = 0
    true_positives: int = 0  # Both flagged True
    true_negatives: int = 0  # Both flagged False
    false_positives: int = 0  # Symbolic flagged True, Oracle was False
    false_negatives: int = 0  # Symbolic flagged False, Oracle was True
    matches: int = 0
    mismatches: int = 0

    @property
    def agreement_pct(self) -> float:
        if self.total == 0:
            return 100.0
        return (self.matches / self.total) * 100.0


@dataclass
class ParitySummary:
    """Aggregated summary of parity testing across all evaluated cases."""

    total_cases: int
    matched_cases: int
    mismatched_cases: int
    category_metrics: dict[str, CategoryMetrics]
    strata_counts: dict[str, int]
    unmodeled_counts: dict[str, int]
    seed: int | None = None
    evaluated_at: str = ""

    @property
    def overall_agreement_pct(self) -> float:
        if self.total_cases == 0:
            return 100.0
        return (self.matched_cases / self.total_cases) * 100.0


# -----------------------------------------------------------------------------
# Database Session Helper
# -----------------------------------------------------------------------------

def create_isolated_session() -> Session:
    """Create a fully isolated in-memory SQLite session seeded with baseline data."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = session_factory()
    seed_baseline(session)
    session.commit()
    return session


# -----------------------------------------------------------------------------
# Evaluation Engine
# -----------------------------------------------------------------------------

def evaluate_draft_case(
    db: Session,
    draft: OrderDraft,
    case_id: str,
    strata: str,
    source: str = "random",
    details: str = "",
) -> CaseResult:
    """Evaluate an OrderDraft against both legacy reconciliation and symbolic rules.

    Normalizes discrepancies across the four common categories:
    1. PriceMismatch
    2. QuantityOrPackagingBreach
    3. ArithmeticMismatch.line
    4. ArithmeticMismatch.order

    Captures non-covered flags (such as CatalogMatchingMismatch) separately.
    Cleans up pending database state via db.rollback().
    """
    try:
        if draft not in db:
            db.add(draft)

        evaluate_clean_draft(db, draft)

        oracle_flags = {
            "PriceMismatch": any(
                f.discrepancy_type == "PriceMismatch" and f.resolution_state == "Unresolved"
                for f in draft.discrepancy_flags
            ),
            "QuantityOrPackagingBreach": any(
                f.discrepancy_type == "QuantityOrPackagingBreach" and f.resolution_state == "Unresolved"
                for f in draft.discrepancy_flags
            ),
            "ArithmeticMismatch.line": any(
                f.discrepancy_type == "ArithmeticMismatch"
                and f.line_item is not None
                and f.resolution_state == "Unresolved"
                for f in draft.discrepancy_flags
            ),
            "ArithmeticMismatch.order": any(
                f.discrepancy_type == "ArithmeticMismatch"
                and f.line_item is None
                and f.resolution_state == "Unresolved"
                for f in draft.discrepancy_flags
            ),
        }

        unmodeled = [
            f.discrepancy_type
            for f in draft.discrepancy_flags
            if f.resolution_state == "Unresolved"
            and f.discrepancy_type not in (
                "PriceMismatch",
                "QuantityOrPackagingBreach",
                "ArithmeticMismatch",
            )
        ]

        facts = draft_to_facts(draft)
        stage = rules_stage("reconciliation", list(RECONCILIATION_RULES))
        state = stage(State(facts=facts))

        symbolic_flags = {
            "PriceMismatch": bool(state.facts.get("reconciliation.price_mismatch", False)),
            "QuantityOrPackagingBreach": bool(
                state.facts.get("reconciliation.quantity_or_packaging_breach", False)
            ),
            "ArithmeticMismatch.line": bool(
                state.facts.get("reconciliation.line_arithmetic_mismatch", False)
            ),
            "ArithmeticMismatch.order": bool(
                state.facts.get("reconciliation.order_arithmetic_mismatch", False)
            ),
        }

        matches = {cat: oracle_flags[cat] == symbolic_flags[cat] for cat in CATEGORIES}
        all_matched = all(matches.values())

        return CaseResult(
            case_id=case_id,
            source=source,
            strata=strata,
            oracle_flags=oracle_flags,
            symbolic_flags=symbolic_flags,
            unmodeled_oracle_flags=unmodeled,
            matches=matches,
            all_matched=all_matched,
            details=details,
        )
    finally:
        db.rollback()


# -----------------------------------------------------------------------------
# Fixture Cases Runner
# -----------------------------------------------------------------------------

def run_fixture_cases(
    db: Session,
    repo_root: Path | None = None,
) -> list[CaseResult]:
    """Execute parity check across all suitable repository fixture documents."""
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent.parent

    results: list[CaseResult] = []
    for fix_id, rel_path, strata in SUITABLE_FIXTURES:
        file_path = repo_root / rel_path
        if not file_path.exists():
            raise FileNotFoundError(f"Fixture file not found: {file_path}")

        doc = parse_document(
            file_path.read_bytes(),
            filename=file_path.name,
            content_type="text/plain",
        )
        provider = FixtureAIProvider(fixture_id=fix_id)
        draft = ingest_order(db, document=doc, provider=provider)

        loaded_draft = db.scalars(
            select(OrderDraft)
            .options(
                joinedload(OrderDraft.line_items).joinedload(DraftLineItem.product),
                joinedload(OrderDraft.discrepancy_flags),
            )
            .where(OrderDraft.id == draft.id)
        ).unique().one()

        res = evaluate_draft_case(
            db=db,
            draft=loaded_draft,
            case_id=fix_id,
            strata=strata,
            source="fixture",
            details=f"File: {rel_path}",
        )
        results.append(res)

    return results


# -----------------------------------------------------------------------------
# Stratified Random Order Generator
# -----------------------------------------------------------------------------

def generate_random_cases(
    db: Session,
    count: int = 200,
    seed: int = 20261002,
) -> list[CaseResult]:
    """Generate and evaluate count pseudo-random stratified order drafts using fixed seed."""
    rng = random.Random(seed)

    # Stratification distribution for count=200:
    # 25 clean, 25 price_mismatch, 25 moq_shortfall, 25 pkg_breach,
    # 25 arith_line, 25 arith_order, 25 mixed, 15 unresolved_ambiguous, 10 removed_lines
    strata_targets = {
        "clean": 25,
        "price_mismatch": 25,
        "moq_shortfall": 25,
        "pkg_breach": 25,
        "arith_line": 25,
        "arith_order": 25,
        "mixed": 25,
        "unresolved_ambiguous": 15,
        "removed_lines": 10,
    }

    if count != 200:
        # Scale proportionally if non-standard count requested
        total_std = sum(strata_targets.values())
        scaled = {k: max(1, int(round(v * count / total_std))) for k, v in strata_targets.items()}
        diff = count - sum(scaled.values())
        scaled["clean"] += diff
        strata_targets = scaled

    customers = ["CUST-ACME", "CUST-APEX"]
    wrap_skus = ["SKU-WRAP-18", "SKU-WRAP-15"]
    pkg_skus = ["SKU-TAPE-03", "SKU-TAPE-02", "SKU-GLOVE-NIT", "SKU-LBL-THERM"]

    def _create_line(
        line_num: int,
        sku: str | None,
        qty: int | None,
        unit_price: int | None,
        line_total: int | None,
        status: str = "Active",
        conf: str | None = "High",
        res: str = "AI_HIGH_CONFIDENCE",
    ) -> DraftLineItem:
        prod = db.get(CatalogProduct, sku) if sku else None
        return DraftLineItem(
            line_number=line_num,
            status=status,
            customer_description=f"Description for {sku or 'unmatched'}",
            matched_sku=sku,
            sku_confidence=conf,
            sku_resolution_source=res,
            extracted_quantity=qty,
            extracted_unit_price_cents=unit_price,
            extracted_line_total_cents=line_total,
            product=prod,
        )

    results: list[CaseResult] = []

    with db.no_autoflush:
        # 1. Clean Orders
        for i in range(strata_targets["clean"]):
            cust = customers[i % 2]
            num_lines = rng.randint(1, 3)
            lines = []
            tot = 0
            for l_idx in range(1, num_lines + 1):
                sku = wrap_skus[l_idx % 2]
                qty = rng.choice([10, 20, 25, 50])
                tier = select_contract_price_tier(db, cust, sku, qty)
                assert tier is not None
                price = tier.tier_price_cents
                lt = qty * price
                tot += lt
                lines.append(_create_line(l_idx, sku, qty, price, lt))
            draft = OrderDraft(
                customer_id=cust,
                customer_name_extracted="Clean Customer",
                po_number_extracted=f"PO-CLN-{i:03d}",
                status="Ingested",
                extracted_order_total_cents=tot,
                line_items=lines,
            )
            res = evaluate_draft_case(
                db, draft, case_id=f"rnd-clean-{i+1:02d}", strata="clean", source="random"
            )
            results.append(res)

        # 2. Price Mismatch Orders
        for i in range(strata_targets["price_mismatch"]):
            cust = customers[i % 2]
            num_lines = rng.randint(1, 3)
            lines = []
            tot = 0
            for l_idx in range(1, num_lines + 1):
                sku = wrap_skus[l_idx % 2]
                qty = rng.choice([10, 20, 25, 50])
                tier = select_contract_price_tier(db, cust, sku, qty)
                assert tier is not None
                base = tier.tier_price_cents
                if l_idx == 1:
                    if i % 3 == 0:
                        delta = 1  # Exact 1 cent above
                    elif i % 3 == 1:
                        delta = -1  # Exact 1 cent below
                    else:
                        delta = rng.choice([50, -50, 200, -200, 500])
                    price = max(1, base + delta)
                else:
                    price = base
                lt = qty * price
                tot += lt
                lines.append(_create_line(l_idx, sku, qty, price, lt))
            draft = OrderDraft(
                customer_id=cust,
                customer_name_extracted="Price Mismatch Customer",
                po_number_extracted=f"PO-PRC-{i:03d}",
                status="Ingested",
                extracted_order_total_cents=tot,
                line_items=lines,
            )
            res = evaluate_draft_case(
                db,
                draft,
                case_id=f"rnd-price_mismatch-{i+1:02d}",
                strata="price_mismatch",
                source="random",
            )
            results.append(res)

        # 3. MOQ Shortfall Orders
        for i in range(strata_targets["moq_shortfall"]):
            cust = customers[i % 2]
            sku = wrap_skus[i % 2]
            prod = db.get(CatalogProduct, sku)
            assert prod is not None
            qty = rng.randint(1, prod.min_order_quantity - 1)
            tier = select_contract_price_tier(db, cust, sku, qty)
            assert tier is not None
            price = tier.tier_price_cents
            lt = qty * price
            lines = [_create_line(1, sku, qty, price, lt)]
            draft = OrderDraft(
                customer_id=cust,
                customer_name_extracted="MOQ Shortfall Customer",
                po_number_extracted=f"PO-MOQ-{i:03d}",
                status="Ingested",
                extracted_order_total_cents=lt,
                line_items=lines,
            )
            res = evaluate_draft_case(
                db,
                draft,
                case_id=f"rnd-moq_shortfall-{i+1:02d}",
                strata="moq_shortfall",
                source="random",
            )
            results.append(res)

        # 4. Package Increment Breach Orders
        for i in range(strata_targets["pkg_breach"]):
            cust = customers[i % 2]
            sku = pkg_skus[i % len(pkg_skus)]
            prod = db.get(CatalogProduct, sku)
            assert prod is not None
            base_qty = max(prod.min_order_quantity, prod.package_increment)
            offset = rng.randint(1, prod.package_increment - 1) if prod.package_increment > 1 else 1
            qty = base_qty + offset
            if qty % prod.package_increment == 0:
                qty += 1
            price = prod.base_price_cents
            lt = qty * price
            lines = [_create_line(1, sku, qty, price, lt)]
            draft = OrderDraft(
                customer_id=cust,
                customer_name_extracted="PKG Breach Customer",
                po_number_extracted=f"PO-PKG-{i:03d}",
                status="Ingested",
                extracted_order_total_cents=lt,
                line_items=lines,
            )
            res = evaluate_draft_case(
                db,
                draft,
                case_id=f"rnd-pkg_breach-{i+1:02d}",
                strata="pkg_breach",
                source="random",
            )
            results.append(res)

        # 5. Line Arithmetic Mismatch Orders
        for i in range(strata_targets["arith_line"]):
            cust = customers[i % 2]
            sku = wrap_skus[i % 2]
            qty = rng.choice([10, 20, 50])
            tier = select_contract_price_tier(db, cust, sku, qty)
            assert tier is not None
            price = tier.tier_price_cents
            correct_lt = qty * price
            if i % 3 == 0:
                delta = 1  # 1 cent off
            elif i % 3 == 1:
                delta = -1  # 1 cent off
            else:
                delta = rng.choice([10, -10, 50, -50, 100])
            lt = correct_lt + delta
            lines = [_create_line(1, sku, qty, price, lt)]
            draft = OrderDraft(
                customer_id=cust,
                customer_name_extracted="Line Arith Customer",
                po_number_extracted=f"PO-ARITHL-{i:03d}",
                status="Ingested",
                extracted_order_total_cents=lt,
                line_items=lines,
            )
            res = evaluate_draft_case(
                db,
                draft,
                case_id=f"rnd-arith_line-{i+1:02d}",
                strata="arith_line",
                source="random",
            )
            results.append(res)

        # 6. Order Arithmetic Mismatch Orders
        for i in range(strata_targets["arith_order"]):
            cust = customers[i % 2]
            sku = wrap_skus[i % 2]
            qty = rng.choice([10, 20, 50])
            tier = select_contract_price_tier(db, cust, sku, qty)
            assert tier is not None
            price = tier.tier_price_cents
            lt = qty * price
            lines = [_create_line(1, sku, qty, price, lt)]
            if i % 3 == 0:
                delta = 1  # 1 cent order mismatch
            elif i % 3 == 1:
                delta = -1  # 1 cent order mismatch
            else:
                delta = rng.choice([10, -10, 50, -50, 100])
            tot = lt + delta
            draft = OrderDraft(
                customer_id=cust,
                customer_name_extracted="Order Arith Customer",
                po_number_extracted=f"PO-ARITHO-{i:03d}",
                status="Ingested",
                extracted_order_total_cents=tot,
                line_items=lines,
            )
            res = evaluate_draft_case(
                db,
                draft,
                case_id=f"rnd-arith_order-{i+1:02d}",
                strata="arith_order",
                source="random",
            )
            results.append(res)

        # 7. Mixed Discrepancies Orders
        for i in range(strata_targets["mixed"]):
            cust = customers[i % 2]
            sku1 = "SKU-WRAP-18"
            sku2 = "SKU-TAPE-03"
            prod2 = db.get(CatalogProduct, sku2)
            assert prod2 is not None

            qty1 = 10
            tier1 = select_contract_price_tier(db, cust, sku1, qty1)
            assert tier1 is not None
            price1 = tier1.tier_price_cents + (1 if i % 2 == 0 else 50)
            line_arith_delta = 1 if (i % 4 == 0) else 0
            lt1 = (qty1 * price1) + line_arith_delta

            qty2 = 5 if (i % 3 == 0) else 3
            price2 = prod2.base_price_cents
            lt2 = qty2 * price2

            order_arith_delta = 10 if (i % 5 == 0) else 0
            tot = lt1 + lt2 + order_arith_delta

            lines = [
                _create_line(1, sku1, qty1, price1, lt1),
                _create_line(2, sku2, qty2, price2, lt2),
            ]
            draft = OrderDraft(
                customer_id=cust,
                customer_name_extracted="Mixed Customer",
                po_number_extracted=f"PO-MIX-{i:03d}",
                status="Ingested",
                extracted_order_total_cents=tot,
                line_items=lines,
            )
            res = evaluate_draft_case(
                db, draft, case_id=f"rnd-mixed-{i+1:02d}", strata="mixed", source="random"
            )
            results.append(res)

        # 8. Unresolved / Ambiguous / Missing Data Orders
        ambiguous_cases: list[tuple[str, dict[str, Any]]] = [
            ("missing_unit_price", {"unit_price": None}),
            ("missing_line_total", {"line_total": None}),
            ("missing_order_total", {"order_total": None}),
            ("ambiguous_sku", {"sku": None, "conf": "Ambiguous", "res": "NONE"}),
            ("unrecognized_sku", {"sku": None, "conf": "Unrecognized", "res": "NONE"}),
            ("unknown_cust", {}),
            ("missing_cust", {}),
            ("missing_qty", {"qty": None}),
            ("missing_price_and_total", {"unit_price": None, "line_total": None}),
            ("missing_line_total_with_order_total", {"line_total": None, "order_total": 5000}),
            ("two_line_ambiguous", {}),
            ("ambiguous_and_missing_order_total", {"sku": None, "conf": "Ambiguous", "res": "NONE", "order_total": None}),
            ("two_line_missing_price", {}),
            ("unknown_cust_pm", {}),
            ("ambiguous_line_arith", {}),
        ]

        for i in range(strata_targets["unresolved_ambiguous"]):
            kind, opts = ambiguous_cases[i % len(ambiguous_cases)]
            cust = "CUST-ACME"
            sku = opts.get("sku", "SKU-WRAP-18")
            qty = opts.get("qty", 10)
            conf = opts.get("conf", "High")
            res_src = opts.get("res", "AI_HIGH_CONFIDENCE")
            tier = select_contract_price_tier(db, cust, "SKU-WRAP-18", 10) if sku else None
            price = opts.get("unit_price", tier.tier_price_cents if tier else 2500)
            lt = opts.get("line_total", (qty * price) if (qty and price) else None)
            tot = opts.get("order_total", lt)

            if kind == "unknown_cust":
                draft = OrderDraft(
                    customer_id="CUST-NONEXISTENT",
                    customer_name_extracted="Unknown Customer",
                    po_number_extracted=f"PO-AMB-{i:03d}",
                    status="Ingested",
                    extracted_order_total_cents=tot,
                    line_items=[_create_line(1, sku, qty, price, lt, conf=conf, res=res_src)],
                )
            elif kind == "missing_cust":
                draft = OrderDraft(
                    customer_id=None,
                    customer_name_extracted="No Customer ID",
                    po_number_extracted=f"PO-AMB-{i:03d}",
                    status="Ingested",
                    extracted_order_total_cents=tot,
                    line_items=[_create_line(1, sku, qty, price, lt, conf=conf, res=res_src)],
                )
            elif kind == "two_line_ambiguous":
                lines = [
                    _create_line(1, "SKU-WRAP-18", 10, 2500, 25000),
                    _create_line(2, None, 2, 2000, 4000, conf="Ambiguous", res="NONE"),
                ]
                draft = OrderDraft(
                    customer_id=cust,
                    customer_name_extracted="Two Line Ambiguous Customer",
                    po_number_extracted=f"PO-AMB-{i:03d}",
                    status="Ingested",
                    extracted_order_total_cents=29000,
                    line_items=lines,
                )
            elif kind == "two_line_missing_price":
                lines = [
                    _create_line(1, "SKU-WRAP-18", 10, None, None),
                    _create_line(2, "SKU-WRAP-15", 5, 2000, 10000),
                ]
                draft = OrderDraft(
                    customer_id=cust,
                    customer_name_extracted="Missing Price Customer",
                    po_number_extracted=f"PO-AMB-{i:03d}",
                    status="Ingested",
                    extracted_order_total_cents=None,
                    line_items=lines,
                )
            elif kind == "unknown_cust_pm":
                draft = OrderDraft(
                    customer_id="CUST-GHOST",
                    customer_name_extracted="Ghost Customer",
                    po_number_extracted=f"PO-AMB-{i:03d}",
                    status="Ingested",
                    extracted_order_total_cents=30000,
                    line_items=[_create_line(1, "SKU-WRAP-18", 10, 3000, 30000)],
                )
            elif kind == "ambiguous_line_arith":
                lines = [
                    _create_line(1, None, 2, 2000, 4005, conf="Ambiguous", res="NONE"),
                ]
                draft = OrderDraft(
                    customer_id=cust,
                    customer_name_extracted="Ambiguous Line Arith Customer",
                    po_number_extracted=f"PO-AMB-{i:03d}",
                    status="Ingested",
                    extracted_order_total_cents=4005,
                    line_items=lines,
                )
            else:
                draft = OrderDraft(
                    customer_id=cust,
                    customer_name_extracted="Ambiguous Variant Customer",
                    po_number_extracted=f"PO-AMB-{i:03d}",
                    status="Ingested",
                    extracted_order_total_cents=tot,
                    line_items=[_create_line(1, sku, qty, price, lt, conf=conf, res=res_src)],
                )
            res = evaluate_draft_case(
                db,
                draft,
                case_id=f"rnd-unresolved_ambiguous-{i+1:02d}",
                strata="unresolved_ambiguous",
                source="random",
                details=f"Kind: {kind}",
            )
            results.append(res)

        # 9. Removed Lines Orders
        for i in range(strata_targets["removed_lines"]):
            cust = customers[i % 2]
            active_line = _create_line(1, "SKU-WRAP-18", 10, 2500, 25000, status="Active")
            if i == 0:
                rem_line = _create_line(2, "SKU-WRAP-18", 10, 9999, 99990, status="Removed")
                lines = [active_line, rem_line]
                tot = 25000
            elif i == 1:
                rem_line = _create_line(2, "SKU-WRAP-18", 10, 2500, 99999, status="Removed")
                lines = [active_line, rem_line]
                tot = 25000
            elif i == 2:
                rem_line = _create_line(2, "SKU-WRAP-18", 1, 2600, 2600, status="Removed")
                lines = [active_line, rem_line]
                tot = 25000
            elif i == 3:
                rem_line = _create_line(2, "SKU-TAPE-03", 5, 850, 4250, status="Removed")
                lines = [active_line, rem_line]
                tot = 25000
            elif i == 4:
                act = _create_line(1, "SKU-WRAP-18", 10, 2601, 26010, status="Active")
                rem = _create_line(2, "SKU-WRAP-18", 10, 2500, 25000, status="Removed")
                lines = [act, rem]
                tot = 26010
            elif i == 5:
                act = _create_line(1, "SKU-WRAP-18", 10, 2500, 25001, status="Active")
                rem = _create_line(2, "SKU-WRAP-18", 10, 2500, 25000, status="Removed")
                lines = [act, rem]
                tot = 25001
            elif i == 6:
                act = _create_line(1, "SKU-WRAP-18", 2, 2600, 5200, status="Active")
                rem = _create_line(2, "SKU-WRAP-18", 10, 2500, 25000, status="Removed")
                lines = [act, rem]
                tot = 5200
            elif i == 7:
                act = _create_line(1, "SKU-TAPE-03", 5, 850, 4250, status="Active")
                rem = _create_line(2, "SKU-WRAP-18", 10, 2500, 25000, status="Removed")
                lines = [act, rem]
                tot = 4250
            elif i == 8:
                act1 = _create_line(1, "SKU-WRAP-18", 10, 2500, 25000, status="Active")
                act2 = _create_line(2, "SKU-WRAP-15", 5, 2000, 10000, status="Active")
                rem = _create_line(3, "SKU-TAPE-03", 1, 9999, 9999, status="Removed")
                lines = [act1, act2, rem]
                tot = 35000
            else:
                act = _create_line(1, "SKU-WRAP-18", 10, 2500, 25000, status="Active")
                rem = _create_line(2, "SKU-WRAP-15", 5, 2000, 50000, status="Removed")
                lines = [act, rem]
                tot = 25000

            draft = OrderDraft(
                customer_id=cust,
                customer_name_extracted="Removed Lines Customer",
                po_number_extracted=f"PO-REM-{i:03d}",
                status="Ingested",
                extracted_order_total_cents=tot,
                line_items=lines,
            )
            res = evaluate_draft_case(
                db,
                draft,
                case_id=f"rnd-removed_lines-{i+1:02d}",
                strata="removed_lines",
                source="random",
            )
            results.append(res)

    return results


# -----------------------------------------------------------------------------
# Summary Computation
# -----------------------------------------------------------------------------

def compute_parity_summary(
    results: Sequence[CaseResult],
    seed: int | None = None,
) -> ParitySummary:
    """Compute confusion matrices, match counts, and strata aggregates across case results."""
    category_metrics = {cat: CategoryMetrics(category=cat) for cat in CATEGORIES}
    strata_counts: dict[str, int] = {}
    unmodeled_counts: dict[str, int] = {}
    matched_cases = 0

    for res in results:
        strata_counts[res.strata] = strata_counts.get(res.strata, 0) + 1
        for u in res.unmodeled_oracle_flags:
            unmodeled_counts[u] = unmodeled_counts.get(u, 0) + 1

        if res.all_matched:
            matched_cases += 1

        for cat in CATEGORIES:
            cm = category_metrics[cat]
            cm.total += 1
            o = res.oracle_flags.get(cat, False)
            s = res.symbolic_flags.get(cat, False)

            if o and s:
                cm.true_positives += 1
                cm.matches += 1
            elif not o and not s:
                cm.true_negatives += 1
                cm.matches += 1
            elif not o and s:
                cm.false_positives += 1
                cm.mismatches += 1
            else:
                cm.false_negatives += 1
                cm.mismatches += 1

    return ParitySummary(
        total_cases=len(results),
        matched_cases=matched_cases,
        mismatched_cases=len(results) - matched_cases,
        category_metrics=category_metrics,
        strata_counts=strata_counts,
        unmodeled_counts=unmodeled_counts,
        seed=seed,
        evaluated_at=datetime.now(timezone.utc).isoformat(),
    )


# -----------------------------------------------------------------------------
# Markdown Report Generation
# -----------------------------------------------------------------------------

def generate_markdown_report(
    summary: ParitySummary,
    results: Sequence[CaseResult],
    seed: int,
    report_path: Path | None = None,
) -> str:
    """Generate Markdown parity verification report and optionally write to report_path."""
    lines: list[str] = []

    lines.append("# Reconciliation Parity Report: Legacy Engine vs Symbolic Rules")
    lines.append("")
    lines.append(f"- **Generated At**: `{summary.evaluated_at}`")
    lines.append(f"- **Seed**: `{seed}`")
    lines.append(f"- **Total Cases**: `{summary.total_cases}`")
    lines.append(f"- **Matched Cases**: `{summary.matched_cases}`")
    lines.append(f"- **Mismatched Cases**: `{summary.mismatched_cases}`")
    lines.append(f"- **Overall Agreement**: `{summary.overall_agreement_pct:.2f}%`")
    lines.append("")

    lines.append("## 1. Executive Summary")
    lines.append("")
    if summary.mismatched_cases == 0:
        lines.append(
            "> [!NOTE]\n"
            "> **100% Deterministic Parity Confirmed** across all evaluated order fixtures "
            "and stratified pseudo-random order drafts for the four core discrepancy categories: "
            "`PriceMismatch`, `QuantityOrPackagingBreach`, `ArithmeticMismatch.line`, "
            "and `ArithmeticMismatch.order`."
        )
    else:
        lines.append(
            f"> [!WARNING]\n"
            f"> Detected {summary.mismatched_cases} mismatches out of {summary.total_cases} cases."
        )
    lines.append("")

    lines.append("## 2. Fixture Manifest & Evaluation")
    lines.append("")
    lines.append("### Evaluated Suitable Fixtures")
    lines.append("")
    lines.append("| Fixture ID | Source Document | Status | Parity Result |")
    lines.append("|---|---|---|---|")
    fixture_results = [r for r in results if r.source == "fixture"]
    for fix_res in fixture_results:
        status_text = "MATCH (100%)" if fix_res.all_matched else "MISMATCH"
        lines.append(f"| `{fix_res.case_id}` | `{fix_res.details}` | Evaluated | **{status_text}** |")
    lines.append("")

    lines.append("### Excluded Fixtures Manifest")
    lines.append("")
    lines.append("| Fixture File | Rationale for Exclusion |")
    lines.append("|---|---|")
    for excl in EXCLUDED_FIXTURES:
        lines.append(f"| `{excl['filename']}` | {excl['reason']} |")
    lines.append("")

    lines.append("## 3. Stratification Breakdown (Pseudo-Random Orders)")
    lines.append("")
    fixture_results = [r for r in results if r.source == "fixture"]
    random_results = [r for r in results if r.source == "random"]
    random_strata_counts: dict[str, int] = {}
    for r in random_results:
        random_strata_counts[r.strata] = random_strata_counts.get(r.strata, 0) + 1

    lines.append(f"Generated exactly {len(random_results)} pseudo-random orders "
                 f"stratified across 9 distinct categories using deterministic seed `{seed}`:")
    lines.append("")
    lines.append("| Stratum | Order Count | Description |")
    lines.append("|---|---|---|")
    lines.append(f"| `clean` | {random_strata_counts.get('clean', 0)} | Clean orders with contract pricing match, exact arithmetic, and no breaches |")
    lines.append(f"| `price_mismatch` | {random_strata_counts.get('price_mismatch', 0)} | Customer-stated unit price deviates from contract tier (including 1-cent boundaries) |")
    lines.append(f"| `moq_shortfall` | {random_strata_counts.get('moq_shortfall', 0)} | Quantity ordered is below catalog minimum order quantity (MOQ) |")
    lines.append(f"| `pkg_breach` | {random_strata_counts.get('pkg_breach', 0)} | Quantity satisfies MOQ but breaches package increment multiplicity |")
    lines.append(f"| `arith_line` | {random_strata_counts.get('arith_line', 0)} | Stated line total != quantity * unit price (including 1-cent boundaries) |")
    lines.append(f"| `arith_order` | {random_strata_counts.get('arith_order', 0)} | Stated order total != sum of stated line totals (including 1-cent boundaries) |")
    lines.append(f"| `mixed` | {random_strata_counts.get('mixed', 0)} | Multi-line combinations featuring multiple concurrent discrepancy types |")
    lines.append(f"| `unresolved_ambiguous` | {random_strata_counts.get('unresolved_ambiguous', 0)} | Incomplete inputs, missing totals, ambiguous SKUs, and unrecognized customers |")
    lines.append(f"| `removed_lines` | {random_strata_counts.get('removed_lines', 0)} | Multi-line drafts containing Active and Removed line items |")
    lines.append("")

    lines.append("## 4. Parity Metrics by Category")
    lines.append("")
    lines.append("| Discrepancy Category | Total Cases | True Positives | True Negatives | False Positives | False Negatives | Matches | Agreement (%) |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for cat in CATEGORIES:
        m = summary.category_metrics[cat]
        lines.append(
            f"| `{cat}` | {m.total} | {m.true_positives} | {m.true_negatives} | "
            f"{m.false_positives} | {m.false_negatives} | {m.matches} | **{m.agreement_pct:.2f}%** |"
        )
    lines.append("")

    lines.append("## 5. Non-Covered / Unmodeled Discrepancies")
    lines.append("")
    lines.append(
        "The legacy engine produces additional flags that are outside the scope of "
        "`RECONCILIATION_RULES`. These are monitored and verified not to cause false "
        "parity discrepancies:"
    )
    lines.append("")
    lines.append("| Discrepancy Type | Legacy Invocations | Handling |")
    lines.append("|---|---|---|")
    for unmod_type, cnt in sorted(summary.unmodeled_counts.items()):
        lines.append(
            f"| `{unmod_type}` | {cnt} | Non-covered by symbolic rules; tracked as out-of-scope |"
        )
    lines.append("")

    lines.append("## 6. Mismatch Analysis")
    lines.append("")
    mismatches = [r for r in results if not r.all_matched]
    if not mismatches:
        lines.append("No mismatches detected. Both engines produced identical binary decisions across all categories.")
    else:
        lines.append(f"Total mismatches: {len(mismatches)}")
        lines.append("")
        for m_case in mismatches:
            lines.append(f"- **Case ID**: `{m_case.case_id}` (Strata: `{m_case.strata}`)")
            lines.append(f"  - Oracle Flags: `{m_case.oracle_flags}`")
            lines.append(f"  - Symbolic Flags: `{m_case.symbolic_flags}`")
            lines.append(f"  - Category Matches: `{m_case.matches}`")
    lines.append("")

    content = "\n".join(lines) + "\n"

    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(content, encoding="utf-8")

    return content
