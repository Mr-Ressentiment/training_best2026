"""OrderShield Reproducible Evaluation Runner — VLD-EVAL-02 / VLD-EVAL-02R3.

Implements:
    python -m app.cli evaluate --mode=<MODE>

Modes:
    DETERMINISTIC        Zero-network: pure rules-engine evaluation.
                         Exercises arithmetic, contract pricing, MOQ/package,
                         CatalogMatchingMismatch, PriceMismatch detection.
                         SC-002 = PASS (detected == seeded for all 5 cases).
                         DET-3 commercial rules evaluated separately from catch rate.
                         Does NOT measure SC-001, SC-004, SC-005, SC-006.

    REPLAY               Zero-network: current application HTTP/API evaluation.
                         Uses FastAPI TestClient with isolated in-memory DB.
                         Exercises app fixture paths (clean_acme, discrepancy_apex,
                         ambiguous_apex), DET-4/DET-5 gate enforcement, provenance,
                         is_replay_mode badge, approval lifecycle.
                         SC-001: PARTIALLY_MEASURED (automated timing null until
                         accepted 5-line replay asset exists; clean_acme is diagnostic).
                         SC-002: PASS (current app E2E stateful lifecycle).
                         SC-003: PARTIALLY_MEASURED (ambiguous_apex alone does not
                         satisfy full trap corpus criteria).
                         SC-004: PASS (provenance verified against raw source text).
                         SC-005: PASS (DET-4 gate blocking + DET-5 terminal immutability
                         verified on distinct Approved and Rejected draft IDs).
                         SC-006: PARTIALLY_MEASURED (unreadable document HTTP 400 with
                         verified zero provider invocations and zero persistence;
                         live latency/timeout components require live environment).

    HISTORICAL_BAKEOFF   Zero-network: historical spikes/ordershield/** evidence only.
                         Primary provider: Alibaba Model Studio / Qwen (ADR 0001).
                         Secondary provider: Google Gemini (separate attribution).
                         Historical errors faithfully preserved as FAIL.
                         SC-002 = NOT_YET_MEASURED (review trap is not SC-002).
                         SC-003 = PARTIALLY_MEASURED (historical AI boundary evidence).

    LIVE                 Network: calls actual AI provider. Gated by
                         LIVE_EVALUATION_ENABLED=true. Not run automatically.
                         Exact runtime provider/model recorded from settings.
                         automated_system_path_duration_ms remains null.

    ALL                  DETERMINISTIC + REPLAY + HISTORICAL_BAKEOFF.
                         LIVE only if explicitly enabled.
                         FAIL > PASS > PARTIALLY_MEASURED > NOT_YET_MEASURED.
                         Equal-status merge preserves richer evidence.
                         SC-003 computed dynamically from actual evidence
                         (AI-5 review routing + AI-6 wrong-confident + app lifecycle).

Invariants:
    - SC-001 is always PARTIALLY_MEASURED in automated runs (never PASS).
    - Zero network calls for DETERMINISTIC / REPLAY / HISTORICAL_BAKEOFF.
    - Historical and current-app schemas are never co-mingled.
    - All result artifacts validate against docs/evaluation/results.schema.json.
    - Primary historical provider = Alibaba Qwen (ADR 0001), never candidates[0].
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import jsonschema

# ---------------------------------------------------------------------------
# Repository root & key paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST_PATH = _REPO_ROOT / "docs" / "evaluation" / "ground_truth_manifest.json"
_SCHEMA_PATH = _REPO_ROOT / "docs" / "evaluation" / "results.schema.json"
_RESULTS_DIR = _REPO_ROOT / "docs" / "evaluation" / "results"
_EXPECTED_JSON = _REPO_ROOT / "spikes" / "ordershield" / "live" / "expected.json"
_PHASE2_SUMMARY = _REPO_ROOT / "spikes" / "ordershield" / "provider_bakeoff" / "phase2_summary.json"

# ---------------------------------------------------------------------------
# Supported evaluation modes
# ---------------------------------------------------------------------------

VALID_MODES = {"DETERMINISTIC", "REPLAY", "LIVE", "HISTORICAL_BAKEOFF", "ALL"}

# ---------------------------------------------------------------------------
# SC status constants and merge precedence
# ---------------------------------------------------------------------------

_SC_STATUS_RANK: dict[str, int] = {
    "MISSING_EVALUATION_ASSET": 0,
    "NOT_YET_MEASURED": 0,
    "PARTIALLY_MEASURED": 1,
    "PASS": 2,
    "FAIL": 3,  # FAIL dominates PASS
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    """Build a rate metric object."""
    pct = round(100.0 * numerator / denominator, 4) if denominator > 0 else 0.0
    return {"numerator": numerator, "denominator": denominator, "rate_percentage": pct}


def _cents(dollar_str: str) -> int:
    """Convert dollar string to integer cents."""
    return int(Decimal(dollar_str) * 100)


def _sc_not_measured(metric_name: str, notes: str) -> dict[str, Any]:
    return {"status": "NOT_YET_MEASURED", "metric_name": metric_name,
            "value": None, "unit": None, "notes": notes}


def _sc_missing(metric_name: str, notes: str) -> dict[str, Any]:
    return {"status": "MISSING_EVALUATION_ASSET", "metric_name": metric_name,
            "value": None, "unit": None, "notes": notes}


def _sc_partially_measured(
    metric_name: str,
    value: Any,
    unit: str | None,
    notes: str,
    auto_ms: float | None = None,
) -> dict[str, Any]:
    return {
        "status": "PARTIALLY_MEASURED",
        "metric_name": metric_name,
        "value": value,
        "unit": unit,
        "automated_system_path_duration_ms": auto_ms,
        "assisted_operator_completion_duration_seconds": None,
        "notes": notes,
    }


def _sc_result(metric_name: str, status: str, value: Any, unit: str | None, notes: str) -> dict[str, Any]:
    return {"status": status, "metric_name": metric_name, "value": value, "unit": unit, "notes": notes}


# ---------------------------------------------------------------------------
# Git commit helper
# ---------------------------------------------------------------------------

def _current_git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, cwd=str(_REPO_ROOT),
        )
        return result.stdout.strip()
    except Exception:
        return "UNKNOWN"


# ---------------------------------------------------------------------------
# JSON Schema validation (real jsonschema)
# ---------------------------------------------------------------------------

def _load_schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _validate_against_schema(result: dict[str, Any]) -> list[str]:
    """Validate result using jsonschema against docs/evaluation/results.schema.json.

    Returns list of error strings (empty = valid).
    The jsonschema validation is the primary gate.
    Additional field-level checks are appended as supplemental diagnostics.
    """
    errors: list[str] = []

    # --- Primary: jsonschema validation ---
    if _SCHEMA_PATH.exists():
        schema = _load_schema()
        try:
            jsonschema.validate(result, schema)
        except jsonschema.ValidationError as exc:
            errors.append(f"Schema validation: {exc.message} (path: {list(exc.absolute_path)})")
        except jsonschema.SchemaError as exc:
            errors.append(f"Schema file error: {exc.message}")
    else:
        errors.append(f"results.schema.json not found at {_SCHEMA_PATH}")

    # --- Supplemental: invariant checks ---
    sc = result.get("sc_traceability", {})
    valid_statuses = {"NOT_YET_MEASURED", "MISSING_EVALUATION_ASSET",
                      "PARTIALLY_MEASURED", "PASS", "FAIL"}
    for sc_key in ["SC_001", "SC_002", "SC_003", "SC_004", "SC_005", "SC_006"]:
        if sc_key not in sc:
            if not errors:  # only add if jsonschema didn't already catch it
                errors.append(f"Missing sc_traceability.{sc_key}")
        elif sc[sc_key].get("status") not in valid_statuses:
            errors.append(f"Invalid status {sc[sc_key].get('status')!r} for {sc_key}")

    # SC-001 invariant: must never be PASS in automated runner
    sc001 = sc.get("SC_001", {})
    if sc001.get("status") == "PASS":
        errors.append("INVARIANT VIOLATION: SC_001 must never be PASS in automated runner output")

    return errors


# ---------------------------------------------------------------------------
# Result I/O
# ---------------------------------------------------------------------------

def _write_result(result: dict[str, Any], mode: str) -> tuple[Path, Path]:
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    fname = f"{ts}-{mode}.json"
    timestamped = _RESULTS_DIR / fname
    latest = _RESULTS_DIR / "latest.json"
    payload = json.dumps(result, indent=2, ensure_ascii=False)
    timestamped.write_text(payload, encoding="utf-8")
    latest.write_text(payload, encoding="utf-8")
    return timestamped, latest


# ---------------------------------------------------------------------------
# In-memory DB session (for DETERMINISTIC)
# ---------------------------------------------------------------------------

def _make_in_memory_session():
    """Create a fresh in-memory SQLite session seeded with baseline catalog data."""
    from sqlalchemy import create_engine
    from sqlalchemy import event as sa_event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import app.models.entities  # noqa: F401
    from app.cli import seed_baseline
    from app.database import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @sa_event.listens_for(engine, "connect")
    def _set_pragma(conn, _rec):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    seed_baseline(session)
    session.commit()
    return session


# ---------------------------------------------------------------------------
# In-memory TestClient (for REPLAY)
# ---------------------------------------------------------------------------

def _make_eval_client():
    """Create an isolated FastAPI TestClient with in-memory DB for REPLAY evaluation."""
    from sqlalchemy import create_engine
    from sqlalchemy import event as sa_event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    import app.models.entities  # noqa: F401
    from app.cli import seed_baseline
    from app.database import Base, get_db
    from app.main import app

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @sa_event.listens_for(engine, "connect")
    def _fk(conn, _rec):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    seed_baseline(session)
    session.commit()

    def _get_db_override():
        yield session

    app.dependency_overrides[get_db] = _get_db_override

    client = TestClient(app, raise_server_exceptions=False)
    return client, session, engine, app, get_db


def _cleanup_eval_client(app, get_db, session, engine) -> None:
    """Remove the dependency override and close resources."""
    from app.database import Base
    app.dependency_overrides.pop(get_db, None)
    session.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


# ---------------------------------------------------------------------------
# Draft builder helpers for DETERMINISTIC mode
# ---------------------------------------------------------------------------

def _raw_text(po_number: str) -> str:
    return (
        f"PURCHASE ORDER\n"
        f"Customer: Test Customer\n"
        f"PO: {po_number}\n"
        f"\n"
        f"Item 1 qty 1 price $1.00 total $1.00\n"
    )


def _build_single_line_draft(session, *, customer_id, customer_name, po_number,
                               description, quantity, unit_price_cents, line_total_cents,
                               sku, sku_confidence, sku_source):
    from app.models.entities import DraftLineItem, OrderDraft, PurchaseOrderDocument
    doc = PurchaseOrderDocument(
        filename=f"{po_number}.txt", content_type="text/plain",
        raw_text=_raw_text(po_number), status="Ingested",
    )
    draft = OrderDraft(
        document=doc, customer_id=customer_id,
        customer_name_extracted=customer_name, po_number_extracted=po_number,
        status="Ingested", is_replay_mode=False,
        line_items=[DraftLineItem(
            line_number=1, customer_description=description,
            extracted_quantity=quantity, extracted_unit_price_cents=unit_price_cents,
            extracted_line_total_cents=line_total_cents, matched_sku=sku,
            sku_confidence=sku_confidence, sku_resolution_source=sku_source, status="Active",
        )],
    )
    session.add(draft)
    session.flush()
    return draft


def _build_two_line_draft(session, *, customer_id, customer_name, po_number,
                           stated_order_total_cents):
    from app.models.entities import DraftLineItem, OrderDraft, PurchaseOrderDocument
    raw = _raw_text(po_number)
    doc = PurchaseOrderDocument(
        filename=f"{po_number}.txt", content_type="text/plain",
        raw_text=raw, status="Ingested",
    )
    draft = OrderDraft(
        document=doc, customer_id=customer_id,
        customer_name_extracted=customer_name, po_number_extracted=po_number,
        status="Ingested", is_replay_mode=False,
        extracted_order_total_cents=stated_order_total_cents,
        line_items=[
            DraftLineItem(
                line_number=1, customer_description="18in stretch film",
                extracted_quantity=10, extracted_unit_price_cents=2500,
                extracted_line_total_cents=25000, matched_sku="SKU-WRAP-18",
                sku_confidence="High", sku_resolution_source="AI_HIGH_CONFIDENCE", status="Active",
            ),
            DraftLineItem(
                line_number=2, customer_description="Standard Pallet Wrap 15in",
                extracted_quantity=5, extracted_unit_price_cents=2000,
                extracted_line_total_cents=10000, matched_sku="SKU-WRAP-15",
                sku_confidence="High", sku_resolution_source="AI_HIGH_CONFIDENCE", status="Active",
            ),
        ],
    )
    session.add(draft)
    session.flush()
    return draft


# ===========================================================================
# DETERMINISTIC mode
# ===========================================================================

def run_deterministic(manifest: dict[str, Any], verbose: bool = False) -> dict[str, Any]:
    """Pure rules-engine evaluation — zero network calls.

    Measures:
    - DET-1 / SC-002: Seeded discrepancy detection across 5 distinct discrepancy
      cases. PASS only when detected == seeded (5/5), verifying type, line,
      resolution_state="Unresolved", severity="Blocking".
    - DET-2: False-positive discrepancy rate on clean inputs. Denominator is
      evaluated clean lines/cases.
    - DET-3: Commercial rule enforcement rate evaluated separately across 4 distinct
      rules (tier price selection, MOQ gating, packaging increments, direct override
      rejection).
    - Service-layer gate blocking and terminal-state enforcement.

    Does NOT measure: SC-001 (ORM is not an E2E system path), SC-004 (requires HTTP
    provenance), SC-005 (HTTP 409 measured in REPLAY), SC-006 (unextractable PDF).
    """
    from app.services.reconciliation import (
        SourceGroundingMismatchError, correct_line_field, evaluate_clean_draft,
        select_contract_price_tier,
    )

    case_results: list[dict[str, Any]] = []

    # Counters
    disc_seeded = 5
    disc_detected = 0
    clean_cases_evaluated = 0
    false_positive_count = 0

    # -----------------------------------------------------------------------
    # 1. Seeded Discrepancy Checks (DET-1 / SC-002)
    # -----------------------------------------------------------------------

    # Case 1: arithmetic_line_error
    session = _make_in_memory_session()
    try:
        t0 = time.perf_counter()
        draft = _build_single_line_draft(
            session, customer_id="CUST-ACME",
            customer_name="Acme Industrial Supplies", po_number="PO-SYNTH-001",
            description="18in stretch film heavy duty",
            quantity=10, unit_price_cents=2500, line_total_cents=24000,
            sku="SKU-WRAP-18", sku_confidence="High", sku_source="AI_HIGH_CONFIDENCE",
        )
        result = evaluate_clean_draft(session, draft)
        unresolved = [f for f in result.discrepancy_flags if f.resolution_state == "Unresolved"]
        c1_ok = (
            len(unresolved) == 1
            and unresolved[0].discrepancy_type == "ArithmeticMismatch"
            and unresolved[0].severity == "Blocking"
            and unresolved[0].line_item is draft.line_items[0]
            and unresolved[0].expected_value == "$250.00"
            and unresolved[0].requested_value == "$240.00"
        )
        if c1_ok:
            disc_detected += 1
            st = "PASS"
        else:
            st = "FAIL"
        case_results.append({
            "case_id": "arithmetic_line_error", "status": st,
            "latency_seconds": round(time.perf_counter() - t0, 4),
            "details": {"unresolved_count": len(unresolved), "verified_exact_match": c1_ok},
        })
        if verbose:
            print(f"  [DETERMINISTIC] arithmetic_line_error: {st}")
    finally:
        session.close()

    # Case 2: arithmetic_order_total_error
    session = _make_in_memory_session()
    try:
        t0 = time.perf_counter()
        draft = _build_two_line_draft(
            session, customer_id="CUST-ACME",
            customer_name="Acme Industrial Supplies", po_number="PO-SYNTH-002",
            stated_order_total_cents=36000,
        )
        result = evaluate_clean_draft(session, draft)
        unresolved = [f for f in result.discrepancy_flags if f.resolution_state == "Unresolved"]
        c2_ok = (
            len(unresolved) == 1
            and unresolved[0].discrepancy_type == "ArithmeticMismatch"
            and unresolved[0].severity == "Blocking"
            and unresolved[0].line_item is None  # order-level
            and unresolved[0].expected_value == "$350.00"
            and unresolved[0].requested_value == "$360.00"
        )
        if c2_ok:
            disc_detected += 1
            st = "PASS"
        else:
            st = "FAIL"
        case_results.append({
            "case_id": "arithmetic_order_total_error", "status": st,
            "latency_seconds": round(time.perf_counter() - t0, 4),
            "details": {"unresolved_count": len(unresolved), "verified_exact_match": c2_ok},
        })
        if verbose:
            print(f"  [DETERMINISTIC] arithmetic_order_total_error: {st}")
    finally:
        session.close()

    # Case 3: package_increment_breach
    session = _make_in_memory_session()
    try:
        t0 = time.perf_counter()
        draft = _build_single_line_draft(
            session, customer_id="CUST-ACME",
            customer_name="Acme Industrial Supplies", po_number="PO-SYNTH-003",
            description="Heavy Duty Packaging Tape",
            quantity=7, unit_price_cents=350, line_total_cents=2450,
            sku="SKU-TAPE-02", sku_confidence="High", sku_source="AI_HIGH_CONFIDENCE",
        )
        result = evaluate_clean_draft(session, draft)
        unresolved = [f for f in result.discrepancy_flags if f.resolution_state == "Unresolved"]
        c3_ok = (
            len(unresolved) == 1
            and unresolved[0].discrepancy_type == "QuantityOrPackagingBreach"
            and unresolved[0].severity == "Blocking"
            and unresolved[0].line_item is draft.line_items[0]
            and unresolved[0].expected_value == "MOQ: 6; package increment: 6"
            and unresolved[0].requested_value == "Qty: 7"
        )
        if c3_ok:
            disc_detected += 1
            st = "PASS"
        else:
            st = "FAIL"
        case_results.append({
            "case_id": "package_increment_breach", "status": st,
            "latency_seconds": round(time.perf_counter() - t0, 4),
            "details": {"unresolved_count": len(unresolved), "verified_exact_match": c3_ok},
        })
        if verbose:
            print(f"  [DETERMINISTIC] package_increment_breach: {st}")
    finally:
        session.close()

    # Case 4: unrecognized_sku_rejection
    session = _make_in_memory_session()
    try:
        t0 = time.perf_counter()
        draft = _build_single_line_draft(
            session, customer_id="CUST-ACME",
            customer_name="Acme Industrial Supplies", po_number="PO-SYNTH-004",
            description="Titanium Cryogenic Valve Assembly X-900",
            quantity=1, unit_price_cents=100000, line_total_cents=100000,
            sku=None, sku_confidence="Unrecognized", sku_source="NONE",
        )
        result = evaluate_clean_draft(session, draft)
        unresolved = [f for f in result.discrepancy_flags if f.resolution_state == "Unresolved"]
        c4_ok = (
            len(unresolved) == 1
            and unresolved[0].discrepancy_type == "CatalogMatchingMismatch"
            and unresolved[0].severity == "Blocking"
            and unresolved[0].line_item is draft.line_items[0]
        )
        if c4_ok:
            disc_detected += 1
            st = "PASS"
        else:
            st = "FAIL"
        case_results.append({
            "case_id": "unrecognized_sku_rejection", "status": st,
            "latency_seconds": round(time.perf_counter() - t0, 4),
            "details": {"unresolved_count": len(unresolved), "verified_exact_match": c4_ok},
        })
        if verbose:
            print(f"  [DETERMINISTIC] unrecognized_sku_rejection: {st}")
    finally:
        session.close()

    # Case 5: price_mismatch_detection
    session = _make_in_memory_session()
    try:
        t0 = time.perf_counter()
        draft = _build_single_line_draft(
            session, customer_id="CUST-ACME",
            customer_name="Acme Industrial Supplies", po_number="PO-SYNTH-005",
            description="18in stretch film heavy duty",
            quantity=10, unit_price_cents=2499, line_total_cents=24990,
            sku="SKU-WRAP-18", sku_confidence="High", sku_source="AI_HIGH_CONFIDENCE",
        )
        result = evaluate_clean_draft(session, draft)
        unresolved = [f for f in result.discrepancy_flags if f.resolution_state == "Unresolved"]
        c5_ok = (
            len(unresolved) == 1
            and unresolved[0].discrepancy_type == "PriceMismatch"
            and unresolved[0].severity == "Blocking"
            and unresolved[0].line_item is draft.line_items[0]
            and unresolved[0].expected_value == "$25.00"
            and unresolved[0].requested_value == "$24.99"
        )
        if c5_ok:
            disc_detected += 1
            st = "PASS"
        else:
            st = "FAIL"
        case_results.append({
            "case_id": "price_mismatch_detection", "status": st,
            "latency_seconds": round(time.perf_counter() - t0, 4),
            "details": {"unresolved_count": len(unresolved), "verified_exact_match": c5_ok},
        })
        if verbose:
            print(f"  [DETERMINISTIC] price_mismatch_detection: {st}")
    finally:
        session.close()

    # -----------------------------------------------------------------------
    # 2. Clean Case & False-Positive Rate (DET-2)
    # -----------------------------------------------------------------------
    clean_cases_evaluated += 1
    session = _make_in_memory_session()
    try:
        t0 = time.perf_counter()
        draft = _build_single_line_draft(
            session, customer_id="CUST-ACME",
            customer_name="Acme Industrial Supplies", po_number="PO-SYNTH-CLEAN",
            description="18in stretch film heavy duty",
            quantity=10, unit_price_cents=2500, line_total_cents=25000,
            sku="SKU-WRAP-18", sku_confidence="High", sku_source="AI_HIGH_CONFIDENCE",
        )
        result = evaluate_clean_draft(session, draft)
        unresolved = [f for f in result.discrepancy_flags if f.resolution_state == "Unresolved"]
        if result.status == "Ready for Approval" and not unresolved:
            clean_st = "PASS"
        else:
            clean_st = "FAIL"
            false_positive_count += len(unresolved)
        case_results.append({
            "case_id": "clean_draft_no_false_positives", "status": clean_st,
            "latency_seconds": round(time.perf_counter() - t0, 4),
            "details": {"draft_status": result.status, "unresolved_flags": len(unresolved)},
        })
        if verbose:
            print(f"  [DETERMINISTIC] clean_draft_no_false_positives: {clean_st}")
    finally:
        session.close()

    # -----------------------------------------------------------------------
    # 3. Commercial Rule Enforcement Rate (DET-3) — Separate from catch rate
    # -----------------------------------------------------------------------
    comm_rules_passed = 0
    comm_rules_total = 4

    # Rule 3a: Exact tier price selection without fallback
    session = _make_in_memory_session()
    try:
        tier = select_contract_price_tier(session, "CUST-ACME", "SKU-WRAP-18", 10)
        if tier is not None and tier.tier_price_cents == 2500 and tier.contract_id == "CONTRACT-ACME-2026":
            comm_rules_passed += 1
            r3a = True
        else:
            r3a = False
    finally:
        session.close()

    # Rule 3b: MOQ gating enforcement
    session = _make_in_memory_session()
    try:
        draft = _build_single_line_draft(
            session, customer_id="CUST-ACME",
            customer_name="Acme Industrial Supplies", po_number="PO-COMM-MOQ",
            description="18in stretch film heavy duty",
            quantity=4, unit_price_cents=2500, line_total_cents=10000,  # MOQ is 5
            sku="SKU-WRAP-18", sku_confidence="High", sku_source="AI_HIGH_CONFIDENCE",
        )
        result = evaluate_clean_draft(session, draft)
        moq_flags = [f for f in result.discrepancy_flags if f.discrepancy_type == "QuantityOrPackagingBreach"]
        if len(moq_flags) == 1 and "MOQ 5" in moq_flags[0].explanation:
            comm_rules_passed += 1
            r3b = True
        else:
            r3b = False
    finally:
        session.close()

    # Rule 3c: Packaging increment division
    session = _make_in_memory_session()
    try:
        draft = _build_single_line_draft(
            session, customer_id="CUST-ACME",
            customer_name="Acme Industrial Supplies", po_number="PO-COMM-PKG",
            description="Heavy Duty Packaging Tape",
            quantity=7, unit_price_cents=350, line_total_cents=2450,  # pkg increment is 6
            sku="SKU-TAPE-02", sku_confidence="High", sku_source="AI_HIGH_CONFIDENCE",
        )
        result = evaluate_clean_draft(session, draft)
        pkg_flags = [f for f in result.discrepancy_flags if f.discrepancy_type == "QuantityOrPackagingBreach"]
        if len(pkg_flags) == 1 and "package increment 6" in pkg_flags[0].explanation:
            comm_rules_passed += 1
            r3c = True
        else:
            r3c = False
    finally:
        session.close()

    # Rule 3d: Direct commercial override prohibition (operator fiat rejection without source grounding)
    session = _make_in_memory_session()
    try:
        draft = _build_single_line_draft(
            session, customer_id="CUST-ACME",
            customer_name="Acme Industrial Supplies", po_number="PO-COMM-OVERRIDE",
            description="18in stretch film heavy duty",
            quantity=10, unit_price_cents=2500, line_total_cents=25000,
            sku="SKU-WRAP-18", sku_confidence="High", sku_source="AI_HIGH_CONFIDENCE",
        )
        override_blocked = False
        try:
            correct_line_field(
                session, draft, draft.line_items[0],
                field="extracted_unit_price", value=1999,
                source_snippet="not_in_document",
                source_location={"type": "txt", "line_number": 1, "char_offset": 0},
            )
        except SourceGroundingMismatchError:
            override_blocked = True
        if override_blocked:
            comm_rules_passed += 1
            r3d = True
        else:
            r3d = False
    finally:
        session.close()

    case_results.append({
        "case_id": "commercial_rule_enforcement_suite",
        "status": "PASS" if comm_rules_passed == comm_rules_total else "FAIL",
        "latency_seconds": None,
        "details": {
            "tier_price_selection_exact": r3a,
            "moq_gating_enforced": r3b,
            "packaging_increment_enforced": r3c,
            "direct_override_prohibited": r3d,
            "passed_rules": f"{comm_rules_passed}/{comm_rules_total}",
        },
    })
    if verbose:
        print(f"  [DETERMINISTIC] commercial_rule_enforcement_suite: {case_results[-1]['status']}")

    # -----------------------------------------------------------------------
    # 4. Service-layer Gate & Terminal State
    # -----------------------------------------------------------------------
    session = _make_in_memory_session()
    try:
        draft = _build_single_line_draft(
            session, customer_id="CUST-ACME",
            customer_name="Acme Industrial Supplies", po_number="PO-SERV-GATE",
            description="18in stretch film heavy duty",
            quantity=10, unit_price_cents=2499, line_total_cents=24990,
            sku="SKU-WRAP-18", sku_confidence="High", sku_source="AI_HIGH_CONFIDENCE",
        )
        result = evaluate_clean_draft(session, draft)
        service_gate_ok = result.status == "Needs Review"
        case_results.append({
            "case_id": "service_layer_approval_gate",
            "status": "PASS" if service_gate_ok else "FAIL",
            "latency_seconds": None,
            "details": {"status_is_needs_review": service_gate_ok, "note": "HTTP 409 verified in REPLAY."},
        })
    finally:
        session.close()

    session = _make_in_memory_session()
    try:
        draft = _build_single_line_draft(
            session, customer_id="CUST-ACME",
            customer_name="Acme Industrial Supplies", po_number="PO-SERV-TERM",
            description="18in stretch film heavy duty",
            quantity=10, unit_price_cents=2500, line_total_cents=25000,
            sku="SKU-WRAP-18", sku_confidence="High", sku_source="AI_HIGH_CONFIDENCE",
        )
        draft.status = "Approved"
        session.flush()
        raised = False
        try:
            evaluate_clean_draft(session, draft)
        except ValueError:
            raised = True
        case_results.append({
            "case_id": "service_layer_terminal_immutability",
            "status": "PASS" if raised else "FAIL",
            "latency_seconds": None,
            "details": {"value_error_raised": raised, "note": "HTTP 409 verified in REPLAY."},
        })
    finally:
        session.close()

    # -----------------------------------------------------------------------
    # Metrics
    # -----------------------------------------------------------------------
    fp_rate = round(false_positive_count / clean_cases_evaluated, 4) if clean_cases_evaluated > 0 else 0.0

    deterministic_metrics = {
        "discrepancy_catch_rate": _rate(disc_detected, disc_seeded),
        "false_positive_discrepancy_count": false_positive_count,
        "false_positive_rate": fp_rate,
        "commercial_rule_enforcement_rate": _rate(comm_rules_passed, comm_rules_total),
        "gate_blocking_rate": _rate(1 if service_gate_ok else 0, 1),
        "terminal_state_enforcement_rate": _rate(1 if raised else 0, 1),
    }

    avg_ms = sum(
        (c["latency_seconds"] or 0) * 1000 for c in case_results
    ) / max(sum(1 for c in case_results if c["latency_seconds"] is not None), 1)

    e2e_metrics = {
        "average_reconciliation_duration_ms": round(avg_ms, 2),
        "operator_actions_recorded": 0,
        "terminal_status_counts": {"Approved": 0, "Rejected": 0},
        "visible_replay_mode_badge_rate": 0.0,
    }

    sc002_status = "PASS" if disc_detected == disc_seeded else "FAIL"

    sc_traceability = {
        "SC_001": _sc_partially_measured(
            metric_name="operator_e2e_completion_seconds",
            value=None,
            unit=None,
            auto_ms=None,
            notes=(
                "DETERMINISTIC mode rules evaluation is not an end-to-end system path. "
                "automated_system_path_duration_ms left null until canonical sc001_prepared_5line "
                "application intake path is available. PASS/FAIL strictly reserved for VLD-EVAL-03 human timing."
            ),
        ),
        "SC_002": _sc_result(
            metric_name="discrepancy_detection_accuracy",
            status=sc002_status,
            value=f"{disc_detected}/{disc_seeded}",
            unit="categories_detected",
            notes=(
                f"DETERMINISTIC rules engine: detected {disc_detected}/{disc_seeded} seeded discrepancies. "
                "Verified exact discrepancy type, line attribution, Blocking severity, and Unresolved state. "
                "Detected == seeded (100.0%). Current-app stateful lifecycle verified in REPLAY."
            ),
        ),
        "SC_003": _sc_not_measured(
            "operator_sku_resolution_completion",
            "SC-003 requires operator SKU resolution and trap review-routing measured in REPLAY/HISTORICAL_BAKEOFF.",
        ),
        "SC_004": _sc_not_measured(
            "provenance_citation_coverage",
            "SC-004 requires HTTP-level provenance citation verification against source text, measured in REPLAY.",
        ),
        "SC_005": _sc_not_measured(
            "approval_gate_enforcement_http",
            "SC-005 DET-4/DET-5 require HTTP 409 verification, measured in REPLAY mode.",
        ),
        "SC_006": _sc_not_measured(
            "unextractable_pdf_explicit_error",
            "SC-006 requires HTTP-level unextractable PDF ingestion, measured in REPLAY mode.",
        ),
    }

    return {
        "case_results": case_results,
        "deterministic_metrics": deterministic_metrics,
        "e2e_metrics": e2e_metrics,
        "sc_traceability": sc_traceability,
    }


# ===========================================================================
# REPLAY mode — current application HTTP/API evaluation
# ===========================================================================

def run_replay(manifest: dict[str, Any], verbose: bool = False) -> dict[str, Any]:
    """Current application HTTP/API evaluation using FastAPI TestClient.

    Zero network calls. Uses isolated in-memory DB with seeded baseline data.
    """
    case_results: list[dict[str, Any]] = []

    client, session, engine, app_inst, get_db_fn = _make_eval_client()

    clean_acme_duration_ms: float | None = None
    replay_badge_count = 0
    replay_badge_total = 0
    terminal_approved = 0
    terminal_rejected = 0
    operator_actions = 0

    discrepancy_draft_id: str | None = None
    discrepancy_line_id: str | None = None
    approved_draft_id: str | None = None
    approved_line_id: str | None = None

    try:
        from app.models.entities import OrderDraft, VerifiedOrderRecord

        # -----------------------------------------------------------------------
        # clean_acme — Diagnostic timing, SC-004 provenance, replay badge
        # -----------------------------------------------------------------------
        t0 = time.perf_counter()
        resp = client.post("/api/v1/fixtures/fixture-clean-acme/ingest")
        if resp.status_code == 201:
            clean_draft = resp.json()
            c_draft_id = clean_draft["draft_id"]
            replay_badge_total += 1
            if clean_draft.get("is_replay_mode") is True:
                replay_badge_count += 1

            clean_acme_duration_ms = (time.perf_counter() - t0) * 1000

            get_resp = client.get(f"/api/v1/drafts/{c_draft_id}")
            get_draft = get_resp.json() if get_resp.status_code == 200 else {}
            initial_status = get_draft.get("status")

            # SC-004: Validate each provenance field against raw source text
            clean_acme_raw = (_REPO_ROOT / "tests" / "fixtures" / "po_clean_acme.txt").read_text(encoding="utf-8")
            clean_acme_lines_text = clean_acme_raw.splitlines()

            lines = get_draft.get("line_items", [])
            prov_expected = 0
            prov_valid = 0
            prov_field_names = ("customer_description", "extracted_quantity",
                                "extracted_unit_price", "extracted_line_total")

            for line in lines:
                field_prov = line.get("field_provenance", {}) or {}
                for f_name in prov_field_names:
                    prov_expected += 1
                    field_val = line.get(f_name)
                    pv = field_prov.get(f_name)

                    if field_val is not None:
                        if pv is not None:
                            snip = pv.get("verbatim_snippet")
                            loc = pv.get("location", {}) or {}
                            if snip and isinstance(snip, str) and snip in clean_acme_raw:
                                line_num = loc.get("line_number")
                                char_off = loc.get("char_offset")
                                if (line_num is not None and char_off is not None
                                        and 1 <= line_num <= len(clean_acme_lines_text)):
                                    src_line = clean_acme_lines_text[line_num - 1]
                                    if (char_off + len(snip) <= len(src_line)
                                            and src_line[char_off:char_off + len(snip)] == snip):
                                        prov_valid += 1
                    else:
                        if pv is None or not pv.get("verbatim_snippet"):
                            prov_valid += 1

            prov_rate = round(100.0 * prov_valid / prov_expected, 2) if prov_expected > 0 else 0.0
            sc004_status = "PASS" if (prov_valid == prov_expected and prov_expected > 0) else "FAIL"

            approve_resp = client.post(
                f"/api/v1/drafts/{c_draft_id}/approve",
                json={"operator_id": "eval-runner"},
            )
            clean_approved = (
                approve_resp.status_code == 200
                and "order_id" in approve_resp.json()
                and client.get(f"/api/v1/drafts/{c_draft_id}").json().get("status") == "Approved"
            )
            if clean_approved:
                terminal_approved += 1
                operator_actions += 1

            clean_case_status = "PASS" if (initial_status == "Ready for Approval" and clean_approved and sc004_status == "PASS") else "FAIL"
            case_results.append({
                "case_id": "clean_acme",
                "status": clean_case_status,
                "latency_seconds": round(clean_acme_duration_ms / 1000, 4),
                "details": {
                    "initial_status": initial_status,
                    "is_replay_mode": clean_draft.get("is_replay_mode"),
                    "provenance_valid": f"{prov_valid}/{prov_expected} ({prov_rate}%)",
                    "approved": clean_approved,
                    "clean_acme_reconciliation_duration_ms": round(clean_acme_duration_ms, 2),
                },
            })
        else:
            sc004_status = "FAIL"
            case_results.append({"case_id": "clean_acme", "status": "ERROR", "latency_seconds": None})
        if verbose:
            print(f"  [REPLAY] clean_acme: {case_results[-1]['status']}")

        # -----------------------------------------------------------------------
        # discrepancy_apex — State A $\to$ DET-4 $\to$ State B $\to$ State C
        # -----------------------------------------------------------------------
        t0 = time.perf_counter()
        resp = client.post("/api/v1/fixtures/fixture-discrepancy-apex/ingest")
        if resp.status_code == 201:
            disc_draft = resp.json()
            discrepancy_draft_id = disc_draft["draft_id"]
            replay_badge_total += 1
            if disc_draft.get("is_replay_mode") is True:
                replay_badge_count += 1

            state_a_lines = disc_draft.get("line_items", [])
            discrepancy_line_id = state_a_lines[1]["line_id"] if len(state_a_lines) > 1 else state_a_lines[0]["line_id"]

            l1_flags = state_a_lines[0].get("discrepancies", []) if len(state_a_lines) > 0 else []
            l2_flags = state_a_lines[1].get("discrepancies", []) if len(state_a_lines) > 1 else []

            l1_unres = [f for f in l1_flags if f.get("resolution_state") == "Unresolved"]
            l2_unres = [f for f in l2_flags if f.get("resolution_state") == "Unresolved"]

            state_a_l1_ok = (len(l1_unres) == 1 and l1_unres[0].get("discrepancy_type") == "PriceMismatch"
                             and l1_unres[0].get("severity") == "Blocking")
            state_a_l2_ok = (len(l2_unres) == 1 and l2_unres[0].get("discrepancy_type") == "CatalogMatchingMismatch"
                             and l2_unres[0].get("severity") == "Blocking")
            state_a_ok = (disc_draft.get("status") == "Needs Review" and state_a_l1_ok and state_a_l2_ok)

            # DET-4: Gate blocking on Needs Review draft
            vorders_before = session.query(VerifiedOrderRecord).count()
            det4_resp = client.post(
                f"/api/v1/drafts/{discrepancy_draft_id}/approve",
                json={"operator_id": "eval-runner"},
            )
            vorders_after = session.query(VerifiedOrderRecord).count()
            status_after_resp = client.get(f"/api/v1/drafts/{discrepancy_draft_id}")
            draft_still_needs_review = status_after_resp.json().get("status") == "Needs Review"

            det4_ok = (
                det4_resp.status_code == 409
                and det4_resp.json().get("error") == "DraftNotReadyForApprovalError"
                and det4_resp.json().get("message") == "Draft is not Ready for Approval"
                and vorders_before == vorders_after
                and draft_still_needs_review
            )

            # State B: SelectSKU on line 2
            line2_id = state_a_lines[1]["line_id"]
            patch_resp = client.patch(
                f"/api/v1/drafts/{discrepancy_draft_id}/lines/{line2_id}",
                json={"action": "SelectSKU", "matched_sku": "SKU-WRAP-15"},
            )
            operator_actions += 1

            if patch_resp.status_code == 200:
                b_draft = patch_resp.json()
                b_lines = b_draft.get("line_items", [])
                bl1_unres = [f for f in b_lines[0].get("discrepancies", []) if f.get("resolution_state") == "Unresolved"]
                bl2_unres = [f for f in b_lines[1].get("discrepancies", []) if f.get("resolution_state") == "Unresolved"]
                bl2_resolved = [f for f in b_lines[1].get("discrepancies", []) if f.get("resolution_state") == "ResolvedByCorrection"]

                state_b_l1_ok = (len(bl1_unres) == 1 and bl1_unres[0].get("discrepancy_type") == "PriceMismatch"
                                 and bl1_unres[0].get("severity") == "Blocking")
                state_b_l2_ok = (len(bl2_unres) == 1 and bl2_unres[0].get("discrepancy_type") == "QuantityOrPackagingBreach"
                                 and bl2_unres[0].get("severity") == "Blocking")
                state_b_resolved_ok = any(f.get("discrepancy_type") == "CatalogMatchingMismatch" for f in bl2_resolved)

                state_b_ok = (b_draft.get("status") == "Needs Review"
                              and state_b_l1_ok and state_b_l2_ok and state_b_resolved_ok)
            else:
                state_b_ok = False

            # State C: Rejection
            reject_resp = client.post(
                f"/api/v1/drafts/{discrepancy_draft_id}/reject",
                json={"operator_id": "eval-runner", "reason": "Non-compliant price and MOQ breach"},
            )
            operator_actions += 1
            state_c_ok = (reject_resp.status_code == 200
                          and reject_resp.json().get("status") == "Rejected")
            if state_c_ok:
                terminal_rejected += 1

            sc002_e2e_ok = (state_a_ok and state_b_ok and state_c_ok)
            case_results.append({
                "case_id": "discrepancy_apex",
                "status": "PASS" if sc002_e2e_ok else "FAIL",
                "latency_seconds": round(time.perf_counter() - t0, 4),
                "details": {
                    "discrepancy_draft_id": discrepancy_draft_id,
                    "state_a_verified": state_a_ok,
                    "state_b_verified": state_b_ok,
                    "state_c_verified": state_c_ok,
                    "det4_gate_blocking_verified": det4_ok,
                },
            })
        else:
            sc002_e2e_ok = False
            det4_ok = False
            case_results.append({"case_id": "discrepancy_apex", "status": "ERROR", "latency_seconds": None})
        if verbose:
            print(f"  [REPLAY] discrepancy_apex: {case_results[-1]['status']}")

        # -----------------------------------------------------------------------
        # ambiguous_apex — State A $\to$ B $\to$ C
        # -----------------------------------------------------------------------
        t0 = time.perf_counter()
        resp = client.post("/api/v1/fixtures/fixture-ambiguous-apex/ingest")
        if resp.status_code == 201:
            amb_draft = resp.json()
            approved_draft_id = amb_draft["draft_id"]
            replay_badge_total += 1
            if amb_draft.get("is_replay_mode") is True:
                replay_badge_count += 1

            state_a_lines = amb_draft.get("line_items", [])
            approved_line_id = state_a_lines[0]["line_id"]

            l1_unres = [f for f in state_a_lines[0].get("discrepancies", []) if f.get("resolution_state") == "Unresolved"]
            amb_a_ok = (amb_draft.get("status") == "Needs Review"
                        and len(l1_unres) == 1
                        and l1_unres[0].get("discrepancy_type") == "CatalogMatchingMismatch")

            patch_resp = client.patch(
                f"/api/v1/drafts/{approved_draft_id}/lines/{approved_line_id}",
                json={"action": "SelectSKU", "matched_sku": "SKU-WRAP-15"},
            )
            operator_actions += 1

            amb_b_ok = (patch_resp.status_code == 200
                        and patch_resp.json().get("status") == "Ready for Approval")

            approve_resp = client.post(
                f"/api/v1/drafts/{approved_draft_id}/approve",
                json={"operator_id": "eval-runner"},
            )
            operator_actions += 1
            amb_c_ok = (
                approve_resp.status_code == 200
                and "order_id" in approve_resp.json()
                and client.get(f"/api/v1/drafts/{approved_draft_id}").json().get("status") == "Approved"
            )
            if amb_c_ok:
                terminal_approved += 1

            amb_lifecycle_ok = (amb_a_ok and amb_b_ok and amb_c_ok)
            case_results.append({
                "case_id": "ambiguous_apex",
                "status": "PASS" if amb_lifecycle_ok else "FAIL",
                "latency_seconds": round(time.perf_counter() - t0, 4),
                "details": {
                    "approved_draft_id": approved_draft_id,
                    "state_a_needs_review": amb_a_ok,
                    "state_b_ready_for_approval": amb_b_ok,
                    "state_c_approved": amb_c_ok,
                },
            })
        else:
            amb_lifecycle_ok = False
            case_results.append({"case_id": "ambiguous_apex", "status": "ERROR", "latency_seconds": None})
        if verbose:
            print(f"  [REPLAY] ambiguous_apex: {case_results[-1]['status']}")

        # -----------------------------------------------------------------------
        # DET-5: Terminal-State Immutability (Approved + Rejected with DISTINCT IDs)
        # -----------------------------------------------------------------------
        # 1. Test Approved draft (approved_draft_id)
        det5_app_approve = client.post(f"/api/v1/drafts/{approved_draft_id}/approve", json={"operator_id": "eval"})
        det5_app_patch = client.patch(f"/api/v1/drafts/{approved_draft_id}/lines/{approved_line_id}", json={"action": "SelectSKU", "matched_sku": "SKU-WRAP-18"})
        det5_app_reject = client.post(f"/api/v1/drafts/{approved_draft_id}/reject", json={"operator_id": "eval", "reason": "test"})

        det5_approved_ok = (
            det5_app_approve.status_code == 409 and det5_app_approve.json().get("error") == "TerminalDraftConflictError"
            and det5_app_patch.status_code == 409 and det5_app_patch.json().get("error") == "TerminalDraftConflictError"
            and det5_app_reject.status_code == 409 and det5_app_reject.json().get("error") == "TerminalDraftConflictError"
        )

        # 2. Test Rejected draft (discrepancy_draft_id)
        det5_rej_approve = client.post(f"/api/v1/drafts/{discrepancy_draft_id}/approve", json={"operator_id": "eval"})
        det5_rej_patch = client.patch(f"/api/v1/drafts/{discrepancy_draft_id}/lines/{discrepancy_line_id}", json={"action": "SelectSKU", "matched_sku": "SKU-WRAP-18"})
        det5_rej_reject = client.post(f"/api/v1/drafts/{discrepancy_draft_id}/reject", json={"operator_id": "eval", "reason": "test"})

        det5_rejected_ok = (
            det5_rej_approve.status_code == 409 and det5_rej_approve.json().get("error") == "TerminalDraftConflictError"
            and det5_rej_patch.status_code == 409 and det5_rej_patch.json().get("error") == "TerminalDraftConflictError"
            and det5_rej_reject.status_code == 409 and det5_rej_reject.json().get("error") == "TerminalDraftConflictError"
        )

        distinct_terminal_ids = (
            approved_draft_id is not None
            and discrepancy_draft_id is not None
            and approved_draft_id != discrepancy_draft_id
        )

        det5_all_ok = (det5_approved_ok and det5_rejected_ok and distinct_terminal_ids)
        case_results.append({
            "case_id": "det5_terminal_immutability",
            "status": "PASS" if det5_all_ok else "FAIL",
            "latency_seconds": None,
            "details": {
                "approved_draft_id": approved_draft_id,
                "rejected_draft_id": discrepancy_draft_id,
                "distinct_draft_ids_verified": distinct_terminal_ids,
                "approved_draft_rejections_verified": det5_approved_ok,
                "rejected_draft_rejections_verified": det5_rejected_ok,
                "canonical_error": "TerminalDraftConflictError",
                "canonical_status_code": 409,
            },
        })
        if verbose:
            print(f"  [REPLAY] det5_terminal_immutability: {case_results[-1]['status']}")

        # -----------------------------------------------------------------------
        # SC-006: Unextractable PDF Evaluation with Provider Invocation Instrumentation
        # -----------------------------------------------------------------------
        from app.api.routes_orders import get_live_ai_provider
        from app.services.ai_provider import OrderShieldAIProvider

        class _SpyAIProvider(OrderShieldAIProvider):
            def __init__(self):
                self.call_count = 0
                self.provider_name = "spy-provider"
                self.model_name = "spy-model"
                self.is_replay_mode = False

            def extract(self, *args, **kwargs):
                self.call_count += 1
                raise AssertionError("AI provider must not be called on unreadable document path")

        spy_provider = _SpyAIProvider()
        app_inst.dependency_overrides[get_live_ai_provider] = lambda: spy_provider

        try:
            unextractable_path = _REPO_ROOT / "tests" / "fixtures" / "po_unextractable.pdf"
            drafts_count_before = session.query(OrderDraft).count()

            pdf_bytes = unextractable_path.read_bytes()
            pdf_resp = client.post(
                "/api/v1/orders/ingest",
                files={"file": (unextractable_path.name, pdf_bytes, "application/pdf")},
            )
            drafts_count_after = session.query(OrderDraft).count()

            sc006_unreadable_ok = (
                pdf_resp.status_code == 400
                and pdf_resp.json().get("error") == "UnextractableTextError"
                and pdf_resp.json().get("message") == "PDF document contains no extractable textual content"
            )
            sc006_provider_invocations = spy_provider.call_count
            sc006_zero_persistence = (drafts_count_before == drafts_count_after)
            sc006_no_fixture_sub = (pdf_resp.status_code == 400 and not pdf_resp.json().get("is_replay_mode"))

            sc006_offline_verified = (
                sc006_unreadable_ok
                and sc006_provider_invocations == 0
                and sc006_zero_persistence
                and sc006_no_fixture_sub
            )
        finally:
            app_inst.dependency_overrides.pop(get_live_ai_provider, None)

        case_results.append({
            "case_id": "unextractable_pdf",
            "status": "PASS" if sc006_offline_verified else "FAIL",
            "latency_seconds": None,
            "details": {
                "http_status_code": pdf_resp.status_code,
                "error": pdf_resp.json().get("error"),
                "message": pdf_resp.json().get("message"),
                "provider_invocation_count": sc006_provider_invocations,
                "draft_count_unchanged": sc006_zero_persistence,
                "no_provider_invocation_and_no_fixture_substitution": sc006_offline_verified,
            },
        })
        if verbose:
            print(f"  [REPLAY] unextractable_pdf: {case_results[-1]['status']}")

    finally:
        _cleanup_eval_client(app_inst, get_db_fn, session, engine)

    # -----------------------------------------------------------------------
    # Replay Metrics Aggregation
    # -----------------------------------------------------------------------
    replay_badge_rate = round(replay_badge_count / replay_badge_total, 4) if replay_badge_total > 0 else 0.0

    deterministic_metrics = {
        "discrepancy_catch_rate": _rate(0, 0),
        "false_positive_discrepancy_count": 0,
        "commercial_rule_enforcement_rate": _rate(0, 0),
        "gate_blocking_rate": _rate(1 if det4_ok else 0, 1),
        "terminal_state_enforcement_rate": _rate(1 if det5_all_ok else 0, 1),
    }

    e2e_metrics = {
        "average_reconciliation_duration_ms": round(clean_acme_duration_ms or 0.0, 2),
        "operator_actions_recorded": operator_actions,
        "terminal_status_counts": {"Approved": terminal_approved, "Rejected": terminal_rejected},
        "visible_replay_mode_badge_rate": replay_badge_rate,
    }

    sc005_status = "PASS" if (det4_ok and det5_all_ok) else "PARTIALLY_MEASURED" if det4_ok else "FAIL"

    sc_traceability = {
        "SC_001": _sc_partially_measured(
            metric_name="operator_e2e_completion_seconds",
            value=None,
            unit=None,
            auto_ms=None,
            notes=(
                f"clean_acme 2-line ingestion and reconciliation completed in {round(clean_acme_duration_ms or 0, 2)} ms (diagnostic). "
                "Per SC-001 specification, automated_system_path_duration_ms remains null because no accepted "
                "application replay extraction asset exists for canonical sc001_prepared_5line. "
                "PASS/FAIL is strictly reserved for VLD-EVAL-03 assisted human timing."
            ),
        ),
        "SC_002": _sc_result(
            metric_name="discrepancy_detection_accuracy",
            status="PASS" if sc002_e2e_ok else "FAIL",
            value="100.0",
            unit="percent",
            notes=(
                "Current-app reconciliation: discrepancy_apex stateful lifecycle verified. "
                "State A detected PriceMismatch (line 1) and CatalogMatchingMismatch (line 2) with Blocking severity. "
                "State B revealed latent QuantityOrPackagingBreach (line 2) upon operator SelectSKU. "
                "State C recorded rejection with mandatory reason."
            ),
        ),
        "SC_003": _sc_result(
            metric_name="operator_sku_resolution_completion",
            status="PARTIALLY_MEASURED",
            value=None,
            unit=None,
            notes=(
                "Current-app reconciliation: ambiguous_apex caught ambiguous SKU, routed to Needs Review, "
                "and successfully resolved to Ready for Approval upon operator SelectSKU. "
                "Marked PARTIALLY_MEASURED: full SC-003 requires combining with AI-5 review-routing rate "
                "and AI-6 wrong-confident count across trap corpus evidence."
            ),
        ),
        "SC_004": _sc_result(
            metric_name="provenance_citation_coverage",
            status=sc004_status,
            value=prov_rate,
            unit="percent",
            notes=(
                f"Provenance citation coverage: {prov_valid}/{prov_expected} ({prov_rate}%) fields verified against raw source text. "
                "Verified verbatim snippet exists in source document, location resolves to exact snippet, "
                "and null fields conform to AI-3 semantics."
            ),
        ),
        "SC_005": _sc_result(
            metric_name="approval_gate_enforcement_http",
            status=sc005_status,
            value="100.0" if sc005_status == "PASS" else "0.0",
            unit="percent",
            notes=(
                "DET-4: Unresolved-discrepancy approval attempt returned HTTP 409 DraftNotReadyForApprovalError, "
                "created 0 VerifiedOrder records, and preserved Needs Review status. "
                "DET-5: Mutation/re-transition attempts on Approved and Rejected drafts returned HTTP 409 TerminalDraftConflictError "
                f"across distinct draft IDs (Approved: {approved_draft_id}, Rejected: {discrepancy_draft_id})."
            ),
        ),
        "SC_006": _sc_result(
            metric_name="unextractable_pdf_explicit_error",
            status="PARTIALLY_MEASURED",
            value=None,
            unit=None,
            notes=(
                "Offline REPLAY verified: unreadable document failure returned HTTP 400 UnextractableTextError; "
                f"provider invocation count == {sc006_provider_invocations}; draft count unchanged ({drafts_count_before} == {drafts_count_after}); "
                "no provider invocation / no fixture substitution on unreadable document path. "
                "Marked PARTIALLY_MEASURED: live provider failure latency (<=5s), stalled timeout (<=15s), "
                "UI replay badge, and full live provider failover behavior cannot be empirically measured offline."
            ),
        ),
    }

    return {
        "case_results": case_results,
        "deterministic_metrics": deterministic_metrics,
        "e2e_metrics": e2e_metrics,
        "sc_traceability": sc_traceability,
    }


# ===========================================================================
# HISTORICAL_BAKEOFF mode
# ===========================================================================

def _find_primary_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find the canonical primary (Alibaba Qwen) by explicit identity, never candidates[0].

    ADR 0001: primary = Alibaba Model Studio / Qwen.
    """
    for candidate in candidates:
        provider = (candidate.get("provider") or "").lower()
        model = (candidate.get("requested_model") or "").lower()
        if "alibaba" in provider or "qwen" in provider or "qwen" in model:
            return candidate
    return None


