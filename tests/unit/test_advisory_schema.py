"""Drift-guard and Pydantic validation tests for draft advisory API schema.

Enforces:
1. Exact structural fingerprint of DraftAdvisoryResponse and nested models.
2. Extra-forbid rejection of unknown fields.
3. Stable JSON Schema with const="1.0.0" and comprehensive field descriptions.
4. Drift detection across all enums and model property sets.
"""
from __future__ import annotations

import json
from typing import Any
import pytest
from pydantic import ValidationError

from app.models.advisory import (
    ADVISORY_SCHEMA_VERSION,
    ActionCountInfo,
    AdvisoryStatus,
    BaselineBlocker,
    BaselineLine,
    BaselineSection,
    CandidateEvaluationSummary,
    CounterfactualActionItem,
    CounterfactualCoverage,
    CounterfactualLimits,
    CounterfactualPlan,
    CounterfactualsSection,
    DerivedEvidenceData,
    DocumentEvidenceData,
    DraftAdvisoryResponse,
    ExternalInputInfo,
    LineSKUConfidence,
    OrderArithmeticInfo,
    PlanBlocker,
    PriceSourceInfo,
    ReferenceEvidenceData,
    ReviewPriorityInputsUsed,
    ReviewPrioritySection,
    SKUConfidenceSection,
    TraceEvidence,
    TraceSection,
    TraceStage,
    TraceStepItem,
)
from app.models.schemas import LocationDataSchema

# -----------------------------------------------------------------------------
# Canonical Drift Guard Fingerprint Constants
# -----------------------------------------------------------------------------

EXPECTED_TOP_LEVEL_FIELDS: tuple[str, ...] = (
    "advisory_status",
    "baseline",
    "counterfactuals",
    "customer_id",
    "customer_name_extracted",
    "document_id",
    "draft_id",
    "included_sections",
    "is_replay_mode",
    "po_number_extracted",
    "review_priority",
    "schema_version",
    "sections_omitted",
    "sku_confidence",
    "status_persisted",
    "trace",
)

EXPECTED_ADVISORY_STATUS_VALUES: tuple[str, ...] = (
    "actionable_plans_found",
    "blocked_no_internal_plan",
    "not_applicable_terminal",
    "ready_no_action_needed",
    "requires_external_input",
)

EXPECTED_TRACE_STAGE_VALUES: tuple[str, ...] = (
    "catalog_resolution",
    "contract_pricing",
    "counterfactual_action",
    "input_completeness",
    "line_arithmetic",
    "moq_packaging",
    "order_arithmetic",
    "readiness",
    "review_priority",
    "sku_confidence",
    "staleness",
)

EXPECTED_BASELINE_BLOCKER_FIELDS: tuple[str, ...] = (
    "discrepancy_type",
    "expected_value",
    "explanation",
    "flag_id",
    "line_id",
    "line_number",
    "price_source",
    "requested_value",
    "resolution_state",
    "scope",
    "severity",
)

EXPECTED_BASELINE_LINE_FIELDS: tuple[str, ...] = (
    "calculated_line_total",
    "candidate_skus",
    "contract_price",
    "customer_description",
    "extracted_line_total",
    "extracted_quantity",
    "extracted_unit_price",
    "line_id",
    "line_number",
    "matched_sku",
    "sku_confidence_label",
    "sku_name",
    "sku_resolution_source",
    "status",
)

EXPECTED_COUNTERFACTUAL_PLAN_FIELDS: tuple[str, ...] = (
    "actions",
    "explanation",
    "incompleteness_reasons",
    "is_successful",
    "outcome",
    "plan_id",
    "remaining_blockers",
    "simulated_status",
)


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------

def test_schema_version_is_const():
    assert ADVISORY_SCHEMA_VERSION == "1.0.0"
    schema = DraftAdvisoryResponse.model_json_schema()
    sv_prop = schema["properties"]["schema_version"]
    assert sv_prop["const"] == "1.0.0"
    assert sv_prop["default"] == "1.0.0"


def test_top_level_fields_drift_guard():
    actual_fields = tuple(sorted(DraftAdvisoryResponse.model_fields.keys()))
    assert actual_fields == EXPECTED_TOP_LEVEL_FIELDS


