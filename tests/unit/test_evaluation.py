"""Unit and integration tests for OrderShield evaluation runner (app/evaluation/runner.py).

Comprehensive coverage for VLD-EVAL-02R2:
- Strict jsonschema validation against docs/evaluation/results.schema.json
- Negative schema tests (wrong types, missing required fields, invalid enums)
- DETERMINISTIC mode: pure rules-engine, zero network, 5 seeded discrepancy categories
  detected with exact line, Blocking severity, and Unresolved state (detected == seeded).
  DET-3 commercial rules evaluated separately (tier price, MOQ, package increment, override prohibition).
  Zero false positives, SC-001/003/004/005/006 NOT_YET_MEASURED.
- REPLAY mode: current-app E2E HTTP evaluation via TestClient:
  * clean_acme: SC-004 provenance verified against raw source text, diagnostic latency.
  * SC-001: automated_system_path_duration_ms null pending 5-line replay extraction asset.
  * discrepancy_apex: SC-002 PASS (State A -> B -> C stateful lifecycle).
  * ambiguous_apex: SC-003 PARTIALLY_MEASURED (operator SKU selection verified, full trap corpus evidence required).
  * DET-4: HTTP 409 DraftNotReadyForApprovalError, 0 VerifiedOrder creation, draft remains Needs Review.
  * DET-5: Terminal-state immutability verified for both Approved and Rejected drafts (HTTP 409).
  * SC-005: PASS (DET-4 + DET-5).
  * SC-006: PARTIALLY_MEASURED (unextractable PDF HTTP 400 + 0 persistence verified; live components require live run).
- HISTORICAL_BAKEOFF mode: Alibaba Qwen primary by explicit identity (ADR 0001),
  Gemini secondary, historical failures kept as FAIL, SC-002 NOT_YET_MEASURED,
  SC-003 PARTIALLY_MEASURED (AI-5 review routing + AI-6 wrong-confident count).
- LIVE mode: exact provider/model provenance recorded from settings, gated by env var.
- ALL mode merge semantics: FAIL dominates PASS > PARTIALLY_MEASURED > NOT_YET_MEASURED;
  equal-status merge preserves richer evidence; SC-003 combines AI-boundary + app reconciliation.
- Invariant: SC-001 is NEVER PASS in automated runner output.
- Zero-network safety: verified with no_external_network socket-blocking fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import jsonschema
import pytest

from app.evaluation.runner import (
    VALID_MODES,
    _compute_combined_sc003,
    _current_git_commit,
    _empty_result_with_note,
    _find_primary_candidate,
    _find_secondary_candidate,
    _load_schema,
    _make_in_memory_session,
    _merge_sc_traceability,
    _rate,
    _validate_against_schema,
    run_all,
    run_deterministic,
    run_evaluation,
    run_historical_bakeoff,
    run_live,
    run_replay,
)

# ---------------------------------------------------------------------------
# Fixtures and Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "docs" / "evaluation" / "ground_truth_manifest.json"
SCHEMA_PATH = REPO_ROOT / "docs" / "evaluation" / "results.schema.json"
RESULTS_DIR = REPO_ROOT / "docs" / "evaluation" / "results"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def results_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def assert_schema_valid(result: dict) -> None:
    """Validate with both jsonschema library directly and runner's validator."""
    schema = _load_schema()
    jsonschema.validate(result, schema)
    errors = _validate_against_schema(result)
    assert not errors, f"Runner validation failed: {errors}"


# ---------------------------------------------------------------------------
# Unit tests: _rate helper
# ---------------------------------------------------------------------------

def test_rate_zero_denominator():
    r = _rate(0, 0)
    assert r["denominator"] == 0
    assert r["numerator"] == 0
    assert r["rate_percentage"] == 0.0


def test_rate_full():
    r = _rate(10, 10)
    assert r["rate_percentage"] == 100.0


def test_rate_partial():
    r = _rate(3, 4)
    assert r["rate_percentage"] == 75.0


# ---------------------------------------------------------------------------
# Schema Validation Tests (Section G: Real JSON Schema Validation)
# ---------------------------------------------------------------------------

