"""Strict Pydantic contract for read-only order draft advisory service (GET /api/v1/drafts/{draft_id}/advisory).

Provides a stable, self-documented, extra="forbid" contract with schema_version="1.0.0"
intended for consumption by downstream reasoning and LLM workflows.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.schemas import LocationDataSchema

ADVISORY_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"


class AdvisoryStatus(str, Enum):
    """Deterministic high-level workflow classification for advisory evaluation."""

    READY_NO_ACTION_NEEDED = "ready_no_action_needed"
    ACTIONABLE_PLANS_FOUND = "actionable_plans_found"
    BLOCKED_NO_INTERNAL_PLAN = "blocked_no_internal_plan"
    REQUIRES_EXTERNAL_INPUT = "requires_external_input"
    NOT_APPLICABLE_TERMINAL = "not_applicable_terminal"


class TraceStage(str, Enum):
    """Lifecycle stage identifier for machine-readable trace steps."""

    INPUT_COMPLETENESS = "input_completeness"
    CATALOG_RESOLUTION = "catalog_resolution"
    CONTRACT_PRICING = "contract_pricing"
    LINE_ARITHMETIC = "line_arithmetic"
    ORDER_ARITHMETIC = "order_arithmetic"
    MOQ_PACKAGING = "moq_packaging"
    READINESS = "readiness"
    COUNTERFACTUAL_ACTION = "counterfactual_action"
    REVIEW_PRIORITY = "review_priority"
    SKU_CONFIDENCE = "sku_confidence"
    STALENESS = "staleness"


# -----------------------------------------------------------------------------
# Baseline Section Models
# -----------------------------------------------------------------------------

class PriceSourceInfo(BaseModel):
    """Master contract price tier citation backing a price expectation."""

    model_config = ConfigDict(extra="forbid")

    contract_id: str = Field(description="Customer contract ID governing pricing.")
    tier_id: str = Field(description="Specific contract price tier identifier matched.")
    min_quantity: int = Field(description="Minimum order quantity threshold for this price tier.")
    tier_price: str = Field(description="Formatted tier unit price in USD (e.g. '22.00').")


class BaselineBlocker(BaseModel):
    """Active unresolved discrepancy flag or blocking validation condition."""

    model_config = ConfigDict(extra="forbid")

    flag_id: str | None = Field(default=None, description="Persistent discrepancy flag ID if stored in database.")
    line_id: str | None = Field(default=None, description="Line item ID or null for order-level blockers.")
    line_number: int | None = Field(default=None, description="1-indexed line number in PO or null for order-level blockers.")
    discrepancy_type: str = Field(description="Discrepancy category (PriceMismatch, QuantityOrPackagingBreach, ArithmeticMismatch, CatalogMatchingMismatch, IncompleteInput).")
    severity: str = Field(description="Discrepancy severity level (Blocking, Warning, Info).")
    expected_value: str = Field(description="Expected value according to catalog or contract rules.")
    requested_value: str = Field(description="Requested value extracted from customer purchase order.")
    explanation: str = Field(description="Human-readable explanation of why this blocker was raised.")
    resolution_state: str = Field(description="Current resolution status (Unresolved, ResolvedByCorrection, ResolvedByLineRemoval).")
    scope: str = Field(description="Scope of blocker ('line' or 'order').")
    price_source: PriceSourceInfo | None = Field(default=None, description="Contract price tier source information if applicable to price blocker.")


class BaselineLine(BaseModel):
    """Deterministic read-only projection of a single draft line item."""

    model_config = ConfigDict(extra="forbid")

    line_id: str = Field(description="Persistent database ID of the line item.")
    line_number: int = Field(description="1-indexed line number in the purchase order.")
    customer_description: str = Field(description="Raw item description text as provided by customer.")
    extracted_quantity: int | None = Field(default=None, description="Extracted quantity requested by customer.")
    extracted_unit_price: str | None = Field(default=None, description="Extracted unit price formatted in USD (e.g. '18.00').")
    extracted_line_total: str | None = Field(default=None, description="Extracted line total formatted in USD (e.g. '180.00').")
    matched_sku: str | None = Field(default=None, description="Currently matched master catalog SKU, or null.")
    sku_name: str | None = Field(default=None, description="Product catalog name for matched SKU, or null.")
    sku_confidence_label: str | None = Field(default=None, description="Persisted SKU confidence label ('High', 'Ambiguous', 'Unrecognized', or null).")
    sku_resolution_source: str = Field(description="Resolution source ('NONE', 'AI_HIGH_CONFIDENCE', 'OPERATOR_SELECTED').")
    candidate_skus: list[str] = Field(description="Candidate catalog SKUs stored on line item from extraction.")
    contract_price: str | None = Field(default=None, description="Eligible contract tier unit price formatted in USD, or null.")
    calculated_line_total: str | None = Field(default=None, description="Calculated line total formatted in USD, or null.")
    status: str = Field(description="Line status ('Active' or 'Removed').")


class OrderArithmeticInfo(BaseModel):
    """Order-level subtotal and grand total arithmetic status and comparison."""

    model_config = ConfigDict(extra="forbid")

    persisted_subtotal: str | None = Field(default=None, description="Subtotal stored on the draft formatted in USD.")
    calculated_subtotal: str | None = Field(default=None, description="Recomputed subtotal summing active lines in USD.")
    extracted_order_total: str | None = Field(default=None, description="Extracted order grand total from document header in USD, or null if not extracted.")
    extracted_order_total_missing_reason: str | None = Field(default=None, description="Reason why extracted order total is null (e.g. 'No order-level total was extracted from document header').")
    has_subtotal_mismatch: bool = Field(description="True if persisted subtotal does not match recomputed subtotal.")
    has_order_total_mismatch: bool = Field(description="True if calculated subtotal differs from extracted order total.")


class BaselineSection(BaseModel):
    """Baseline reconciliation state, active blockers, line item projections, and order arithmetic."""

    model_config = ConfigDict(extra="forbid")

    status_persisted: str = Field(description="Current stored workflow status in persistent database.")
    simulated_status: str | None = Field(default=None, description="Fresh reconciliation status obtained by running evaluate_clean_draft in an isolated in-memory sandbox (null if draft is terminal).")
    is_stale_evaluation: bool = Field(description="True if stored status differs from freshly simulated status, indicating stored evaluation is out of date.")
    blockers: list[BaselineBlocker] = Field(description="Active unresolved discrepancy flags and blocking validation conditions.")
    incomplete_inputs: list[str] = Field(description="List of missing required input fields preventing reconciliation.")
    completeness_reasons: list[str] = Field(description="Human-readable explanations for input data incompleteness.")
    lines: list[BaselineLine] = Field(description="Deterministic read-only projection of all draft line items.")
    order_arithmetic: OrderArithmeticInfo = Field(description="Order-level subtotal and total arithmetic comparison.")


# -----------------------------------------------------------------------------
# Counterfactuals Section Models
# -----------------------------------------------------------------------------

class CounterfactualLimits(BaseModel):
    """Effective parameters used for the bounded counterfactual search."""

    model_config = ConfigDict(extra="forbid")

    max_depth: int = Field(description="Maximum sequence length of operator actions explored (1..3).")
    max_scenarios: int = Field(description="Maximum number of candidate action combinations evaluated (1..50).")


class CounterfactualCoverage(BaseModel):
    """Search space coverage and termination metadata."""

    model_config = ConfigDict(extra="forbid")

    total_scenarios_evaluated: int = Field(description="Total number of discrete action sequences simulated.")
    is_truncated: bool = Field(description="True if search terminated early due to max_scenarios limit before exhausting search tree.")
    search_is_exhaustive: bool = Field(description="True if search explored all possible combinations within max_depth without truncation.")
    candidate_actions_count: int = Field(description="Number of candidate actions available in the draft.")
    search_space_description: str = Field(description="Description of the explored action space.")
    interpretation_note: str = Field(description="Advisory note on interpreting search completeness and lack of successful plans.")


class ActionCountInfo(BaseModel):
    """Summary of operator action types and counts evaluated."""

    model_config = ConfigDict(extra="forbid")

    action_type: str = Field(description="Action type name ('SelectSKU', 'RemoveLine', 'RequestCorrectedPO').")
    count: int = Field(description="Number of candidate actions of this type available or evaluated.")


class CounterfactualActionItem(BaseModel):
    """Discrete operator action within a counterfactual plan."""

    model_config = ConfigDict(extra="forbid")

    action_type: str = Field(description="Action type ('SelectSKU', 'RemoveLine', 'RequestCorrectedPO').")
    line_id: str | None = Field(default=None, description="Target draft line item ID.")
    line_number: int | None = Field(default=None, description="1-indexed line number of target line.")
    sku: str | None = Field(default=None, description="Selected SKU for SelectSKU action.")
    reason: str | None = Field(default=None, description="Explanation or rationale for action.")


class PlanBlocker(BaseModel):
    """Diagnostic detail of an unresolved blocker after simulated plan execution."""

    model_config = ConfigDict(extra="forbid")

    category: str = Field(description="Discrepancy category.")
    scope: str = Field(description="Scope of blocker ('line' or 'order').")
    line_id: str | None = Field(default=None, description="Line item ID or null.")
    line_number: int | None = Field(default=None, description="1-indexed line number or null.")
    explanation: str = Field(description="Explanation of blocker.")


class CounterfactualPlan(BaseModel):
    """Simulated action plan and resulting readiness outcome."""

    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(description="Deterministic canonical plan identifier based on actions (e.g. 'plan:SelectSKU(L1,SKU-WRAP-15)').")
    actions: list[CounterfactualActionItem] = Field(description="Ordered sequence of operator actions in this plan.")
    outcome: str = Field(description="Simulation outcome ('ready', 'blocked', 'requires_external_input', 'invalid').")
    simulated_status: str | None = Field(default=None, description="Resulting draft status ('Ready for Approval', 'Needs Review', or null).")
    is_successful: bool = Field(description="True if plan successfully transitions draft to Ready for Approval.")
    remaining_blockers: list[PlanBlocker] = Field(description="Discrepancies remaining unresolved after plan execution.")
    incompleteness_reasons: list[str] = Field(description="Data completeness reasons preventing approval.")
    explanation: str = Field(description="Human-readable explanation of simulation result.")


class ExternalInputInfo(BaseModel):
    """Analysis of customer-side corrections required when internal operator actions cannot resolve blockers."""

    model_config = ConfigDict(extra="forbid")

    is_required: bool = Field(description="True if resolution requires revised purchase order from customer.")
    reasons: list[str] = Field(description="Specific commercial reasons requiring customer correction (e.g. PriceMismatch, MOQ breach).")
    explanation: str = Field(description="Detailed explanation of why internal operator actions cannot resolve the issue.")


class CounterfactualsSection(BaseModel):
    """Counterfactual analysis of allowed operator actions dry-run in an isolated sandbox."""

    model_config = ConfigDict(extra="forbid")

    limits: CounterfactualLimits = Field(description="Effective search bounds used.")
    coverage: CounterfactualCoverage = Field(description="Search space coverage and termination metadata.")
    actions_considered: list[ActionCountInfo] = Field(description="Summary of operator action types considered.")
    successful_plans: list[CounterfactualPlan] = Field(description="List of action plans that successfully transition draft to Ready for Approval, ordered shortest first.")
    blocked_plans: list[CounterfactualPlan] = Field(description="Action plans that were evaluated but remained blocked or did not achieve readiness.")
    requires_external_input: ExternalInputInfo | None = Field(default=None, description="Analysis of customer correction requirements.")
    invalid_plans: list[CounterfactualPlan] = Field(default_factory=list, description="Plans failing mutation validation (e.g. non-existent lines or products).")


# -----------------------------------------------------------------------------
# Review Priority Section Models
# -----------------------------------------------------------------------------

class ReviewPriorityInputsUsed(BaseModel):
    """Exact normalized feature values supplied to the review priority evaluator."""

    model_config = ConfigDict(extra="forbid")

    discrepancy_count: int = Field(description="Number of detected discrepancies.")
    discrepancy_severity: float = Field(description="Aggregated discrepancy severity in [0.0, 1.0].")
    price_deviation_pct: float = Field(description="Maximum relative price deviation percentage across lines.")
    order_total_cents: int | None = Field(default=None, description="Order total in integer cents.")
    order_total_formatted: str | None = Field(default=None, description="Order total formatted in USD.")
    normalized_order_total: float = Field(description="Order total normalized into [0.0, 1.0] against saturation ceiling.")
    match_confidence: float | None = Field(default=None, description="SKU match confidence numeric score in [0.0, 1.0], or null.")
    match_confidence_source: str = Field(description="Origin of match_confidence score ('persisted_numeric', 'mapped_from_label', 'unavailable').")
    persisted_match_confidence_label: str | None = Field(default=None, description="Persisted linguistic SKU confidence label from database.")
    effective_uncertainty: float = Field(description="Uncertainty score in [0.0, 1.0] used by fuzzy rules (1.0 - match_confidence).")
    note: str = Field(default="Advisory heuristic score, not a calibrated probability; never fed back into reconciliation.", description="Important interpretative constraint.")


class ReviewPrioritySection(BaseModel):
    """Fuzzy review priority heuristic score and linguistic label based on discrepancy severity and count."""

    model_config = ConfigDict(extra="forbid")

    review_priority: float = Field(description="Fuzzy review priority score in [0.0, 1.0], rounded to 4 decimals. Advisory heuristic, not a calibrated probability.")
    review_priority_label: str = Field(description="Linguistic review priority tier ('low', 'medium', 'high').")
    raw_fuzzy_score: float = Field(description="Raw Mamdani defuzzified centroid score prior to monotonic upper envelope.")
    has_incomplete_data: bool = Field(description="True if input features were partially missing and safety floors were applied.")
    monotonic_adjustment: bool = Field(description="True if upper envelope adjusted the raw fuzzy score upward to guarantee monotonicity.")
    applied_rules: list[str] = Field(description="List of symbolic rule identifiers that fired in the fuzzy inference engine.")
    inputs_used: ReviewPriorityInputsUsed = Field(description="Exact normalized feature values supplied to the review priority evaluator.")


# -----------------------------------------------------------------------------
# SKU Confidence Section Models
# -----------------------------------------------------------------------------

class CandidateEvaluationSummary(BaseModel):
    """Summary evaluation of an individual catalog SKU candidate."""

    model_config = ConfigDict(extra="forbid")

    sku: str = Field(description="Catalog SKU identifier.")
    score: float = Field(description="Defuzzified candidate match score in [0.0, 1.0].")
    label: str = Field(description="Linguistic match label ('low', 'medium', 'high').")
    description_similarity: float = Field(description="Semantic description similarity feature in [0.0, 1.0].")
    price_closeness: float = Field(description="Price proximity feature in [0.0, 1.0].")
    quantity_plausibility: float = Field(description="Quantity alignment feature in [0.0, 1.0].")


class LineSKUConfidence(BaseModel):
    """Per-line SKU matching confidence evaluation and candidate scores."""

    model_config = ConfigDict(extra="forbid")

    line_id: str = Field(description="Line item ID.")
    line_number: int = Field(description="1-indexed line number.")
    customer_description: str = Field(description="Customer description text.")
    persisted_label: str | None = Field(default=None, description="Persisted sku_confidence label from database.")
    matched_sku: str | None = Field(default=None, description="Currently assigned SKU, or null.")
    evaluated_confidence: float | None = Field(default=None, description="Computed or mapped match confidence in [0.0, 1.0], rounded to 4 decimals.")
    evaluated_label: str | None = Field(default=None, description="Evaluated confidence label ('low', 'medium', 'high', or null).")
    confidence_source: str = Field(description="Source of evaluated confidence ('persisted_numeric', 'mapped_from_label', 'unavailable').")
    score_margin: float | None = Field(default=None, description="Difference between top candidate score and runner-up score.")
    reason: str | None = Field(default=None, description="Reason for confidence determination or ambiguity downgrade.")
    candidates: list[CandidateEvaluationSummary] = Field(default_factory=list, description="Evaluated SKU candidate scores if candidates were evaluated.")
    note: str = Field(default="Advisory heuristic score, not a calibrated probability.", description="Interpretation note.")


class SKUConfidenceSection(BaseModel):
    """Fuzzy SKU match confidence evaluation and ambiguity analysis across candidate SKUs."""

    model_config = ConfigDict(extra="forbid")

    lines: list[LineSKUConfidence] = Field(description="Per-line SKU matching confidence evaluation and candidate scores.")


# -----------------------------------------------------------------------------
# Trace Section Models
# -----------------------------------------------------------------------------

class DocumentEvidenceData(BaseModel):
    """Grounding citation from source document verbatim text with exact character coordinates."""

    model_config = ConfigDict(extra="forbid")

    field_name: str = Field(description="Extracted field name (e.g. 'customer_name', 'extracted_unit_price').")
    verbatim_snippet: str = Field(description="Exact character sequence from raw document.")
    location: LocationDataSchema = Field(description="Coordinates in document (type: 'txt'|'pdf', line_number, char_offset).")


class ReferenceEvidenceData(BaseModel):
    """Master catalog product or contract price tier lookup citation."""

    model_config = ConfigDict(extra="forbid")

    sku: str = Field(description="Master catalog SKU.")
    contract_id: str | None = Field(default=None, description="Customer contract ID.")
    tier_id: str | None = Field(default=None, description="Contract price tier ID.")
    min_quantity: int | None = Field(default=None, description="Tier minimum order quantity threshold.")
    tier_price: str | None = Field(default=None, description="Tier price in USD.")


class DerivedEvidenceData(BaseModel):
    """Deterministic arithmetic or rule aggregation calculation citation."""

    model_config = ConfigDict(extra="forbid")

    formula: str = Field(description="Exact arithmetic or logic formula (e.g. 'quantity * contract_price').")
    input_values: dict[str, Any] = Field(description="Exact input values used in formula.")
    result_value: Any = Field(description="Computed result of formula.")


class TraceEvidence(BaseModel):
    """Verifiable grounding citation backing an advisory trace decision."""

    model_config = ConfigDict(extra="forbid")

    source_type: str = Field(description="Evidence origin category: 'document', 'reference_data', or 'derived'.")
    document_evidence: DocumentEvidenceData | None = Field(default=None, description="Verbatim quote from source document with exact character coordinates.")
    reference_evidence: ReferenceEvidenceData | None = Field(default=None, description="Master catalog or contract tier lookup record.")
    derived_evidence: DerivedEvidenceData | None = Field(default=None, description="Deterministic arithmetic calculation with formula and inputs.")


class TraceStepItem(BaseModel):
    """Individual machine-readable decision step in the advisory derivation trace."""

    model_config = ConfigDict(extra="forbid")

    step_index: int = Field(description="1-indexed step sequence number.")
    stage: TraceStage = Field(description="Lifecycle stage of the decision.")
    subject: str = Field(description="Target of the step ('order' or 'line:<line_number>').")
    inputs: dict[str, Any] = Field(description="Exact feature inputs supplied to the decision logic.")
    rule_or_decision: str = Field(description="Rule name or decision point identifier.")
    outcome: str = Field(description="Outcome of this step ('ok', 'blocking', 'warning', 'info', 'requires_external_input', 'action_simulated').")
    explanation: str = Field(description="Human-readable explanation of why this step was evaluated and what was decided.")
    evidence: list[TraceEvidence] = Field(default_factory=list, description="Grounding evidence citations backing this decision.")


class TraceSection(BaseModel):
    """Step-by-step machine-readable trace of decisions grounded in document provenance and contract pricing."""

    model_config = ConfigDict(extra="forbid")

    steps: list[TraceStepItem] = Field(description="Deterministically ordered sequence of evaluation steps with grounded evidence.")


# -----------------------------------------------------------------------------
# Top-Level Advisory Response
# -----------------------------------------------------------------------------

class DraftAdvisoryResponse(BaseModel):
    """Complete machine-readable advisory response contract for order draft inspection."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0.0"] = Field(
        default=ADVISORY_SCHEMA_VERSION,
        description="Fixed schema version string conforming to Semantic Versioning (const in JSON Schema).",
    )
    draft_id: str = Field(description="Unique identifier of the order draft.")
    document_id: str | None = Field(default=None, description="Identifier of the associated purchase order source document.")
    customer_id: str | None = Field(default=None, description="Canonical customer account ID.")
    customer_name_extracted: str | None = Field(default=None, description="Customer organization name extracted from the document header.")
    po_number_extracted: str | None = Field(default=None, description="Purchase order identifier extracted from the document header.")
    status_persisted: str = Field(description="Current stored workflow status in the database (e.g. Ingested, Needs Review, Ready for Approval, Approved, Rejected).")
    is_replay_mode: bool = Field(description="Indicates whether this draft originated from synthetic replay mode.")
    advisory_status: AdvisoryStatus = Field(description="Deterministic high-level workflow classification (ready_no_action_needed, actionable_plans_found, blocked_no_internal_plan, requires_external_input, not_applicable_terminal).")
    included_sections: list[str] = Field(description="Canonically sorted list of section names included in this response.")
    sections_omitted: list[str] = Field(description="Canonically sorted list of section names omitted from this response.")
    baseline: BaselineSection | None = Field(default=None, description="Baseline reconciliation state, active blockers, line item projections, and order arithmetic.")
    counterfactuals: CounterfactualsSection | None = Field(default=None, description="Counterfactual analysis of allowed operator actions dry-run in an isolated sandbox.")
    review_priority: ReviewPrioritySection | None = Field(default=None, description="Fuzzy review priority heuristic score and linguistic label based on discrepancy severity and count.")
    sku_confidence: SKUConfidenceSection | None = Field(default=None, description="Fuzzy SKU match confidence evaluation and ambiguity analysis across candidate SKUs.")
    trace: TraceSection | None = Field(default=None, description="Step-by-step machine-readable trace of decisions grounded in document provenance and contract pricing.")
