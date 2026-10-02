"""Counterfactual analysis of allowed operator actions on purchase order drafts.

Dry-runs permitted operator actions in isolated, in-memory sandboxes to evaluate
which actions or sequences lead to 'Ready for Approval' without modifying source state
or applying prohibited commercial overrides.

Allowed Action Space:
- SelectSKU(line_id, sku): resolves catalog ambiguity to a valid master-catalog SKU.
- RemoveLine(line_id): marks line Removed, preserving discrepancy history.
- RequestCorrectedPO([reason]): proposes requesting a revised PO from customer;
  always requires external input and never fabricates commercial values.

Strictly Prohibited:
- Commercial overrides: altering customer-stated prices or quantities.
- CorrectField or any action outside the allowlist.
- Directly altering contract tiers, MOQ, package increments, or draft status.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping, Sequence

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.models.entities import (
    CatalogProduct,
    ContractPriceTier,
    CustomerContract,
    DiscrepancyFlag,
    DraftLineItem,
    FieldProvenance,
    OrderDraft,
    PurchaseOrderDocument,
)
from app.services.reconciliation import (
    LineMutationValidationError,
    LineNotFoundError,
    TerminalDraftMutationError,
    evaluate_clean_draft,
    remove_line,
    select_line_sku,
)

# -----------------------------------------------------------------------------
# Action Space & Allowlist Constants
# -----------------------------------------------------------------------------

ALLOWED_ACTION_TYPES = frozenset({"SelectSKU", "RemoveLine", "RequestCorrectedPO"})

# Forbidden commercial and internal fields that must never be overridden
FORBIDDEN_COMMERCIAL_FIELDS = frozenset({
    "extracted_quantity",
    "extracted_unit_price",
    "extracted_unit_price_cents",
    "extracted_line_total",
    "extracted_line_total_cents",
    "extracted_order_total",
    "extracted_order_total_cents",
    "contract_price",
    "contract_price_cents",
    "calculated_line_total_cents",
    "calculated_subtotal_cents",
    "price",
    "quantity",
    "amount",
    "min_order_quantity",
    "package_increment",
    "status",
    "draft_status",
    "resolution_state",
    "discrepancy_type",
})


# -----------------------------------------------------------------------------
# Action DTOs
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class SelectSKUAction:
    """Explicitly select a master-catalog SKU for an active draft line item."""

    line_id: str
    sku: str
    action_type: str = "SelectSKU"


@dataclass(frozen=True)
class RemoveLineAction:
    """Mark a draft line item as Removed, preserving discrepancy history."""

    line_id: str
    action_type: str = "RemoveLine"


@dataclass(frozen=True)
class RequestCorrectedPOAction:
    """Proposal to request a corrected purchase order document from customer."""

    action_type: str = "RequestCorrectedPO"
    reason: str = ""


OperatorAction = SelectSKUAction | RemoveLineAction | RequestCorrectedPOAction


# -----------------------------------------------------------------------------
# Action Parsing & Strict Validation
# -----------------------------------------------------------------------------

def parse_and_validate_action(payload: Any) -> OperatorAction:
    """Parse and validate an action against the strict operator allowlist.

    Rejects unauthorized action types, forbidden commercial overrides, extra fields,
    and invalid types.
    """
    if isinstance(payload, (SelectSKUAction, RemoveLineAction, RequestCorrectedPOAction)):
        return payload

    if not isinstance(payload, dict):
        raise TypeError(
            f"Action must be a dict or OperatorAction instance, got {type(payload).__name__} ({payload!r})"
        )

    action_type = payload.get("action") or payload.get("action_type")
    if not isinstance(action_type, str) or not action_type.strip():
        raise ValueError(f"Action type is missing or blank in payload: {payload!r}")

    action_type = action_type.strip()
    if action_type not in ALLOWED_ACTION_TYPES:
        raise ValueError(
            f"Action '{action_type}' is not in allowed action types: {sorted(ALLOWED_ACTION_TYPES)}. "
            "Commercial overrides and unlisted actions are strictly forbidden."
        )

    # Check for forbidden commercial override fields
    forbidden_keys = set(payload.keys()) & FORBIDDEN_COMMERCIAL_FIELDS
    if forbidden_keys:
        raise ValueError(
            f"Commercial overrides are forbidden: field(s) {sorted(forbidden_keys)} cannot be altered by operator action"
        )

    if action_type == "SelectSKU":
        allowed_keys = {"action", "action_type", "line_id", "sku", "matched_sku"}
        extra_keys = set(payload.keys()) - allowed_keys
        if extra_keys:
            raise ValueError(f"Unexpected extra field(s) {sorted(extra_keys)} in SelectSKU payload")

        line_id = payload.get("line_id")
        if not isinstance(line_id, str) or not line_id.strip():
            raise ValueError(f"SelectSKU requires a non-empty string line_id, got {line_id!r}")

        sku = payload.get("sku") or payload.get("matched_sku")
        if not isinstance(sku, str) or not sku.strip():
            raise ValueError(f"SelectSKU requires a non-empty string sku, got {sku!r}")

        return SelectSKUAction(line_id=line_id.strip(), sku=sku.strip())

    elif action_type == "RemoveLine":
        allowed_keys = {"action", "action_type", "line_id"}
        extra_keys = set(payload.keys()) - allowed_keys
        if extra_keys:
            raise ValueError(f"Unexpected extra field(s) {sorted(extra_keys)} in RemoveLine payload")

        line_id = payload.get("line_id")
        if not isinstance(line_id, str) or not line_id.strip():
            raise ValueError(f"RemoveLine requires a non-empty string line_id, got {line_id!r}")

        return RemoveLineAction(line_id=line_id.strip())

    elif action_type == "RequestCorrectedPO":
        allowed_keys = {"action", "action_type", "reason"}
        extra_keys = set(payload.keys()) - allowed_keys
        if extra_keys:
            raise ValueError(f"Unexpected extra field(s) {sorted(extra_keys)} in RequestCorrectedPO payload")

        reason = payload.get("reason", "")
        if not isinstance(reason, str):
            raise TypeError(f"RequestCorrectedPO reason must be a string, got {type(reason).__name__}")

        return RequestCorrectedPOAction(reason=reason.strip())

    raise ValueError(f"Unhandled action type '{action_type}'")


# -----------------------------------------------------------------------------
# Evaluation Result DTOs
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class RemainingBlocker:
    """Diagnostic detail of an unresolved discrepancy or blocking condition."""

    category: str
    scope: str  # "line" or "order"
    line_id: str | None
    line_number: int | None
    explanation: str


@dataclass(frozen=True)
class LineItemSummary:
    """Immutable snapshot of a line item after simulated plan execution."""

    line_id: str
    line_number: int
    status: str  # "Active" or "Removed"
    matched_sku: str | None
    customer_description: str
    extracted_quantity: int | None
    extracted_unit_price_cents: int | None
    contract_price_cents: int | None


@dataclass(frozen=True)
class PlanEvaluationResult:
    """Complete structured outcome of simulating a discrete action plan."""

    actions: tuple[OperatorAction, ...]
    outcome: str  # "ready" | "blocked" | "requires_external_input" | "invalid"
    simulated_status: str | None  # "Ready for Approval" | "Needs Review" | None
    remaining_blockers: tuple[RemainingBlocker, ...]
    incompleteness_reasons: tuple[str, ...]
    active_lines: tuple[LineItemSummary, ...]
    removed_lines: tuple[LineItemSummary, ...]
    explanation: str
    is_successful: bool


@dataclass(frozen=True)
class SearchCoverageInfo:
    """Metadata regarding candidate action space coverage and bounded search limits."""

    total_scenarios_evaluated: int
    max_depth: int
    max_scenarios: int
    is_truncated: bool
    candidate_actions_count: int
    search_space_description: str


@dataclass(frozen=True)
class CounterfactualAnalysisResult:
    """Aggregate result of counterfactual exploration across a draft."""

    draft_id: str
    initial_status: str
    is_already_ready: bool
    successful_plans: tuple[PlanEvaluationResult, ...]
    all_evaluated_plans: tuple[PlanEvaluationResult, ...]
    coverage: SearchCoverageInfo
    summary: str


# -----------------------------------------------------------------------------
# Sandbox Manager & Pure In-Memory Isolation
# -----------------------------------------------------------------------------

def _clone_to_sandbox(source_db: Session, draft_id: str) -> tuple[Session, Any, OrderDraft]:
    """Clone draft and required context into an isolated in-memory SQLite database.

    Reads from source_db without autoflush and without modifying any source ORM objects.
    Preserves original UUIDs for stable line references.
    """
    with source_db.no_autoflush:
        draft = source_db.get(OrderDraft, draft_id)
        if draft is None:
            raise ValueError(f"Order draft with id '{draft_id}' not found")

        products = source_db.scalars(select(CatalogProduct)).all()
        contracts = source_db.scalars(select(CustomerContract)).all()
        tiers = source_db.scalars(select(ContractPriceTier)).all()
        document = source_db.get(PurchaseOrderDocument, draft.document_id) if draft.document_id else None
        line_items = list(draft.line_items)
        provenances = list(draft.provenance_records)
        flags = list(draft.discrepancy_flags)

    sandbox_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(sandbox_engine)
    sandbox_session = Session(sandbox_engine)

    def _copy(model_cls, obj):
        return model_cls(**{c.name: getattr(obj, c.name) for c in obj.__table__.columns})

    for p in products:
        sandbox_session.add(_copy(CatalogProduct, p))
    for c in contracts:
        sandbox_session.add(_copy(CustomerContract, c))
    for t in tiers:
        sandbox_session.add(_copy(ContractPriceTier, t))
    if document is not None:
        sandbox_session.add(_copy(PurchaseOrderDocument, document))
    sandbox_session.add(_copy(OrderDraft, draft))
    for l in line_items:
        sandbox_session.add(_copy(DraftLineItem, l))
    for pr in provenances:
        sandbox_session.add(_copy(FieldProvenance, pr))
    for fl in flags:
        sandbox_session.add(_copy(DiscrepancyFlag, fl))

    sandbox_session.commit()
    sandbox_draft = sandbox_session.get(OrderDraft, draft_id)
    return sandbox_session, sandbox_engine, sandbox_draft


def _extract_summaries(draft: OrderDraft) -> tuple[tuple[LineItemSummary, ...], tuple[LineItemSummary, ...]]:
    """Extract immutable line item summaries from sandbox draft."""
    active: list[LineItemSummary] = []
    removed: list[LineItemSummary] = []

    for item in sorted(draft.line_items, key=lambda x: x.line_number):
        summary = LineItemSummary(
            line_id=item.id,
            line_number=item.line_number,
            status=item.status,
            matched_sku=item.matched_sku,
            customer_description=item.customer_description,
            extracted_quantity=item.extracted_quantity,
            extracted_unit_price_cents=item.extracted_unit_price_cents,
            contract_price_cents=item.contract_price_cents,
        )
        if item.status == "Active":
            active.append(summary)
        else:
            removed.append(summary)

    return tuple(active), tuple(removed)


def _extract_incompleteness_reasons(draft: OrderDraft) -> tuple[str, ...]:
    """Identify data incompleteness causes preventing Ready for Approval."""
    reasons: list[str] = []
    if not draft.customer_id:
        reasons.append("Customer ID is missing")
    if not draft.customer_name_extracted:
        reasons.append("Customer name is missing")
    if not draft.po_number_extracted:
        reasons.append("PO number is missing")

    active_lines = [l for l in draft.line_items if l.status == "Active"]
    if not active_lines:
        reasons.append("No active line items remain in draft")

    for line in active_lines:
        if line.extracted_quantity is None or line.extracted_quantity < 1:
            reasons.append(f"Line {line.line_number} is missing a valid positive quantity")
        if line.extracted_unit_price_cents is None or line.extracted_unit_price_cents < 0:
            reasons.append(f"Line {line.line_number} is missing valid unit price cents")
        if line.extracted_line_total_cents is None or line.extracted_line_total_cents < 0:
            reasons.append(f"Line {line.line_number} is missing valid line total cents")
        if not line.customer_description or not line.customer_description.strip():
            reasons.append(f"Line {line.line_number} is missing customer description")

    return tuple(reasons)


# -----------------------------------------------------------------------------
# Single Plan Evaluation
# -----------------------------------------------------------------------------

def evaluate_action_plan(
    db: Session,
    draft_id: str,
    actions: Sequence[OperatorAction | dict[str, Any]],
) -> PlanEvaluationResult:
    """Simulate execution of an explicit operator action sequence in an isolated sandbox.

    Args:
        db: Caller database session (never flushed, committed, or rolled back).
        draft_id: Primary key ID of the OrderDraft to evaluate.
        actions: Sequence of OperatorAction instances or compliant dicts.

    Returns:
        PlanEvaluationResult: Deterministic outcome, simulated status, and remaining blockers.
    """
    # 1. Parse and validate all actions
    parsed_actions: list[OperatorAction] = []
    for idx, act in enumerate(actions):
        try:
            parsed = parse_and_validate_action(act)
            parsed_actions.append(parsed)
        except (ValueError, TypeError) as exc:
            return PlanEvaluationResult(
                actions=tuple(parsed_actions),
                outcome="invalid",
                simulated_status=None,
                remaining_blockers=(),
                incompleteness_reasons=(),
                active_lines=(),
                removed_lines=(),
                explanation=f"Action #{idx + 1} is invalid: {exc}",
                is_successful=False,
            )

    tuple_actions = tuple(parsed_actions)

    # 2. Check for RequestCorrectedPO
    # RequestCorrectedPO proposes requesting a revised PO from the customer.
    # It cannot fabricate values, clear blockers, or reach Ready for Approval without external input.
    for act in parsed_actions:
        if isinstance(act, RequestCorrectedPOAction):
            # Inspect existing blockers in source db to provide diagnostic explanation
            blockers: list[RemainingBlocker] = []
            with db.no_autoflush:
                orig_draft = db.get(OrderDraft, draft_id)
                if orig_draft:
                    for fl in orig_draft.discrepancy_flags:
                        if fl.resolution_state == "Unresolved":
                            blockers.append(
                                RemainingBlocker(
                                    category=fl.discrepancy_type,
                                    scope="line" if fl.line_item_id else "order",
                                    line_id=fl.line_item_id,
                                    line_number=fl.line_item.line_number if fl.line_item else None,
                                    explanation=fl.explanation,
                                )
                            )
            reasons_text = "; ".join(sorted({b.category for b in blockers})) or "unresolved discrepancies"
            custom_reason = f" ({act.reason})" if act.reason else ""
            explanation = (
                f"RequestCorrectedPO proposes requesting a revised purchase order from customer{custom_reason}. "
                f"Customer intervention is required to resolve: {reasons_text}. "
                "Without a newly ingested purchase order, blockers cannot be cleared and draft cannot be approved."
            )
            return PlanEvaluationResult(
                actions=tuple_actions,
                outcome="requires_external_input",
                simulated_status=None,
                remaining_blockers=tuple(blockers),
                incompleteness_reasons=(),
                active_lines=(),
                removed_lines=(),
                explanation=explanation,
                is_successful=False,
            )

    # 3. Spin up isolated in-memory sandbox and apply mutations
    sb_sess, sb_engine, sb_draft = _clone_to_sandbox(db, draft_id)
    try:
        for idx, act in enumerate(parsed_actions):
            if isinstance(act, SelectSKUAction):
                line = sb_sess.get(DraftLineItem, act.line_id)
                if line is None or line.draft_id != sb_draft.id:
                    raise LineNotFoundError(f"Line item '{act.line_id}' does not belong to draft '{draft_id}'")
                select_line_sku(sb_sess, sb_draft, line, matched_sku=act.sku)

            elif isinstance(act, RemoveLineAction):
                line = sb_sess.get(DraftLineItem, act.line_id)
                if line is None or line.draft_id != sb_draft.id:
                    raise LineNotFoundError(f"Line item '{act.line_id}' does not belong to draft '{draft_id}'")
                remove_line(sb_sess, sb_draft, line)

        sb_sess.commit()

        # Re-derive clean draft readiness in sandbox
        evaluate_clean_draft(sb_sess, sb_draft)
        sb_sess.commit()

        status = sb_draft.status
        is_ready = (status == "Ready for Approval")

        # Collect unresolved blockers
        blockers = []
        for fl in sb_draft.discrepancy_flags:
            if fl.resolution_state == "Unresolved":
                blockers.append(
                    RemainingBlocker(
                        category=fl.discrepancy_type,
                        scope="line" if fl.line_item_id else "order",
                        line_id=fl.line_item_id,
                        line_number=fl.line_item.line_number if fl.line_item else None,
                        explanation=fl.explanation,
                    )
                )

        incompleteness = _extract_incompleteness_reasons(sb_draft)
        active_lines, removed_lines = _extract_summaries(sb_draft)

        if is_ready:
            outcome = "ready"
            explanation = f"Plan successfully transitioned draft to 'Ready for Approval' ({len(active_lines)} active lines)."
        else:
            outcome = "blocked"
            reasons_summary = []
            if blockers:
                reasons_summary.append(f"{len(blockers)} unresolved discrepancy flag(s)")
            if incompleteness:
                reasons_summary.append("; ".join(incompleteness))
            reasons_str = "; ".join(reasons_summary) or "unmet readiness criteria"
            explanation = f"Plan resulted in status '{status}'. Blockers remain: {reasons_str}."

        return PlanEvaluationResult(
            actions=tuple_actions,
            outcome=outcome,
            simulated_status=status,
            remaining_blockers=tuple(blockers),
            incompleteness_reasons=incompleteness,
            active_lines=active_lines,
            removed_lines=removed_lines,
            explanation=explanation,
            is_successful=is_ready,
        )

    except (LineMutationValidationError, TerminalDraftMutationError, LineNotFoundError, ValueError) as exc:
        return PlanEvaluationResult(
            actions=tuple_actions,
            outcome="invalid",
            simulated_status=None,
            remaining_blockers=(),
            incompleteness_reasons=(),
            active_lines=(),
            removed_lines=(),
            explanation=f"Plan execution failed: {type(exc).__name__}: {exc}",
            is_successful=False,
        )
    finally:
        sb_sess.close()
        sb_engine.dispose()


# -----------------------------------------------------------------------------
# Bounded Deterministic Search
# -----------------------------------------------------------------------------

def _extract_line_candidate_skus(line: DraftLineItem) -> list[str]:
    """Parse candidate SKUs stored on line item without scanning the full catalog."""
    candidates: list[str] = []
    if not line.candidate_skus_json:
        return candidates

    try:
        raw = json.loads(line.candidate_skus_json)
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str) and item.strip():
                    candidates.append(item.strip())
                elif isinstance(item, dict) and "sku" in item and isinstance(item["sku"], str):
                    candidates.append(item["sku"].strip())
    except (json.JSONDecodeError, TypeError):
        pass

    return sorted(set(candidates))


def search_counterfactual_plans(
    db: Session,
    draft_id: str,
    *,
    max_depth: int = 2,
    max_scenarios: int = 50,
    operator_candidate_skus: Mapping[str, Sequence[str]] | None = None,
) -> CounterfactualAnalysisResult:
    """Perform bounded deterministic search over allowed operator actions.

    Searches discrete sequences of SelectSKU and RemoveLine to find any that achieve
    'Ready for Approval'. Prunes no-ops, duplicate orderings, and terminates within
    max_depth and max_scenarios limits.

    Args:
        db: Caller database session.
        draft_id: OrderDraft ID.
        max_depth: Maximum action sequence length to explore (default: 2).
        max_scenarios: Hard limit on total scenarios evaluated (default: 50).
        operator_candidate_skus: Optional mapping of line_id -> list of additional candidate SKUs.

    Returns:
        CounterfactualAnalysisResult: Successful plans, coverage metadata, and truncation status.
    """
    if max_depth < 1:
        raise ValueError("max_depth must be at least 1")
    if max_scenarios < 1:
        raise ValueError("max_scenarios must be at least 1")

    # 1. Read initial draft snapshot without autoflush
    with db.no_autoflush:
        draft = db.get(OrderDraft, draft_id)
        if draft is None:
            raise ValueError(f"Order draft with id '{draft_id}' not found")

        initial_status = draft.status
        active_lines = [l for l in draft.line_items if l.status == "Active"]
        active_lines_by_id = {l.id: l for l in active_lines}

    # If draft is already Ready for Approval, no actions are needed
    if initial_status == "Ready for Approval":
        already_ready_plan = PlanEvaluationResult(
            actions=(),
            outcome="ready",
            simulated_status="Ready for Approval",
            remaining_blockers=(),
            incompleteness_reasons=(),
            active_lines=tuple(
                LineItemSummary(
                    line_id=l.id,
                    line_number=l.line_number,
                    status=l.status,
                    matched_sku=l.matched_sku,
                    customer_description=l.customer_description,
                    extracted_quantity=l.extracted_quantity,
                    extracted_unit_price_cents=l.extracted_unit_price_cents,
                    contract_price_cents=l.contract_price_cents,
                )
                for l in sorted(active_lines, key=lambda x: x.line_number)
            ),
            removed_lines=(),
            explanation="Draft is already 'Ready for Approval'; no operator actions required.",
            is_successful=True,
        )
        return CounterfactualAnalysisResult(
            draft_id=draft_id,
            initial_status=initial_status,
            is_already_ready=True,
            successful_plans=(already_ready_plan,),
            all_evaluated_plans=(already_ready_plan,),
            coverage=SearchCoverageInfo(
                total_scenarios_evaluated=0,
                max_depth=max_depth,
                max_scenarios=max_scenarios,
                is_truncated=False,
                candidate_actions_count=0,
                search_space_description="Draft already ready. Action generation bypassed.",
            ),
            summary="Draft is already Ready for Approval; 0 actions required.",
        )

    # 2. Build discrete candidate action pool per line
    line_candidate_actions: dict[str, list[OperatorAction]] = {}
    total_candidate_actions = 0

    for line in active_lines:
        line_actions: list[OperatorAction] = [RemoveLineAction(line_id=line.id)]

        # Collect SKUs from AI candidate history plus operator explicit inputs
        skus_to_test: set[str] = set(_extract_line_candidate_skus(line))
        if operator_candidate_skus and line.id in operator_candidate_skus:
            skus_to_test.update(operator_candidate_skus[line.id])

        # Filter out redundant no-op if line is already operator-selected to this SKU
        for sku in sorted(skus_to_test):
            if line.matched_sku == sku and line.sku_resolution_source == "OPERATOR_SELECTED":
                continue
            line_actions.append(SelectSKUAction(line_id=line.id, sku=sku))

        line_candidate_actions[line.id] = line_actions
        total_candidate_actions += len(line_actions)

    evaluated_plans: list[PlanEvaluationResult] = []
    successful_plans: list[PlanEvaluationResult] = []
    is_truncated = False

    # 3. Depth 1 Evaluation: Single line actions + RequestCorrectedPO
    # Evaluate RequestCorrectedPO proposal once
    if len(evaluated_plans) < max_scenarios:
        req_res = evaluate_action_plan(db, draft_id, [RequestCorrectedPOAction()])
        evaluated_plans.append(req_res)
    else:
        is_truncated = True

    # Single actions per line
    for line_id in sorted(line_candidate_actions.keys()):
        for action in line_candidate_actions[line_id]:
            if len(evaluated_plans) >= max_scenarios:
                is_truncated = True
                break
            res = evaluate_action_plan(db, draft_id, [action])
            evaluated_plans.append(res)
            if res.is_successful:
                successful_plans.append(res)
        if is_truncated:
            break

    # 4. Depth 2 Evaluation: Pairs of actions on distinct lines in canonical order
    if max_depth >= 2 and not is_truncated:
        sorted_line_ids = sorted(line_candidate_actions.keys())
        for i in range(len(sorted_line_ids)):
            id1 = sorted_line_ids[i]
            for j in range(i + 1, len(sorted_line_ids)):
                id2 = sorted_line_ids[j]

                # Actions on id1 paired with actions on id2
                for a1 in line_candidate_actions[id1]:
                    for a2 in line_candidate_actions[id2]:
                        if len(evaluated_plans) >= max_scenarios:
                            is_truncated = True
                            break

                        res = evaluate_action_plan(db, draft_id, [a1, a2])
                        evaluated_plans.append(res)
                        if res.is_successful:
                            successful_plans.append(res)
                    if is_truncated:
                        break
                if is_truncated:
                    break
            if is_truncated:
                break

    # 5. Depth 3 Evaluation (if requested and scenarios budget remains)
    if max_depth >= 3 and not is_truncated:
        sorted_line_ids = sorted(line_candidate_actions.keys())
        for i in range(len(sorted_line_ids)):
            id1 = sorted_line_ids[i]
            for j in range(i + 1, len(sorted_line_ids)):
                id2 = sorted_line_ids[j]
                for k in range(j + 1, len(sorted_line_ids)):
                    id3 = sorted_line_ids[k]

                    for a1 in line_candidate_actions[id1]:
                        for a2 in line_candidate_actions[id2]:
                            for a3 in line_candidate_actions[id3]:
                                if len(evaluated_plans) >= max_scenarios:
                                    is_truncated = True
                                    break

                                res = evaluate_action_plan(db, draft_id, [a1, a2, a3])
                                evaluated_plans.append(res)
                                if res.is_successful:
                                    successful_plans.append(res)
                                if is_truncated:
                                    break
                            if is_truncated:
                                break
                        if is_truncated:
                            break
                    if is_truncated:
                        break
                if is_truncated:
                    break
            if is_truncated:
                break

    # Sort successful plans by length (shortest first), then deterministically
    sorted_successes = tuple(
        sorted(successful_plans, key=lambda p: (len(p.actions), [getattr(a, "line_id", "") for a in p.actions]))
    )

    space_desc = (
        f"Generated {total_candidate_actions} candidate action(s) across {len(active_lines)} line(s). "
        f"Evaluated {len(evaluated_plans)} scenarios up to depth {max_depth} (cap {max_scenarios})."
    )

    coverage = SearchCoverageInfo(
        total_scenarios_evaluated=len(evaluated_plans),
        max_depth=max_depth,
        max_scenarios=max_scenarios,
        is_truncated=is_truncated,
        candidate_actions_count=total_candidate_actions,
        search_space_description=space_desc,
    )

    summary = (
        f"Counterfactual search completed: {len(sorted_successes)} successful plan(s) found "
        f"out of {len(evaluated_plans)} scenario(s) evaluated. "
        f"Search depth: {max_depth}. Truncated: {is_truncated}."
    )

    return CounterfactualAnalysisResult(
        draft_id=draft_id,
        initial_status=initial_status,
        is_already_ready=False,
        successful_plans=sorted_successes,
        all_evaluated_plans=tuple(evaluated_plans),
        coverage=coverage,
        summary=summary,
    )