def test_validate_accepts_valid_minimal_result():
    result = _empty_result_with_note("NOT_YET_MEASURED", "test note")
    result.update({
        "run_id": "test-run-123",
        "mode": "DETERMINISTIC",
        "timestamp_utc": "2026-10-02T00:00:00Z",
        "git_commit": "abc1234",
    })
    assert_schema_valid(result)


def test_validate_rejects_missing_required_top_level_field():
    incomplete = {
        "run_id": "x",
        "mode": "DETERMINISTIC",
        "timestamp_utc": "2026-10-02T00:00:00Z",
    }
    errors = _validate_against_schema(incomplete)
    assert any("git_commit" in e or "required" in e.lower() for e in errors)


def test_validate_rejects_missing_nested_required_field():
    result = _empty_result_with_note("NOT_YET_MEASURED", "test note")
    result.update({
        "run_id": "test-run-123",
        "mode": "DETERMINISTIC",
        "timestamp_utc": "2026-10-02T00:00:00Z",
        "git_commit": "abc1234",
    })
    del result["deterministic_metrics"]["discrepancy_catch_rate"]
    errors = _validate_against_schema(result)
    assert any("discrepancy_catch_rate" in e for e in errors)


def test_validate_rejects_wrong_nested_type():
    result = _empty_result_with_note("NOT_YET_MEASURED", "test note")
    result.update({
        "run_id": "test-run-123",
        "mode": "DETERMINISTIC",
        "timestamp_utc": "2026-10-02T00:00:00Z",
        "git_commit": "abc1234",
    })
    result["deterministic_metrics"]["false_positive_discrepancy_count"] = "not_an_int"
    errors = _validate_against_schema(result)
    assert any("false_positive_discrepancy_count" in e or "integer" in e.lower() for e in errors)


def test_validate_rejects_invalid_mode_enum():
    result = _empty_result_with_note("NOT_YET_MEASURED", "test")
    result.update({
        "run_id": "x",
        "mode": "BOGUS_MODE",
        "timestamp_utc": "2026-10-02T00:00:00Z",
        "git_commit": "abc1234",
    })
    errors = _validate_against_schema(result)
    assert any("mode" in e.lower() or "BOGUS_MODE" in e for e in errors)


def test_validate_rejects_invalid_sc_enum():
    result = _empty_result_with_note("NOT_YET_MEASURED", "test")
    result.update({
        "run_id": "x",
        "mode": "DETERMINISTIC",
        "timestamp_utc": "2026-10-02T00:00:00Z",
        "git_commit": "abc1234",
    })
    result["sc_traceability"]["SC_001"]["status"] = "INVALID_STATUS"
    errors = _validate_against_schema(result)
    assert any("status" in e.lower() or "INVALID_STATUS" in e for e in errors)


def test_validate_rejects_invalid_case_result_status():
    result = _empty_result_with_note("NOT_YET_MEASURED", "test")
    result.update({
        "run_id": "x",
        "mode": "DETERMINISTIC",
        "timestamp_utc": "2026-10-02T00:00:00Z",
        "git_commit": "abc1234",
        "case_results": [{"case_id": "c1", "status": "UNKNOWN_RESULT"}],
    })
    errors = _validate_against_schema(result)
    assert any("case_results" in e or "status" in e.lower() or "UNKNOWN_RESULT" in e for e in errors)


def test_validate_rejects_malformed_ai_metric_object():
    result = _empty_result_with_note("NOT_YET_MEASURED", "test")
    result.update({
        "run_id": "x",
        "mode": "HISTORICAL_BAKEOFF",
        "timestamp_utc": "2026-10-02T00:00:00Z",
        "git_commit": "abc1234",
        "ai_metrics": {
            "historical_bakeoff_metrics": {
                "schema_valid_rate": "100%",
            }
        },
    })
    errors = _validate_against_schema(result)
    assert any("schema_valid_rate" in e or "object" in e.lower() for e in errors)