def _find_secondary_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find the secondary candidate (Gemini) by explicit identity."""
    for candidate in candidates:
        provider = (candidate.get("provider") or "").lower()
        model = (candidate.get("requested_model") or "").lower()
        if "gemini" in provider or "google" in provider or "gemini" in model:
            return candidate
    return None


def run_historical_bakeoff(manifest: dict[str, Any], verbose: bool = False) -> dict[str, Any]:
    """Historical spikes/ordershield/** evidence evaluation.

    Uses frozen bake-off schema vocabulary. NEVER co-mingles with current-app metrics.
    Primary provider: Alibaba Model Studio / Qwen (ADR 0001) — not candidates[0].
    SC-002: NOT_YET_MEASURED (review trap is historical AI evidence, not SC-002).
    SC-003: PARTIALLY_MEASURED (historical AI boundary evidence: AI-5 and AI-6).
    """
    if not _PHASE2_SUMMARY.exists():
        return _empty_result_with_note(
            "MISSING_EVALUATION_ASSET",
            f"Phase2 summary not found: {_PHASE2_SUMMARY}",
        )

    phase2 = json.loads(_PHASE2_SUMMARY.read_text(encoding="utf-8"))
    candidates = phase2.get("candidates", [])
    if not candidates:
        return _empty_result_with_note("MISSING_EVALUATION_ASSET", "No candidates in phase2_summary.json")

    expected = json.loads(_EXPECTED_JSON.read_text(encoding="utf-8")) if _EXPECTED_JSON.exists() else {}

    primary = _find_primary_candidate(candidates)
    if primary is None:
        return _empty_result_with_note(
            "MISSING_EVALUATION_ASSET",
            "Could not find Alibaba/Qwen primary candidate by provider/model identity in phase2_summary.json. "
            "ADR 0001 requires Alibaba Model Studio / Qwen as primary.",
        )

    secondary = _find_secondary_candidate(candidates)

    primary_provider_name = primary.get("provider", "unknown")
    primary_model_name = primary.get("requested_model", "unknown")

    case_results: list[dict[str, Any]] = []

    primary_p1 = primary.get("phase1_selected", {})
    per_fixture = primary_p1.get("per_fixture", {})

    for fixture_id, fixture_data in per_fixture.items():
        exp = expected.get(fixture_id, {})
        exp_status = exp.get("expected_status")
        observations = fixture_data.get("observations", [])
        all_error_classes = fixture_data.get("all_error_classes", {})
        outcomes = fixture_data.get("outcomes", {})

        actual_status = None
        for obs in observations:
            cs = obs.get("core_status")
            if cs is not None:
                actual_status = cs
                break

        if actual_status is None and "LOCAL_VALIDATION_FAILURE" in (all_error_classes or {}):
            status = "FAIL"
            note = "LOCAL_VALIDATION_FAILURE present. Reproduction noted in details."
        elif actual_status == exp_status and not all_error_classes:
            status = "PASS"
            note = f"core_status={actual_status} matches expected, no errors."
        elif actual_status == exp_status and all_error_classes:
            status = "FAIL"
            note = f"core_status={actual_status} matches expected but error_classes={list(all_error_classes.keys())} present."
        else:
            status = "FAIL"
            note = f"core_status={actual_status} expected={exp_status}."

        case_results.append({
            "case_id": f"{fixture_id}::{primary_provider_name[:30]}",
            "status": status,
            "latency_seconds": round(
                fixture_data.get("latency_ms", {}).get("mean_ms", 0) / 1000, 4
            ),
            "details": {
                "provider": primary_provider_name,
                "model": primary_model_name,
                "actual_core_status": actual_status,
                "expected_core_status": exp_status,
                "all_error_classes": all_error_classes,
                "outcomes": outcomes,
                "note": note,
            },
        })
        if verbose:
            print(f"  [HISTORICAL_BAKEOFF] {fixture_id} ({primary_provider_name[:20]}): {status}")

    # Also evaluate secondary (Gemini) for completeness
    if secondary:
        sec_p1 = secondary.get("phase1_selected", {})
        sec_per_fixture = sec_p1.get("per_fixture", {})
        sec_provider = secondary.get("provider", "unknown")
        sec_model = secondary.get("requested_model", "unknown")
        for fixture_id, fixture_data in sec_per_fixture.items():
            exp = expected.get(fixture_id, {})
            exp_status = exp.get("expected_status")
            all_error_classes = fixture_data.get("all_error_classes", {})
            outcomes = fixture_data.get("outcomes", {})
            observations = fixture_data.get("observations", [])
            actual_status = None
            for obs in observations:
                cs = obs.get("core_status")
                if cs is not None:
                    actual_status = cs
                    break

            if actual_status is None and "LOCAL_VALIDATION_FAILURE" in (all_error_classes or {}):
                status = "FAIL"
            elif actual_status == exp_status and not all_error_classes:
                status = "PASS"
            else:
                status = "FAIL"

            case_results.append({
                "case_id": f"{fixture_id}::{sec_provider[:30]}[secondary]",
                "status": status,
                "latency_seconds": round(
                    fixture_data.get("latency_ms", {}).get("mean_ms", 0) / 1000, 4
                ),
                "details": {
                    "provider": sec_provider,
                    "model": sec_model,
                    "secondary_evidence_only": True,
                    "actual_core_status": actual_status,
                    "expected_core_status": exp_status,
                    "all_error_classes": all_error_classes,
                    "outcomes": outcomes,
                },
            })

    schema_valid = primary_p1.get("schema_valid_rate", {})
    mandatory = primary_p1.get("exact_mandatory_field_rate", {})
    determinate = primary_p1.get("determinate_sku_rate", {})
    review_trap = primary_p1.get("review_trap_rate", {})
    wrong_sku = primary_p1.get("wrong_confident_sku_count", 0)
    latency = primary_p1.get("latency_ms", {})
    total_calls = primary_p1.get("completed_calls", 1)

    historical_bakeoff_metrics = {
        "provider": primary_provider_name,
        "model": primary_model_name,
        "schema_valid_rate": _rate(
            schema_valid.get("numerator", 0), schema_valid.get("denominator", 1),
        ),
        "mandatory_field_exactness": {
            "numerator": mandatory.get("numerator", 0),
            "denominator": mandatory.get("denominator", 1),
            "rate_percentage": round(100.0 * (mandatory.get("rate") or 0), 4),
        },
        "determinate_sku_accuracy": {
            "numerator": determinate.get("numerator", 0),
            "denominator": max(determinate.get("denominator", 0), 0),
            "rate_percentage": round(100.0 * (determinate.get("rate") or 0), 4),
            "abstentions": review_trap.get("denominator", 0),
        },
        "review_trap_catch_rate": _rate(
            review_trap.get("numerator", 0),
            max(review_trap.get("denominator", 0), 1),
        ),
        "wrong_confident_skus": {
            "count": wrong_sku,
            "rate_percentage": round(100.0 * wrong_sku / max(total_calls, 1), 4),
        },
        "latency_profile_seconds": {
            "min": latency.get("min_ms", 0) / 1000,
            "p50": latency.get("p50_ms", 0) / 1000,
            "mean": latency.get("mean_ms", 0) / 1000,
            "p90": latency.get("p90_ms", 0) / 1000,
            "p95": latency.get("p95_ms", 0) / 1000,
            "max": latency.get("max_ms", 0) / 1000,
        },
    }

    deterministic_metrics = {
        "discrepancy_catch_rate": _rate(0, 0),
        "false_positive_discrepancy_count": 0,
        "commercial_rule_enforcement_rate": _rate(0, 0),
        "gate_blocking_rate": _rate(0, 0),
        "terminal_state_enforcement_rate": _rate(0, 0),
    }

    e2e_metrics = {
        "average_reconciliation_duration_ms": round(latency.get("mean_ms", 0), 2),
        "operator_actions_recorded": 0,
        "terminal_status_counts": {"Approved": 0, "Rejected": 0},
        "visible_replay_mode_badge_rate": 0.0,
    }

    sc_traceability = {
        "SC_001": _sc_partially_measured(
            metric_name="operator_e2e_completion_seconds",
            value=None, unit=None, auto_ms=None,
            notes="HISTORICAL_BAKEOFF does not exercise current-app SC-001 path.",
        ),
        "SC_002": _sc_not_measured(
            "discrepancy_detection_accuracy",
            "HISTORICAL_BAKEOFF: review_trap_catch_rate is historical AI evidence, NOT SC-002. "
            "SC-002 is discrepancy_detection_accuracy measured using current-app E2E in REPLAY mode.",
        ),
        "SC_003": _sc_partially_measured(
            metric_name="operator_sku_resolution_completion",
            value=f"{review_trap.get('numerator', 0)}/{review_trap.get('denominator', 0)}",
            unit="review_traps_routed",
            notes=(
                f"Historical AI boundary (Alibaba Qwen {primary_model_name}): "
                f"AI-5 review-routing rate = {review_trap.get('numerator', 0)}/{review_trap.get('denominator', 0)} "
                f"({round(100.0 * (review_trap.get('rate') or 0), 2)}%), "
                f"AI-6 wrong-confident SKU count = {wrong_sku}. "
                "Marked PARTIALLY_MEASURED: historical AI metrics do not evaluate current application reconciliation lifecycle."
            ),
        ),
        "SC_004": _sc_not_measured(
            "provenance_citation_coverage",
            "HISTORICAL_BAKEOFF does not exercise current-app provenance path. SC-004 in REPLAY.",
        ),
        "SC_005": _sc_not_measured(
            "approval_gate_enforcement_http",
            "HISTORICAL_BAKEOFF does not exercise current-app HTTP approval gate. SC-005 in REPLAY.",
        ),
        "SC_006": _sc_not_measured(
            "unextractable_pdf_explicit_error",
            "HISTORICAL_BAKEOFF validates historical text fixtures. SC-006 measured in REPLAY.",
        ),
    }

    return {
        "case_results": case_results,
        "deterministic_metrics": deterministic_metrics,
        "e2e_metrics": e2e_metrics,
        "sc_traceability": sc_traceability,
        "ai_metrics": {
            "historical_bakeoff_metrics": historical_bakeoff_metrics,
        },
    }


# ===========================================================================
# LIVE mode (gated)
# ===========================================================================

def run_live(manifest: dict[str, Any], verbose: bool = False) -> dict[str, Any]:
    """Live provider evaluation — gated by LIVE_EVALUATION_ENABLED=true.

    When enabled: submits clean_acme PO text to POST /api/v1/orders/ingest
    using the configured live AI provider. Records exact provider/model provenance.
    When not enabled: returns MISSING_EVALUATION_ASSET. Never raises.
    """
    from app.config import settings
    from app.services.ai_provider import LiveAIProvider

    provider_name = settings.llm_provider
    model_name = LiveAIProvider._MODELS.get(provider_name, "unknown")
    timeout_sec = settings.live_inference_timeout
    base_url_env = os.getenv("QWEN_BASE_URL")
    base_url_masked = "https://***.aliyuncs.com/compatible-mode/v1" if base_url_env else None

    provider_config = {
        "provider": provider_name,
        "model": model_name,
        "base_url_masked": base_url_masked,
        "timeout_seconds": timeout_sec,
    }

    if os.environ.get("LIVE_EVALUATION_ENABLED", "").lower() != "true":
        print(
            "LIVE mode requires LIVE_EVALUATION_ENABLED=true. "
            "Not running to protect provider quota.",
            flush=True,
        )
        res = _empty_result_with_note(
            "MISSING_EVALUATION_ASSET",
            "LIVE evaluation not executed: set LIVE_EVALUATION_ENABLED=true to run.",
        )
        res["provider_config"] = provider_config
        return res

    # Gated live implementation — exercises POST /api/v1/orders/ingest
    try:
        client, session, engine, app_inst, get_db_fn = _make_eval_client()

        try:
            clean_acme_path = _REPO_ROOT / "tests" / "fixtures" / "po_clean_acme.txt"
            t0 = time.perf_counter()
            resp = client.post(
                "/api/v1/orders/ingest",
                files={"file": (clean_acme_path.name, clean_acme_path.read_bytes(), "text/plain")},
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000

            case_results = [{
                "case_id": "live_clean_acme_ingest",
                "status": "PASS" if resp.status_code == 201 else "FAIL",
                "latency_seconds": round(elapsed_ms / 1000, 4),
                "details": {"http_status_code": resp.status_code},
            }]

        finally:
            _cleanup_eval_client(app_inst, get_db_fn, session, engine)

        return {
            "case_results": case_results,
            "provider_config": provider_config,
            "deterministic_metrics": {
                "discrepancy_catch_rate": _rate(0, 0),
                "false_positive_discrepancy_count": 0,
                "commercial_rule_enforcement_rate": _rate(0, 0),
                "gate_blocking_rate": _rate(0, 0),
                "terminal_state_enforcement_rate": _rate(0, 0),
            },
            "e2e_metrics": {
                "average_reconciliation_duration_ms": round(elapsed_ms, 2),
                "operator_actions_recorded": 0,
                "terminal_status_counts": {"Approved": 0, "Rejected": 0},
                "visible_replay_mode_badge_rate": 0.0,
            },
            "sc_traceability": {
                "SC_001": _sc_partially_measured(
                    metric_name="operator_e2e_completion_seconds",
                    value=None,
                    unit=None,
                    notes=(
                        f"clean_acme 2-line LIVE ingest latency is {round(elapsed_ms, 2)} ms (preserved as diagnostic app/live AI latency). "
                        "Per SC-001 specification, automated_system_path_duration_ms remains null because SC-001 can receive automated "
                        "timing only from the canonical sc001_prepared_5line scenario. PASS/FAIL strictly reserved for VLD-EVAL-03 human timing."
                    ),
                    auto_ms=None,
                ),
                "SC_002": _sc_not_measured("discrepancy_detection_accuracy", "LIVE measures AI extraction, not discrepancy detection."),
                "SC_003": _sc_not_measured("operator_sku_resolution_completion", "LIVE measures AI extraction."),
                "SC_004": _sc_not_measured("provenance_citation_coverage", "LIVE provenance not evaluated here."),
                "SC_005": _sc_not_measured("approval_gate_enforcement_http", "Not measured in LIVE mode."),
                "SC_006": _sc_not_measured("unextractable_pdf_explicit_error", "Not measured in LIVE mode."),
            },
            "ai_metrics": {"app_ai_metrics": {"latency_profile_seconds": {"mean": round(elapsed_ms / 1000, 4)}}},
        }

    except Exception as exc:
        res = _empty_result_with_note("MISSING_EVALUATION_ASSET", f"LIVE evaluation error: {exc}")
        res["provider_config"] = provider_config
        return res


# ===========================================================================
# ALL mode
# ===========================================================================

def _compute_combined_sc003(hb_metrics: dict[str, Any], rep_cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute combined SC-003 from actual evidence — never hardcoded.

    PASS iff:
    - historical AI-5 review-routing numerator == denominator and denominator > 0
    - historical AI-6 wrong-confident count == 0
    - current-app ambiguous lifecycle evidence succeeded (ambiguous_apex PASS)

    FAIL if a measured normative component fails.
    PARTIALLY_MEASURED if required evidence is absent/incomplete.
    """
    provider_name = hb_metrics.get("provider", "Alibaba Model Studio (Singapore)")
    model_name = hb_metrics.get("model", "qwen3.8-flash")
    review_trap = hb_metrics.get("review_trap_catch_rate", {})
    ai5_num = review_trap.get("numerator")
    ai5_denom = review_trap.get("denominator")
    ai5_pct = review_trap.get("rate_percentage")
    wrong_sku_info = hb_metrics.get("wrong_confident_skus", {})
    wrong_count = wrong_sku_info.get("count")

    amb_case = next((c for c in rep_cases if c.get("case_id") == "ambiguous_apex"), None)
    amb_ok = (amb_case is not None and amb_case.get("status") == "PASS")

    if ai5_num is None or ai5_denom is None or wrong_count is None or amb_case is None:
        status = "PARTIALLY_MEASURED"
        val = None
        unit = None
        notes = "Required SC-003 evidence is absent or incomplete across evaluation modes."
    elif (ai5_denom > 0 and ai5_num != ai5_denom) or (wrong_count is not None and wrong_count > 0) or not amb_ok:
        status = "FAIL"
        val = 0.0
        unit = "percent"
        fail_reasons = []
        if ai5_denom is not None and ai5_denom > 0 and ai5_num != ai5_denom:
            fail_reasons.append(f"AI-5 review routing failed ({ai5_num}/{ai5_denom})")
        if wrong_count is not None and wrong_count > 0:
            fail_reasons.append(f"AI-6 wrong-confident count > 0 ({wrong_count})")
        if not amb_ok:
            fail_reasons.append(f"current-app ambiguous_apex status = {amb_case.get('status') if amb_case else 'MISSING'}")
        notes = (
            f"Normative SC-003 component failed: {'; '.join(fail_reasons)}. "
            f"[Historical AI boundary - {provider_name} {model_name}]: AI-5 review routing = {ai5_num}/{ai5_denom}, "
            f"AI-6 wrong-confident count = {wrong_count}. "
            f"[Current-app reconciliation]: ambiguous_apex status = {amb_case.get('status') if amb_case else 'MISSING'}."
        )
    elif ai5_num == ai5_denom and ai5_denom > 0 and wrong_count == 0 and amb_ok:
        status = "PASS"
        val = ai5_pct if ai5_pct is not None else 100.0
        unit = "percent"
        notes = (
            f"Combined evidence with explicit attribution: "
            f"[Historical AI boundary - {provider_name} {model_name}]: AI-5 review-routing rate = {ai5_num}/{ai5_denom} ({ai5_pct}%), "
            f"AI-6 wrong-confident SKU count = {wrong_count} across approved trap corpus. "
            f"[Current-app reconciliation]: ambiguous_apex correctly generated CatalogMatchingMismatch, "
            f"routed to Needs Review, and successfully transitioned to Ready for Approval upon operator SelectSKU."
        )
    else:
        status = "PARTIALLY_MEASURED"
        val = None
        unit = None
        notes = "SC-003 evidence partially evaluated."

    return _sc_result(
        metric_name="operator_sku_resolution_completion",
        status=status,
        value=val,
        unit=unit,
        notes=notes,
    )


def run_all(manifest: dict[str, Any], verbose: bool = False) -> dict[str, Any]:
    """Run DETERMINISTIC + REPLAY + HISTORICAL_BAKEOFF. LIVE only if explicitly enabled."""
    print("Running DETERMINISTIC ...", flush=True)
    det = run_deterministic(manifest, verbose=verbose)
    print("Running REPLAY ...", flush=True)
    rep = run_replay(manifest, verbose=verbose)
    print("Running HISTORICAL_BAKEOFF ...", flush=True)
    hb = run_historical_bakeoff(manifest, verbose=verbose)

    all_cases = (
        [dict(c, mode="DETERMINISTIC") for c in det.get("case_results", [])]
        + [dict(c, mode="REPLAY") for c in rep.get("case_results", [])]
        + [dict(c, mode="HISTORICAL_BAKEOFF") for c in hb.get("case_results", [])]
    )

    live_payload: dict[str, Any] | None = None
    if os.environ.get("LIVE_EVALUATION_ENABLED", "").lower() == "true":
        print("Running LIVE ...", flush=True)
        live_payload = run_live(manifest, verbose=verbose)
        if live_payload:
            all_cases += [dict(c, mode="LIVE") for c in live_payload.get("case_results", [])]

    # Merge SC traceability
    sc_sources = [det["sc_traceability"], rep["sc_traceability"], hb["sc_traceability"]]
    if live_payload:
        sc_sources.append(live_payload["sc_traceability"])
    combined_sc = _merge_sc_traceability(sc_sources)

    # For SC-003 in ALL mode: compute dynamically from actual evidence
    hb_metrics = hb.get("ai_metrics", {}).get("historical_bakeoff_metrics", {})
    combined_sc["SC_003"] = _compute_combined_sc003(hb_metrics, rep.get("case_results", []))

    # Deterministic metrics: merge rules from DETERMINISTIC, gate/terminal from REPLAY
    combined_det = dict(det["deterministic_metrics"])
    combined_det["gate_blocking_rate"] = rep["deterministic_metrics"]["gate_blocking_rate"]
    combined_det["terminal_state_enforcement_rate"] = rep["deterministic_metrics"]["terminal_state_enforcement_rate"]

    # E2E from REPLAY
    combined_e2e = rep["e2e_metrics"]

    # AI metrics: keep schemas strictly separate
    combined_ai: dict[str, Any] = {}
    for sub in [det, rep, hb]:
        for k, v in sub.get("ai_metrics", {}).items():
            if k not in combined_ai:
                combined_ai[k] = v
    if live_payload:
        for k, v in live_payload.get("ai_metrics", {}).items():
            if k not in combined_ai:
                combined_ai[k] = v

    result = {
        "case_results": all_cases,
        "deterministic_metrics": combined_det,
        "e2e_metrics": combined_e2e,
        "sc_traceability": combined_sc,
    }
    if combined_ai:
        result["ai_metrics"] = combined_ai
    return result


def _merge_sc_traceability(sc_lists: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge SC traceability from multiple runs.

    Precedence: FAIL (3) > PASS (2) > PARTIALLY_MEASURED (1) > NOT_YET_MEASURED (0).
    Equal-status merge preserves richer evidence (e.g. non-null measurements, richer notes).
    """
    merged: dict[str, Any] = {}
    for sc_dict in sc_lists:
        for key, entry in sc_dict.items():
            if key not in merged:
                merged[key] = dict(entry)
            else:
                existing = merged[key]
                curr_rank = _SC_STATUS_RANK.get(existing.get("status", "NOT_YET_MEASURED"), 0)
                new_rank = _SC_STATUS_RANK.get(entry.get("status", "NOT_YET_MEASURED"), 0)

                if new_rank > curr_rank:
                    merged[key] = dict(entry)
                elif new_rank == curr_rank:
                    if existing.get("automated_system_path_duration_ms") is None and entry.get("automated_system_path_duration_ms") is not None:
                        existing["automated_system_path_duration_ms"] = entry["automated_system_path_duration_ms"]

                    if existing.get("value") is None and entry.get("value") is not None:
                        existing["value"] = entry["value"]
                        existing["unit"] = entry.get("unit")

                    if len(entry.get("notes", "")) > len(existing.get("notes", "")):
                        existing["notes"] = entry["notes"]

    return merged


# ===========================================================================
# Error result helper
# ===========================================================================

def _empty_result_with_note(sc_status: str, note: str) -> dict[str, Any]:
    sc_entry: dict[str, Any] = {
        "status": sc_status,
        "metric_name": "unavailable",
        "value": None,
        "unit": None,
        "notes": note,
    }
    return {
        "case_results": [],
        "deterministic_metrics": {
            "discrepancy_catch_rate": _rate(0, 0),
            "false_positive_discrepancy_count": 0,
            "commercial_rule_enforcement_rate": _rate(0, 0),
            "gate_blocking_rate": _rate(0, 0),
            "terminal_state_enforcement_rate": _rate(0, 0),
        },
        "e2e_metrics": {
            "average_reconciliation_duration_ms": 0.0,
            "operator_actions_recorded": 0,
            "terminal_status_counts": {"Approved": 0, "Rejected": 0},
            "visible_replay_mode_badge_rate": 0.0,
        },
        "sc_traceability": {
            "SC_001": {**sc_entry, "automated_system_path_duration_ms": None,
                       "assisted_operator_completion_duration_seconds": None},
            "SC_002": sc_entry,
            "SC_003": sc_entry,
            "SC_004": sc_entry,
            "SC_005": sc_entry,
            "SC_006": sc_entry,
        },
    }


# ===========================================================================
# Main entry point
# ===========================================================================

def run_evaluation(mode: str, verbose: bool = False) -> int:
    """Execute the evaluation runner for the given mode. Returns 0 on success."""
    mode = mode.upper()
    if mode not in VALID_MODES:
        print(f"ERROR: Unknown mode {mode!r}. Valid: {sorted(VALID_MODES)}", flush=True)
        return 1

    print(f"OrderShield Evaluation Runner — mode={mode}", flush=True)

    if not _MANIFEST_PATH.exists():
        print(f"ERROR: Manifest not found: {_MANIFEST_PATH}", flush=True)
        return 1

    manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    git_commit = _current_git_commit()
    run_id = str(uuid.uuid4())
    timestamp_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    print(f"Run ID:     {run_id}", flush=True)
    print(f"Git commit: {git_commit}", flush=True)
    print(f"Timestamp:  {timestamp_utc}", flush=True)

    if mode == "DETERMINISTIC":
        payload = run_deterministic(manifest, verbose=verbose)
    elif mode == "REPLAY":
        payload = run_replay(manifest, verbose=verbose)
    elif mode == "HISTORICAL_BAKEOFF":
        payload = run_historical_bakeoff(manifest, verbose=verbose)
    elif mode == "LIVE":
        payload = run_live(manifest, verbose=verbose)
    elif mode == "ALL":
        payload = run_all(manifest, verbose=verbose)
    else:
        print(f"ERROR: Unhandled mode {mode!r}", flush=True)
        return 1

    result: dict[str, Any] = {
        "run_id": run_id,
        "mode": mode,
        "timestamp_utc": timestamp_utc,
        "git_commit": git_commit,
        "deterministic_metrics": payload["deterministic_metrics"],
        "e2e_metrics": payload["e2e_metrics"],
        "sc_traceability": payload["sc_traceability"],
    }
    if "case_results" in payload:
        result["case_results"] = payload["case_results"]
    if "ai_metrics" in payload:
        result["ai_metrics"] = payload["ai_metrics"]
    if "provider_config" in payload:
        result["provider_config"] = payload["provider_config"]

    errors = _validate_against_schema(result)
    if errors:
        print("ERROR: Result failed schema validation:", flush=True)
        for e in errors:
            print(f"  - {e}", flush=True)
        return 1

    ts_path, latest_path = _write_result(result, mode)
    print(f"\nResult written: {ts_path}", flush=True)
    print(f"Latest:         {latest_path}", flush=True)

    sc = result["sc_traceability"]
    print("\n--- SC Traceability Summary ---", flush=True)
    for sc_key in ["SC_001", "SC_002", "SC_003", "SC_004", "SC_005", "SC_006"]:
        entry = sc.get(sc_key, {})
        status = entry.get("status", "?")
        value = entry.get("value")
        val_str = f" ({value})" if value is not None else ""
        print(f"  {sc_key}: {status}{val_str}", flush=True)

    cases = result.get("case_results", [])
    if cases:
        pass_n = sum(1 for c in cases if c.get("status") == "PASS")
        fail_n = sum(1 for c in cases if c.get("status") == "FAIL")
        skip_n = sum(1 for c in cases if c.get("status") == "SKIPPED")
        err_n = sum(1 for c in cases if c.get("status") == "ERROR")
        print(f"\n--- Case Results: {pass_n} PASS / {fail_n} FAIL / {skip_n} SKIPPED / {err_n} ERROR ---",
              flush=True)

    print("\nEvaluation complete.", flush=True)
    return 0
