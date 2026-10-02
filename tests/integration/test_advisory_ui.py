"""Integration and regression test suite for Advisory UI component, static wiring, and contracts.

Validates:
1. Static contract (HTML structure, required IDs, scripts, preserved strings).
2. Client safety and cleanliness (no innerHTML, eval, storage, external resources, or write calls).
3. State management guarantees in app.js (draft ID matching, schema version, stale invalidation).
4. Static file serving via TestClient (/static mount, SPA redirect, non-empty resources).
5. UI data contract anti-drift across all 3 canonical fixtures (clean-acme, ambiguous-apex, discrepancy-apex).
6. Read-only DB snapshot invariance during advisory inspection.
7. Grounded document evidence coordinate verification (line_number and char_offset).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.routing import Mount

from app.cli import seed_baseline
from app.main import create_app
from app.models.entities import (
    AuditEvent,
    CatalogProduct,
    ContractPriceTier,
    CustomerContract,
    DiscrepancyFlag,
    DraftLineItem,
    FieldProvenance,
    OrderDraft,
    PurchaseOrderDocument,
    VerifiedOrderRecord,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def seeded_db(db_session: Session) -> Session:
    seed_baseline(db_session)
    db_session.commit()
    return db_session


def _take_db_snapshot(db: Session) -> dict[str, list[dict[str, Any]]]:
    tables = [
        PurchaseOrderDocument,
        OrderDraft,
        DraftLineItem,
        FieldProvenance,
        DiscrepancyFlag,
        AuditEvent,
        VerifiedOrderRecord,
        CatalogProduct,
        CustomerContract,
        ContractPriceTier,
    ]
    snapshot: dict[str, list[dict[str, Any]]] = {}
    with db.no_autoflush:
        for t in tables:
            rows = db.scalars(select(t)).all()
            serialized = []
            for r in rows:
                row_dict = {}
                for col in r.__table__.columns:
                    val = getattr(r, col.name)
                    if isinstance(val, (int, str, bool, float, type(None))):
                        row_dict[col.name] = val
                    else:
                        row_dict[col.name] = str(val)
                serialized.append(row_dict)
            serialized.sort(key=lambda x: str(x.get("id", x.get("sku", ""))))
            snapshot[t.__tablename__] = serialized
    return snapshot


# -----------------------------------------------------------------------------
# 1. Static Contract & Markup Tests
# -----------------------------------------------------------------------------

def test_static_html_contract_and_frozen_strings():
    """Validates that index.html contains all required IDs, scripts, and frozen strings."""
    index_html = (REPO_ROOT / "app/static/index.html").read_text(encoding="utf-8")

    # Required IDs for advisory panel contract
    required_ids = [
        "advisory-section",
        "advisory-badge",
        "advisory-disclaimer",
        "advisory-load",
        "advisory-hint",
        "advisory-meta",
        "advisory-priority",
        "advisory-hints",
        "advisory-confidence",
        "advisory-trace",
    ]
    for req_id in required_ids:
        assert f'id="{req_id}"' in index_html, f"Missing required ID '{req_id}' in index.html"

    # Mandatory visible badge text and accessibility attributes
    assert "ADVISORY ONLY" in index_html
    assert "badge--advisory" in index_html
    assert 'aria-label="Advisory (non-authoritative)"' in index_html

    # Script tag presence and relative load order
    assert "js/components/advisory_panel.js" in index_html
    assert "js/components/provenance_drawer.js" in index_html
    assert "js/app.js" in index_html

    pos_drawer = index_html.index("js/components/provenance_drawer.js")
    pos_advisory = index_html.index("js/components/advisory_panel.js")
    pos_app = index_html.index("js/app.js")
    assert pos_drawer < pos_advisory < pos_app, "advisory_panel.js must be loaded after drawer and before app.js"

    # Frozen string contracts from earlier milestones
    assert 'id="replay-banner"' in index_html
    assert "⚠️ DEMO / REPLAY MODE (NON-LIVE FIXTURE DATA)" in index_html
    assert 'id="audit-load"' in index_html
    assert "source-evidence-control" in index_html


# -----------------------------------------------------------------------------
# 2. Client Safety & Cleanliness Tests
# -----------------------------------------------------------------------------

def test_client_safety_tokens_and_no_write_mutations():
    """Ensures no dangerous methods, forbidden APIs, external resources, or unwanted mutations exist."""
    adv_file = REPO_ROOT / "app/static/js/components/advisory_panel.js"
    assert adv_file.is_file(), "advisory_panel.js must exist"
    adv_code = adv_file.read_text(encoding="utf-8")

    forbidden_in_component = [
        "innerHTML",
        "eval(",
        "new Function",
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "http://",
        "https://",
        "//cdn",
        "fetch(",
    ]
    for token in forbidden_in_component:
        assert token not in adv_code, f"Forbidden token '{token}' in advisory_panel.js"

    # Check git diff of app.js to ensure no forbidden tokens or draft-mutating methods were introduced
    diff_output = subprocess.check_output(
        ["git", "diff", "app/static/js/app.js"],
        cwd=REPO_ROOT,
    ).decode("utf-8")

    forbidden_in_diff = [
        "innerHTML",
        "eval(",
        "new Function",
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "http://",
        "https://",
        "//cdn",
    ]
    for line in diff_output.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            for token in forbidden_in_diff:
                assert token not in line, f"Forbidden token '{token}' found in app.js diff: {line}"
            # Ensure no new PATCH, DELETE, or POST calls were added in advisory logic
            if 'method: "PATCH"' in line or 'method: "DELETE"' in line or 'method: "POST"' in line:
                assert False, f"Unauthorized mutation method introduced in app.js diff: {line}"


# -----------------------------------------------------------------------------
# 3. State Management Guarantees in app.js
# -----------------------------------------------------------------------------

def test_app_state_guarantees_present():
    """Validates required state invariant strings in app.js."""
    app_js = (REPO_ROOT / "app/static/js/app.js").read_text(encoding="utf-8")

    assert "state.advisory.draftId === state.draft.draft_id" in app_js, (
        "app.js must guard advisory rendering by draft ID match"
    )
    assert 'SUPPORTED_ADVISORY_SCHEMA_VERSION = "1.0.0"' in app_js or "SUPPORTED_ADVISORY_SCHEMA_VERSION" in app_js, (
        "app.js must define SUPPORTED_ADVISORY_SCHEMA_VERSION"
    )
    assert "state.advisory.stale = true" in app_js, (
        "app.js must mark advisory state as stale on mutations"
    )
    assert "state.draft.is_replay_mode === true" in app_js, (
        "Frozen string state.draft.is_replay_mode === true must remain in app.js"
    )


# -----------------------------------------------------------------------------
# 4. Static File Serving via TestClient
# -----------------------------------------------------------------------------

def test_static_file_serving_and_single_mount(client):
    """Verifies that static assets are served, root redirects with 307, and mount count is 1."""
    app = create_app()
    mounts = [route for route in app.routes if isinstance(route, Mount)]
    assert [mount.path for mount in mounts] == ["/static"], "Only /static mount must exist"

    # Static assets exist and return 200 with non-empty content
    resp_index = client.get("/static/index.html")
    assert resp_index.status_code == 200
    assert len(resp_index.text.strip()) > 0
    assert "OrderShield" in resp_index.text

    resp_panel = client.get("/static/js/components/advisory_panel.js")
    assert resp_panel.status_code == 200
    assert len(resp_panel.text.strip()) > 0
    assert "OrderShieldAdvisoryPanel" in resp_panel.text

    resp_css = client.get("/static/css/styles.css")
    assert resp_css.status_code == 200
    assert len(resp_css.text.strip()) > 0
    assert ".advisory" in resp_css.text
    assert ".badge--advisory" in resp_css.text

    # Root redirect to SPA index.html via HTTP 307
    root_resp = client.get("/", follow_redirects=False)
    assert root_resp.status_code == 307
    assert root_resp.headers["location"] == "/static/index.html"


# -----------------------------------------------------------------------------
# 5. UI Data Contract Anti-Drift Tests (all 3 fixtures)
# -----------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fixture_name,expected_status,expect_successful_plans",
    [
        ("fixture-clean-acme", "ready_no_action_needed", False),
        ("fixture-ambiguous-apex", "actionable_plans_found", True),
        ("fixture-discrepancy-apex", "blocked_no_internal_plan", False),
    ],
)
def test_ui_data_contract_anti_drift(
    client,
    seeded_db,
    no_external_network,
    fixture_name: str,
    expected_status: str,
    expect_successful_plans: bool,
):
    """Ensures the server returns exact data structures expected by the advisory UI panel."""
    # Ingest fixture
    ingest_resp = client.post(f"/api/v1/fixtures/{fixture_name}/ingest")
    assert ingest_resp.status_code == 201
    draft_id = ingest_resp.json()["draft_id"]

    # Fetch advisory payload
    adv_resp = client.get(f"/api/v1/drafts/{draft_id}/advisory")
    assert adv_resp.status_code == 200
    assert adv_resp.headers["cache-control"] == "no-store"
    adv = adv_resp.json()

    # 1. Top-level metadata
    assert adv["schema_version"] == "1.0.0"
    assert adv["advisory_status"] == expected_status
    assert set(adv["included_sections"]) == {
        "baseline",
        "counterfactuals",
        "review_priority",
        "sku_confidence",
        "trace",
    }
    assert adv["sections_omitted"] == []

    # 2. Baseline section
    base = adv["baseline"]
    assert base is not None
    assert "status_persisted" in base
    assert "simulated_status" in base
    assert isinstance(base["is_stale_evaluation"], bool)
    assert isinstance(base["blockers"], list)
    for blocker in base["blockers"]:
        assert "discrepancy_type" in blocker
        assert "line_number" in blocker
        if blocker["price_source"] is not None:
            ps = blocker["price_source"]
            assert "contract_id" in ps
            assert "tier_id" in ps
            assert "min_quantity" in ps
            assert "tier_price" in ps
    assert "order_arithmetic" in base
    arith = base["order_arithmetic"]
    if arith["extracted_order_total"] is None:
        assert arith["extracted_order_total_missing_reason"] is not None

    # 3. Counterfactuals section
    cf = adv["counterfactuals"]
    assert cf is not None
    assert cf["limits"]["max_depth"] >= 1
    assert cf["limits"]["max_scenarios"] >= 1
    assert isinstance(cf["coverage"]["is_truncated"], bool)
    assert isinstance(cf["coverage"]["search_is_exhaustive"], bool)
    assert isinstance(cf["successful_plans"], list)

    if expect_successful_plans:
        assert len(cf["successful_plans"]) > 0
        plan = cf["successful_plans"][0]
        assert "plan_id" in plan
        assert "simulated_status" in plan
        assert "explanation" in plan
        assert len(plan["actions"]) > 0
        for act in plan["actions"]:
            assert "action_type" in act
            assert "line_number" in act
            assert "sku" in act
        # Specific check for ambiguous apex: SelectSKU for SKU-WRAP-15
        if fixture_name == "fixture-ambiguous-apex":
            assert any(
                a["action_type"] == "SelectSKU" and a.get("sku") == "SKU-WRAP-15"
                for p in cf["successful_plans"]
                for a in p["actions"]
            )
    else:
        assert len(cf["successful_plans"]) == 0

    if fixture_name == "fixture-discrepancy-apex":
        assert cf["requires_external_input"] is not None
        assert cf["requires_external_input"]["is_required"] is True
        assert len(cf["requires_external_input"]["reasons"]) > 0
        assert cf["coverage"]["interpretation_note"] is not None

    # 4. Review Priority section
    rp = adv["review_priority"]
    assert rp is not None
    assert isinstance(rp["review_priority"], float)
    assert 0.0 <= rp["review_priority"] <= 1.0
    assert rp["review_priority_label"] in ("low", "medium", "high")
    assert "inputs_used" in rp
    assert "match_confidence_source" in rp["inputs_used"]
    assert "note" in rp["inputs_used"]

    # 5. SKU Confidence section
    sc = adv["sku_confidence"]
    assert sc is not None
    assert isinstance(sc["lines"], list)
    for ln in sc["lines"]:
        assert "line_number" in ln
        assert "customer_description" in ln
        assert "persisted_label" in ln
        assert "confidence_source" in ln
        if ln["confidence_source"] == "mapped_from_label":
            assert ln["evaluated_confidence"] is not None
        if ln["candidates"]:
            for c in ln["candidates"]:
                assert "sku" in c
                assert "score" in c
                assert "label" in c

    # 6. Trace section
    tr = adv["trace"]
    assert tr is not None
    assert isinstance(tr["steps"], list)
    assert len(tr["steps"]) > 0
    for idx, step in enumerate(tr["steps"], start=1):
        assert step["step_index"] == idx
        assert "stage" in step
        assert "subject" in step
        assert "rule_or_decision" in step
        assert "outcome" in step
        assert "explanation" in step
        assert isinstance(step["evidence"], list)
        for ev in step["evidence"]:
            assert ev["source_type"] in ("document", "reference_data", "derived")


# -----------------------------------------------------------------------------
# 6. Read-Only Snapshot Invariance Test
# -----------------------------------------------------------------------------

def test_advisory_inspection_is_strictly_read_only(client, seeded_db, no_external_network):
    """Verifies that calling advisory (even repeatedly) creates zero DB mutations or AuditEvents."""
    # Ingest ambiguous draft
    ingest_resp = client.post("/api/v1/fixtures/fixture-ambiguous-apex/ingest")
    assert ingest_resp.status_code == 201
    draft_id = ingest_resp.json()["draft_id"]

    # Snapshot before advisory calls
    snap_before = _take_db_snapshot(seeded_db)
    events_before = seeded_db.scalars(
        select(AuditEvent).where(AuditEvent.draft_id == draft_id)
    ).all()
    events_count_before = len(events_before)

    # Call advisory multiple times with full query
    query = "?sections=baseline,counterfactuals,review_priority,sku_confidence,trace"
    for _ in range(3):
        resp = client.get(f"/api/v1/drafts/{draft_id}/advisory{query}")
        assert resp.status_code == 200

    # Snapshot after advisory calls
    snap_after = _take_db_snapshot(seeded_db)
    events_after = seeded_db.scalars(
        select(AuditEvent).where(AuditEvent.draft_id == draft_id)
    ).all()

    assert snap_before == snap_after, "Database snapshot must remain strictly unchanged"
    assert len(events_after) == events_count_before, "No new AuditEvents may be created by advisory"


# -----------------------------------------------------------------------------
# 7. Grounded Document Evidence Coordinate Verification
# -----------------------------------------------------------------------------

@pytest.mark.parametrize(
    "fixture_name,fixture_file",
    [
        ("fixture-clean-acme", "po_clean_acme.txt"),
        ("fixture-ambiguous-apex", "po_ambiguous_apex.txt"),
        ("fixture-discrepancy-apex", "po_discrepancy_apex.txt"),
    ],
)
def test_grounded_document_evidence_coordinates(
    client,
    seeded_db,
    no_external_network,
    fixture_name: str,
    fixture_file: str,
):
    """Verifies that all document evidence citations resolve verbatim in the source PO text."""
    raw_text = (REPO_ROOT / f"tests/fixtures/{fixture_file}").read_text(encoding="utf-8")
    raw_lines = raw_text.splitlines()

    ingest_resp = client.post(f"/api/v1/fixtures/{fixture_name}/ingest")
    assert ingest_resp.status_code == 201
    draft_id = ingest_resp.json()["draft_id"]

    adv_resp = client.get(f"/api/v1/drafts/{draft_id}/advisory")
    assert adv_resp.status_code == 200
    adv = adv_resp.json()

    doc_evidence_count = 0
    for step in adv.get("trace", {}).get("steps", []):
        for ev in step.get("evidence", []):
            if ev.get("source_type") == "document" and ev.get("document_evidence"):
                doc_evidence_count += 1
                dev = ev["document_evidence"]
                snippet = dev["verbatim_snippet"]
                loc = dev["location"]
                if loc["type"] == "txt":
                    line_idx = loc["line_number"] - 1
                    char_offset = loc["char_offset"]
                    assert 0 <= line_idx < len(raw_lines), f"Line {loc['line_number']} out of bounds"
                    line_content = raw_lines[line_idx]
                    actual = line_content[char_offset : char_offset + len(snippet)]
                    assert actual == snippet, (
                        f"{fixture_name}: snippet mismatch at line {loc['line_number']}, "
                        f"offset {char_offset}: expected '{snippet}', got '{actual}'"
                    )

    assert doc_evidence_count > 0, f"Expected document evidence citations for {fixture_name}"