def test_sc001_cannot_be_pass_in_automated_output():
    """Invariant: runner validator strictly forbids SC_001 = PASS."""
    result = _empty_result_with_note("NOT_YET_MEASURED", "test")
    result.update({
        "run_id": "x",
        "mode": "DETERMINISTIC",
        "timestamp_utc": "2026-10-02T00:00:00Z",
        "git_commit": "abc1234",
    })
    result["sc_traceability"]["SC_001"]["status"] = "PASS"
    errors = _validate_against_schema(result)
    assert any("INVARIANT VIOLATION" in e or "SC_001" in e for e in errors)


# ---------------------------------------------------------------------------
# DETERMINISTIC Mode Tests
# ---------------------------------------------------------------------------

def test_deterministic_detects_all_five_seeded_discrepancies(manifest):
    """DET-1 / SC-002: PASS only when detected == seeded (5/5), verified type, line, severity, state."""
    payload = run_deterministic(manifest)
    det = payload["deterministic_metrics"]
    assert det["discrepancy_catch_rate"]["numerator"] == 5
    assert det["discrepancy_catch_rate"]["denominator"] == 5
    assert det["discrepancy_catch_rate"]["rate_percentage"] == 100.0
    assert payload["sc_traceability"]["SC_002"]["status"] == "PASS"


def test_deterministic_det3_commercial_rule_enforcement_separate(manifest):
    """DET-3: Evaluates 4 distinct commercial rules separately from discrepancy catch rate."""
    payload = run_deterministic(manifest)
    comm = payload["deterministic_metrics"]["commercial_rule_enforcement_rate"]
    assert comm["numerator"] == 4
    assert comm["denominator"] == 4
    assert comm["rate_percentage"] == 100.0


def test_deterministic_zero_false_positives(manifest):
    """DET-2: False-positive rate on clean inputs."""
    payload = run_deterministic(manifest)
    det = payload["deterministic_metrics"]
    assert det["false_positive_discrepancy_count"] == 0
    assert det["false_positive_rate"] == 0.0


def test_deterministic_sc_traceability_boundaries(manifest):
    """DETERMINISTIC leaves SC-001, SC-003, SC-004, SC-005, SC-006 NOT_YET_MEASURED (SC-001 PARTIALLY)."""
    payload = run_deterministic(manifest)
    sc = payload["sc_traceability"]
    assert sc["SC_001"]["status"] == "PARTIALLY_MEASURED"
    assert sc["SC_001"]["automated_system_path_duration_ms"] is None
    assert sc["SC_002"]["status"] == "PASS"
    assert sc["SC_003"]["status"] == "NOT_YET_MEASURED"
    assert sc["SC_004"]["status"] == "NOT_YET_MEASURED"
    assert sc["SC_005"]["status"] == "NOT_YET_MEASURED"
    assert sc["SC_006"]["status"] == "NOT_YET_MEASURED"


def test_deterministic_result_schema_valid(manifest):
    payload = run_deterministic(manifest)
    payload.update({
        "run_id": "test-det-run",
        "mode": "DETERMINISTIC",
        "timestamp_utc": "2026-10-02T00:00:00Z",
        "git_commit": "abc1234",
    })
    assert_schema_valid(payload)


# ---------------------------------------------------------------------------
# REPLAY Mode Tests (Application HTTP/API Evaluation)
# ---------------------------------------------------------------------------

def test_replay_exercises_all_app_scenarios(manifest):
    """REPLAY exercises clean_acme, discrepancy_apex, ambiguous_apex, det5, unextractable_pdf."""
    payload = run_replay(manifest)
    cases = payload["case_results"]
    case_ids = {c["case_id"] for c in cases}
    assert "clean_acme" in case_ids
    assert "discrepancy_apex" in case_ids
    assert "ambiguous_apex" in case_ids
    assert "det5_terminal_immutability" in case_ids
    assert "unextractable_pdf" in case_ids


def test_replay_sc001_automated_duration_null_per_spec(manifest):
    """SC-001: automated_system_path_duration_ms is null because no 5-line replay asset exists."""
    payload = run_replay(manifest)
    sc001 = payload["sc_traceability"]["SC_001"]
    assert sc001["status"] == "PARTIALLY_MEASURED"
    assert sc001["automated_system_path_duration_ms"] is None
    assert sc001["assisted_operator_completion_duration_seconds"] is None
    assert payload["e2e_metrics"]["average_reconciliation_duration_ms"] > 0