def test_advisory_status_enum_drift_guard():
    actual = tuple(sorted(e.value for e in AdvisoryStatus))
    assert actual == EXPECTED_ADVISORY_STATUS_VALUES


def test_trace_stage_enum_drift_guard():
    actual = tuple(sorted(e.value for e in TraceStage))
    assert actual == EXPECTED_TRACE_STAGE_VALUES


def test_nested_models_drift_guard():
    assert tuple(sorted(BaselineBlocker.model_fields.keys())) == EXPECTED_BASELINE_BLOCKER_FIELDS
    assert tuple(sorted(BaselineLine.model_fields.keys())) == EXPECTED_BASELINE_LINE_FIELDS
    assert tuple(sorted(CounterfactualPlan.model_fields.keys())) == EXPECTED_COUNTERFACTUAL_PLAN_FIELDS


def test_all_fields_have_descriptions():
    models_to_check = [
        DraftAdvisoryResponse,
        BaselineSection,
        BaselineBlocker,
        BaselineLine,
        OrderArithmeticInfo,
        PriceSourceInfo,
        CounterfactualsSection,
        CounterfactualLimits,
        CounterfactualCoverage,
        ActionCountInfo,
        CounterfactualActionItem,
        PlanBlocker,
        CounterfactualPlan,
        ExternalInputInfo,
        ReviewPrioritySection,
        ReviewPriorityInputsUsed,
        SKUConfidenceSection,
        LineSKUConfidence,
        CandidateEvaluationSummary,
        TraceSection,
        TraceStepItem,
        TraceEvidence,
        DocumentEvidenceData,
        ReferenceEvidenceData,
        DerivedEvidenceData,
    ]

    for model_cls in models_to_check:
        for field_name, field_info in model_cls.model_fields.items():
            assert field_info.description is not None and len(field_info.description.strip()) > 0, (
                f"{model_cls.__name__}.{field_name} is missing a description"
            )


def test_extra_fields_forbidden():
    minimal_payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "draft_id": "draft-123",
        "document_id": "doc-123",
        "customer_id": "CUST-ACME",
        "customer_name_extracted": "Acme",
        "po_number_extracted": "PO-1",
        "status_persisted": "Needs Review",
        "is_replay_mode": False,
        "advisory_status": "ready_no_action_needed",
        "included_sections": ["baseline"],
        "sections_omitted": ["counterfactuals", "review_priority", "sku_confidence", "trace"],
        "baseline": None,
        "counterfactuals": None,
        "review_priority": None,
        "sku_confidence": None,
        "trace": None,
    }

    # Valid payload parses cleanly
    obj = DraftAdvisoryResponse.model_validate(minimal_payload)
    assert obj.draft_id == "draft-123"

    # Injecting an unexpected field must fail
    invalid_payload = {**minimal_payload, "unrecognized_extra_key": "forbidden"}
    with pytest.raises(ValidationError) as exc_info:
        DraftAdvisoryResponse.model_validate(invalid_payload)
    assert "extra_forbidden" in str(exc_info.value)


def test_json_schema_generation_stability():
    schema = DraftAdvisoryResponse.model_json_schema()
    serialized_schema = json.dumps(schema, sort_keys=True)
    assert len(serialized_schema) > 2000
    assert "DraftAdvisoryResponse" in schema["title"]
    assert "BaselineSection" in schema.get("$defs", {}) or "BaselineSection" in str(schema)


def test_optional_jsonschema_conformance():
    """Validates schema using python jsonschema package if installed (in CI)."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = DraftAdvisoryResponse.model_json_schema()
    instance = DraftAdvisoryResponse(
        schema_version="1.0.0",
        draft_id="draft-001",
        status_persisted="Needs Review",
        is_replay_mode=False,
        advisory_status=AdvisoryStatus.READY_NO_ACTION_NEEDED,
        included_sections=["baseline"],
        sections_omitted=["counterfactuals", "review_priority", "sku_confidence", "trace"],
    ).model_dump(mode="json")

    jsonschema.validate(instance=instance, schema=schema)