def test_replay_measures_sc004_provenance_citations_against_source(manifest):
    """SC-004: Validates each provenance field against raw source text (100.0% = PASS)."""
    payload = run_replay(manifest)
    sc004 = payload["sc_traceability"]["SC_004"]
    assert sc004["status"] == "PASS"
    assert sc004["value"] == 100.0


def test_replay_measures_sc002_current_app_discrepancies(manifest):
    """SC-002: Measured via discrepancy_apex lifecycle."""
    payload = run_replay(manifest)
    sc002 = payload["sc_traceability"]["SC_002"]
    assert sc002["status"] == "PASS"


def test_replay_measures_sc003_partially_measured(manifest):
    """SC-003: ambiguous_apex alone is PARTIALLY_MEASURED (requires full trap corpus evidence)."""
    payload = run_replay(manifest)
    sc003 = payload["sc_traceability"]["SC_003"]
    assert sc003["status"] == "PARTIALLY_MEASURED"


def test_replay_measures_sc005_det4_and_det5(manifest):
    """SC-005: DET-4 gate blocking + DET-5 terminal immutability for Approved and Rejected drafts."""
    payload = run_replay(manifest)
    sc005 = payload["sc_traceability"]["SC_005"]
    assert sc005["status"] == "PASS"
    assert sc005["value"] == "100.0"


def test_det5_approved_and_rejected_draft_ids_are_distinct(manifest):
    """DET-5: Prove approved and rejected draft IDs are distinct and both terminal states are exercised."""
    payload = run_replay(manifest)
    det5_case = next(c for c in payload["case_results"] if c["case_id"] == "det5_terminal_immutability")
    assert det5_case["status"] == "PASS"
    details = det5_case["details"]
    assert details["approved_draft_id"] != details["rejected_draft_id"]
    assert details["approved_draft_id"] is not None
    assert details["rejected_draft_id"] is not None
    assert details["distinct_draft_ids_verified"] is True
    assert details["approved_draft_rejections_verified"] is True
    assert details["rejected_draft_rejections_verified"] is True
    assert details["canonical_status_code"] == 409
    assert details["canonical_error"] == "TerminalDraftConflictError"


def test_replay_measures_sc006_partially_measured(manifest):
    """SC-006: Offline REPLAY returns PARTIALLY_MEASURED, proves zero provider calls, zero persistence, HTTP 400."""
    payload = run_replay(manifest)
    sc006 = payload["sc_traceability"]["SC_006"]
    assert sc006["status"] == "PARTIALLY_MEASURED"
    unext_case = next(c for c in payload["case_results"] if c["case_id"] == "unextractable_pdf")
    assert unext_case["status"] == "PASS"
    details = unext_case["details"]
    assert details["provider_invocation_count"] == 0
    assert details["http_status_code"] == 400
    assert details["error"] == "UnextractableTextError"
    assert details["draft_count_unchanged"] is True
    assert details["no_provider_invocation_and_no_fixture_substitution"] is True


def test_replay_verifies_is_replay_mode_badge(manifest):
    payload = run_replay(manifest)
    badge_rate = payload["e2e_metrics"]["visible_replay_mode_badge_rate"]
    assert badge_rate == 1.0


def test_replay_result_schema_valid(manifest):
    payload = run_replay(manifest)
    payload.update({
        "run_id": "test-replay-run",
        "mode": "REPLAY",
        "timestamp_utc": "2026-10-02T00:00:00Z",
        "git_commit": "abc1234",
    })
    assert_schema_valid(payload)


# ---------------------------------------------------------------------------
# HISTORICAL_BAKEOFF Mode Tests
# ---------------------------------------------------------------------------

def test_historical_bakeoff_selects_alibaba_qwen_primary(manifest):
    """Primary candidate must be Alibaba Qwen (ADR 0001), not candidates[0]."""
    payload = run_historical_bakeoff(manifest)
    hb = payload["ai_metrics"]["historical_bakeoff_metrics"]
    provider = hb["provider"].lower()
    model = hb["model"].lower()
    assert "alibaba" in provider or "qwen" in provider or "qwen" in model


def test_find_primary_candidate_does_not_blindly_pick_index_zero():
    mock_candidates = [
        {"provider": "Google Gemini API", "requested_model": "gemini-3.5-flash-lite"},
        {"provider": "Alibaba Model Studio (Singapore)", "requested_model": "qwen3.8-flash"},
    ]
    cand = _find_primary_candidate(mock_candidates)
    assert cand is not None
    assert "Alibaba" in cand["provider"]


def test_historical_bakeoff_sc_boundaries(manifest):
    """SC-002 is NOT_YET_MEASURED; SC-003 is PARTIALLY_MEASURED with AI-5/AI-6 evidence."""
    payload = run_historical_bakeoff(manifest)
    sc = payload["sc_traceability"]
    assert sc["SC_002"]["status"] == "NOT_YET_MEASURED"
    assert sc["SC_003"]["status"] == "PARTIALLY_MEASURED"
    assert "3/3" in sc["SC_003"]["notes"]


def test_historical_bakeoff_preserves_historical_failures_as_fail(manifest):
    """h08 and h10 provider errors must remain status=FAIL."""
    payload = run_historical_bakeoff(manifest)
    fail_cases = [c for c in payload["case_results"] if c["status"] == "FAIL"]
    assert len(fail_cases) > 0, "Known historical provider errors must be reported as FAIL"


def test_historical_bakeoff_result_schema_valid(manifest):
    payload = run_historical_bakeoff(manifest)
    payload.update({
        "run_id": "test-hb-run",
        "mode": "HISTORICAL_BAKEOFF",
        "timestamp_utc": "2026-10-02T00:00:00Z",
        "git_commit": "abc1234",
    })
    assert_schema_valid(payload)


# ---------------------------------------------------------------------------
# LIVE Mode Gate & Provenance Tests
# ---------------------------------------------------------------------------

def test_live_mode_blocked_without_env_var(manifest):
    """LIVE mode must return MISSING_EVALUATION_ASSET with exact settings provenance."""
    with patch.dict("os.environ", {"LIVE_EVALUATION_ENABLED": "false"}, clear=False):
        payload = run_live(manifest)
    assert payload["sc_traceability"]["SC_001"]["status"] == "MISSING_EVALUATION_ASSET"
    assert "provider_config" in payload
    assert payload["provider_config"]["provider"] is not None
    assert payload["provider_config"]["model"] is not None


def test_live_sc001_automated_duration_null_per_spec(manifest):
    """LIVE SC-001: automated_system_path_duration_ms must be null (clean_acme 2-line latency is diagnostic only)."""
    from app.models.schemas import AIExtractionPayload

    fixture_path = REPO_ROOT / "app" / "fixtures" / "clean_acme.json"
    clean_acme_data = json.loads(fixture_path.read_text(encoding="utf-8"))
    clean_payload = AIExtractionPayload.model_validate(clean_acme_data["extraction"])

    with patch.dict("os.environ", {"LIVE_EVALUATION_ENABLED": "true"}, clear=False), \
         patch("app.services.ai_provider.LiveAIProvider.extract", return_value=clean_payload):
        payload = run_live(manifest)
    sc001 = payload["sc_traceability"]["SC_001"]
    assert sc001["status"] == "PARTIALLY_MEASURED"
    assert sc001["automated_system_path_duration_ms"] is None
    assert "clean_acme 2-line LIVE ingest latency is" in sc001["notes"]


# ---------------------------------------------------------------------------
# ALL Mode Tests (Precedence & Merge Semantics)
# ---------------------------------------------------------------------------

def test_merge_sc_traceability_fail_dominates_pass():
    sc1 = {"SC_002": {"status": "PASS", "metric_name": "m", "value": 1, "unit": None, "notes": "pass"}}
    sc2 = {"SC_002": {"status": "FAIL", "metric_name": "m", "value": 0, "unit": None, "notes": "fail"}}
    merged = _merge_sc_traceability([sc1, sc2])
    assert merged["SC_002"]["status"] == "FAIL"

    merged_rev = _merge_sc_traceability([sc2, sc1])
    assert merged_rev["SC_002"]["status"] == "FAIL"


def test_merge_sc_traceability_equal_status_preserves_richer_evidence():
    sc1 = {"SC_001": {"status": "PARTIALLY_MEASURED", "metric_name": "m", "value": None, "unit": None, "notes": "short"}}
    sc2 = {"SC_001": {"status": "PARTIALLY_MEASURED", "metric_name": "m", "value": 50, "unit": "s", "notes": "much longer and richer note with timing"}}
    merged = _merge_sc_traceability([sc1, sc2])
    assert merged["SC_001"]["value"] == 50
    assert "much longer" in merged["SC_001"]["notes"]


def test_run_all_combines_offline_modes(manifest):
    payload = run_all(manifest)
    sc = payload["sc_traceability"]
    assert sc["SC_001"]["status"] == "PARTIALLY_MEASURED"
    assert sc["SC_002"]["status"] == "PASS"
    assert sc["SC_003"]["status"] == "PASS"  # Combined with explicit attribution
    assert sc["SC_004"]["status"] == "PASS"
    assert sc["SC_005"]["status"] == "PASS"
    assert sc["SC_006"]["status"] == "PARTIALLY_MEASURED"


def test_run_all_result_schema_valid(manifest):
    payload = run_all(manifest)
    payload.update({
        "run_id": "test-all-run",
        "mode": "ALL",
        "timestamp_utc": "2026-10-02T00:00:00Z",
        "git_commit": "abc1234",
    })
    assert_schema_valid(payload)


# ---------------------------------------------------------------------------
# Dynamic SC-003 Computation Tests (ALL Mode)
# ---------------------------------------------------------------------------

def test_sc003_complete_evidence_produces_pass():
    hb_metrics = {
        "provider": "Alibaba Model Studio (Singapore)",
        "model": "qwen3.8-flash",
        "review_trap_catch_rate": {"numerator": 3, "denominator": 3, "rate_percentage": 100.0},
        "wrong_confident_skus": {"count": 0},
    }
    rep_cases = [{"case_id": "ambiguous_apex", "status": "PASS"}]
    res = _compute_combined_sc003(hb_metrics, rep_cases)
    assert res["status"] == "PASS"
    assert res["value"] == 100.0
    assert "Alibaba Model Studio" in res["notes"]
    assert "3/3" in res["notes"]


def test_sc003_historical_trap_failure_prevents_pass():
    hb_metrics = {
        "provider": "Alibaba Model Studio (Singapore)",
        "model": "qwen3.8-flash",
        "review_trap_catch_rate": {"numerator": 2, "denominator": 3, "rate_percentage": 66.7},
        "wrong_confident_skus": {"count": 0},
    }
    rep_cases = [{"case_id": "ambiguous_apex", "status": "PASS"}]
    res = _compute_combined_sc003(hb_metrics, rep_cases)
    assert res["status"] == "FAIL"
    assert res["value"] == 0.0
    assert "AI-5 review routing failed" in res["notes"]


def test_sc003_wrong_confident_prevents_pass():
    hb_metrics = {
        "provider": "Alibaba Model Studio (Singapore)",
        "model": "qwen3.8-flash",
        "review_trap_catch_rate": {"numerator": 3, "denominator": 3, "rate_percentage": 100.0},
        "wrong_confident_skus": {"count": 1},
    }
    rep_cases = [{"case_id": "ambiguous_apex", "status": "PASS"}]
    res = _compute_combined_sc003(hb_metrics, rep_cases)
    assert res["status"] == "FAIL"
    assert res["value"] == 0.0
    assert "AI-6 wrong-confident count > 0" in res["notes"]


def test_sc003_app_lifecycle_failure_prevents_pass():
    hb_metrics = {
        "provider": "Alibaba Model Studio (Singapore)",
        "model": "qwen3.8-flash",
        "review_trap_catch_rate": {"numerator": 3, "denominator": 3, "rate_percentage": 100.0},
        "wrong_confident_skus": {"count": 0},
    }
    rep_cases = [{"case_id": "ambiguous_apex", "status": "FAIL"}]
    res = _compute_combined_sc003(hb_metrics, rep_cases)
    assert res["status"] == "FAIL"
    assert res["value"] == 0.0
    assert "ambiguous_apex status = FAIL" in res["notes"]


def test_sc003_missing_evidence_yields_partially_measured():
    res1 = _compute_combined_sc003({}, [{"case_id": "ambiguous_apex", "status": "PASS"}])
    assert res1["status"] == "PARTIALLY_MEASURED"

    res2 = _compute_combined_sc003(
        {"review_trap_catch_rate": {"numerator": 3, "denominator": 3}},
        [],
    )
    assert res2["status"] == "PARTIALLY_MEASURED"


# ---------------------------------------------------------------------------
# Zero-Network Safety Tests (Socket-Level Isolation)
# ---------------------------------------------------------------------------

def test_deterministic_zero_network_safety(manifest, no_external_network):
    payload = run_deterministic(manifest)
    assert payload["deterministic_metrics"]["discrepancy_catch_rate"]["numerator"] == 5


def test_replay_zero_network_safety(manifest, no_external_network):
    payload = run_replay(manifest)
    assert payload["sc_traceability"]["SC_004"]["status"] == "PASS"


def test_historical_bakeoff_zero_network_safety(manifest, no_external_network):
    payload = run_historical_bakeoff(manifest)
    assert payload["ai_metrics"]["historical_bakeoff_metrics"]["provider"] is not None


# ---------------------------------------------------------------------------
# CLI run_evaluation File Writing Tests
# ---------------------------------------------------------------------------

def test_run_evaluation_deterministic_exit_zero(tmp_path, monkeypatch):
    results_dir = tmp_path / "results"
    monkeypatch.setattr("app.evaluation.runner._RESULTS_DIR", results_dir)
    ret = run_evaluation("DETERMINISTIC")
    assert ret == 0
    assert len(list(results_dir.glob("*-DETERMINISTIC.json"))) == 1
    latest = json.loads((results_dir / "latest.json").read_text(encoding="utf-8"))
    assert latest["mode"] == "DETERMINISTIC"
    assert_schema_valid(latest)


def test_run_evaluation_replay_exit_zero(tmp_path, monkeypatch):
    results_dir = tmp_path / "results"
    monkeypatch.setattr("app.evaluation.runner._RESULTS_DIR", results_dir)
    ret = run_evaluation("REPLAY")
    assert ret == 0
    assert len(list(results_dir.glob("*-REPLAY.json"))) == 1
    latest = json.loads((results_dir / "latest.json").read_text(encoding="utf-8"))
    assert latest["mode"] == "REPLAY"
    assert_schema_valid(latest)


def test_run_evaluation_historical_bakeoff_exit_zero(tmp_path, monkeypatch):
    results_dir = tmp_path / "results"
    monkeypatch.setattr("app.evaluation.runner._RESULTS_DIR", results_dir)
    ret = run_evaluation("HISTORICAL_BAKEOFF")
    assert ret == 0
    assert len(list(results_dir.glob("*-HISTORICAL_BAKEOFF.json"))) == 1
    latest = json.loads((results_dir / "latest.json").read_text(encoding="utf-8"))
    assert latest["mode"] == "HISTORICAL_BAKEOFF"
    assert_schema_valid(latest)


def test_run_evaluation_all_exit_zero(tmp_path, monkeypatch):
    results_dir = tmp_path / "results"
    monkeypatch.setattr("app.evaluation.runner._RESULTS_DIR", results_dir)
    ret = run_evaluation("ALL")
    assert ret == 0
    assert len(list(results_dir.glob("*-ALL.json"))) == 1
    latest = json.loads((results_dir / "latest.json").read_text(encoding="utf-8"))
    assert latest["mode"] == "ALL"
    assert_schema_valid(latest)


def test_run_evaluation_invalid_mode_exit_one():
    ret = run_evaluation("INVALID_MODE")
    assert ret == 1
