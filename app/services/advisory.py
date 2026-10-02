"""Read-only advisory engine for purchase order drafts.

Assembles on-the-fly advisory diagnostics across:
1. Baseline stored state & fresh sandbox reconciliation simulation.
2. Counterfactual dry-run of allowed operator actions in an isolated sandbox.
3. Fuzzy review priority and SKU match confidence evaluations.
4. Machine-readable derivation trace grounded in source document text and contract pricing.

Zero Database Writes Guarantee:
- All reads from caller DB session run strictly under `db.no_autoflush`.
- No flush, commit, or rollback is ever invoked on the caller session.
- No ORM entities in the caller identity map are mutated.
- All simulations execute inside dedicated in-memory SQLite sandboxes that are disposed immediately.
"""
from __future__ import annotations

from decimal import Decimal
import json
import math
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serialization import _money
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
from app.models.schemas import LocationDataSchema, cents_to_decimal
from app.services.order_service import DraftNotFoundError
from app.services.reconciliation import (
    calculate_line_total_cents,
    calculate_subtotal_cents,
    evaluate_clean_draft,
    select_contract_price_tier,
)
from app.symbolic.counterfactuals import (
    PlanEvaluationResult,
    _clone_to_sandbox,
    _extract_incompleteness_reasons,
    _extract_line_candidate_skus,
    search_counterfactual_plans,
)
from app.symbolic.review_priority import (
    DiscrepancyItem,
    OrderReviewInput,
    calculate_price_deviation_pct,
    evaluate_review_priority,
)

CANONICAL_SECTIONS: tuple[str, ...] = (
    "baseline",
    "counterfactuals",
    "review_priority",
    "sku_confidence",
    "trace",
)

# Deterministic advisory mapping for persisted sku_confidence labels to numeric scores
# Advisory heuristic only; not a calibrated probability; never fed back into reconciliation.
LABEL_TO_CONFIDENCE: dict[str, float] = {
    "High": 0.8500,
    "Ambiguous": 0.2500,
    "Unrecognized": 0.1000,
}


def _parse_candidate_skus(raw_json: str | None) -> list[str]:
    """Parse candidate SKUs list from JSON string."""
    if not raw_json:
        return []
    try:
        parsed = json.loads(raw_json)
        if isinstance(parsed, list):
            res: list[str] = []
            for item in parsed:
                if isinstance(item, str) and item.strip():
                    res.append(item.strip())
                elif isinstance(item, dict) and "sku" in item and isinstance(item["sku"], str):
                    res.append(item["sku"].strip())
            return sorted(set(res))
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _format_plan_id(actions: Sequence[Any]) -> str:
    """Generate deterministic plan ID string."""
    if not actions:
        return "plan:no_action"
    parts: list[str] = []
    for a in actions:
        if hasattr(a, "action_type"):
            atype = a.action_type
            if atype == "SelectSKU":
                parts.append(f"SelectSKU({getattr(a, 'line_id', '')},{getattr(a, 'sku', '')})")
            elif atype == "RemoveLine":
                parts.append(f"RemoveLine({getattr(a, 'line_id', '')})")
            elif atype == "RequestCorrectedPO":
                parts.append(f"RequestCorrectedPO({getattr(a, 'reason', '')})")
            else:
                parts.append(f"{atype}()")
        else:
            parts.append(repr(a))
    return "plan:" + ";".join(parts)


def build_draft_advisory(
    db: Session,
    draft_id: str,
    *,
    sections: Sequence[str] = CANONICAL_SECTIONS,
    max_depth: int = 2,
    max_scenarios: int = 24,
) -> DraftAdvisoryResponse:
    """Read-only compilation of draft advisory analysis.

    Executes completely under `db.no_autoflush` without modifying any state or session.
    """
    requested_set = set(sections)
    included = [s for s in CANONICAL_SECTIONS if s in requested_set]
    omitted = [s for s in CANONICAL_SECTIONS if s not in requested_set]

    # 1. Read persistent draft state under no_autoflush
    with db.no_autoflush:
        draft = db.get(OrderDraft, draft_id)
        if draft is None:
            raise DraftNotFoundError("Draft does not exist")

        persisted_status = draft.status
        is_terminal = persisted_status in ("Approved", "Rejected")
        document_id = draft.document_id
        customer_id = draft.customer_id
        customer_name = draft.customer_name_extracted
        po_number = draft.po_number_extracted
        is_replay_mode = draft.is_replay_mode
        persisted_subtotal_cents = draft.calculated_subtotal_cents
        extracted_order_total_cents = draft.extracted_order_total_cents

        doc = db.get(PurchaseOrderDocument, document_id) if document_id else None
        doc_raw_text = doc.raw_text if doc else None

        # Snapshots of children sorted deterministically
        lines_orm = sorted(draft.line_items, key=lambda l: (l.line_number, l.id))
        provenance_orm = sorted(draft.provenance_records, key=lambda p: (p.line_item_id or "", p.field_name, p.id))
        flags_orm = sorted(draft.discrepancy_flags, key=lambda f: (f.line_item_id or "", f.discrepancy_type, f.id))

        active_lines_orm = [l for l in lines_orm if l.status == "Active"]

    # 2. Baseline Reconciliation Simulation (in dedicated sandbox if not terminal)
    simulated_status: str | None = None
    simulated_flags: list[DiscrepancyFlag] = []
    completeness_reasons: list[str] = []
    incomplete_inputs: list[str] = []

    if not is_terminal:
        sb_session, sb_engine, sb_draft = _clone_to_sandbox(db, draft_id)
        try:
            evaluate_clean_draft(sb_session, sb_draft)
            simulated_status = sb_draft.status
            simulated_flags = list(sb_draft.discrepancy_flags)
            completeness_reasons = list(_extract_incompleteness_reasons(sb_draft))
        finally:
            sb_session.close()
            sb_engine.dispose()
    else:
        # Terminal draft: cannot evaluate via evaluate_clean_draft
        simulated_status = None
        simulated_flags = list(flags_orm)
        completeness_reasons = []

    # Identify missing input fields
    if not customer_id:
        incomplete_inputs.append("customer_id")
    if not customer_name:
        incomplete_inputs.append("customer_name")
    if not po_number:
        incomplete_inputs.append("po_number")
    if not active_lines_orm:
        incomplete_inputs.append("active_lines")

    is_stale_evaluation = False
    if not is_terminal and simulated_status is not None:
        is_stale_evaluation = (persisted_status != simulated_status)

    # 3. Construct Baseline Lines & Price Sources
    baseline_lines: list[BaselineLine] = []
    line_number_by_id: dict[str, int] = {}

    with db.no_autoflush:
        for l in lines_orm:
            line_number_by_id[l.id] = l.line_number
            cand_skus = _parse_candidate_skus(l.candidate_skus_json)
            prod = db.get(CatalogProduct, l.matched_sku) if l.matched_sku else None

            b_line = BaselineLine(
                line_id=l.id,
                line_number=l.line_number,
                customer_description=l.customer_description,
                extracted_quantity=l.extracted_quantity,
                extracted_unit_price=_money(l.extracted_unit_price_cents),
                extracted_line_total=_money(l.extracted_line_total_cents),
                matched_sku=l.matched_sku,
                sku_name=prod.name if prod else None,
                sku_confidence_label=l.sku_confidence,
                sku_resolution_source=l.sku_resolution_source,
                candidate_skus=cand_skus,
                contract_price=_money(l.contract_price_cents),
                calculated_line_total=_money(l.calculated_line_total_cents),
                status=l.status,
            )
            baseline_lines.append(b_line)

    # 4. Construct Baseline Blockers
    baseline_blockers: list[BaselineBlocker] = []
    flags_to_use = simulated_flags if not is_terminal else flags_orm

    with db.no_autoflush:
        for flag in flags_to_use:
            if flag.resolution_state != "Unresolved":
                continue
            line_num = line_number_by_id.get(flag.line_item_id) if flag.line_item_id else None
            price_source: PriceSourceInfo | None = None

            if flag.discrepancy_type == "PriceMismatch" and flag.line_item_id:
                # Find matching contract price tier in DB
                target_line = next((l for l in lines_orm if l.id == flag.line_item_id), None)
                if target_line and target_line.matched_sku and target_line.extracted_quantity and customer_id:
                    tier = select_contract_price_tier(
                        db,
                        customer_id=customer_id,
                        sku=target_line.matched_sku,
                        quantity=target_line.extracted_quantity,
                    )
                    if tier:
                        price_source = PriceSourceInfo(
                            contract_id=tier.contract_id,
                            tier_id=tier.id,
                            min_quantity=tier.min_quantity,
                            tier_price=_money(tier.tier_price_cents) or "0.00",
                        )

            blocker = BaselineBlocker(
                flag_id=flag.id if hasattr(flag, "id") else None,
                line_id=flag.line_item_id,
                line_number=line_num,
                discrepancy_type=flag.discrepancy_type,
                severity=flag.severity,
                expected_value=flag.expected_value,
                requested_value=flag.requested_value,
                explanation=flag.explanation,
                resolution_state=flag.resolution_state,
                scope="line" if flag.line_item_id else "order",
                price_source=price_source,
            )
            baseline_blockers.append(blocker)

    # Sort blockers deterministically: line_number, discrepancy_type, flag_id
    baseline_blockers.sort(
        key=lambda b: (
            b.line_number if b.line_number is not None else -1,
            b.discrepancy_type,
            b.flag_id or "",
        )
    )

    # 5. Order Arithmetic Status
    calculated_subtotal_cents = 0
    for l in active_lines_orm:
        if l.calculated_line_total_cents:
            calculated_subtotal_cents += l.calculated_line_total_cents

    has_subtotal_mismatch = (persisted_subtotal_cents != calculated_subtotal_cents)
    has_order_total_mismatch = False
    missing_order_total_reason: str | None = None

    if extracted_order_total_cents is not None:
        has_order_total_mismatch = (extracted_order_total_cents != calculated_subtotal_cents)
    else:
        missing_order_total_reason = "No order-level total was extracted from document header"

    order_arithmetic = OrderArithmeticInfo(
        persisted_subtotal=_money(persisted_subtotal_cents),
        calculated_subtotal=_money(calculated_subtotal_cents),
        extracted_order_total=_money(extracted_order_total_cents),
        extracted_order_total_missing_reason=missing_order_total_reason,
        has_subtotal_mismatch=has_subtotal_mismatch,
        has_order_total_mismatch=has_order_total_mismatch,
    )

    baseline_section: BaselineSection | None = None
    if "baseline" in included:
        baseline_section = BaselineSection(
            status_persisted=persisted_status,
            simulated_status=simulated_status,
            is_stale_evaluation=is_stale_evaluation,
            blockers=baseline_blockers,
            incomplete_inputs=incomplete_inputs,
            completeness_reasons=completeness_reasons,
            lines=baseline_lines,
            order_arithmetic=order_arithmetic,
        )

    # 6. Counterfactual Operator Action Analysis
    counterfactuals_section: CounterfactualsSection | None = None
    successful_cf_plans: list[CounterfactualPlan] = []
    blocked_cf_plans: list[CounterfactualPlan] = []
    invalid_cf_plans: list[CounterfactualPlan] = []
    ext_input_info: ExternalInputInfo | None = None

    if "counterfactuals" in included:
        if is_terminal:
            counterfactuals_section = CounterfactualsSection(
                limits=CounterfactualLimits(max_depth=max_depth, max_scenarios=max_scenarios),
                coverage=CounterfactualCoverage(
                    total_scenarios_evaluated=0,
                    is_truncated=False,
                    search_is_exhaustive=True,
                    candidate_actions_count=0,
                    search_space_description="Terminal draft: operator actions are prohibited.",
                    interpretation_note="Draft is in terminal status (Approved/Rejected); counterfactual search not applicable.",
                ),
                actions_considered=[],
                successful_plans=[],
                blocked_plans=[],
                requires_external_input=None,
                invalid_plans=[],
            )
        else:
            cf_res = search_counterfactual_plans(
                db,
                draft_id,
                max_depth=max_depth,
                max_scenarios=max_scenarios,
            )

            def _to_cf_plan(p: PlanEvaluationResult) -> CounterfactualPlan:
                actions_dto: list[CounterfactualActionItem] = []
                for a in p.actions:
                    atype = getattr(a, "action_type", type(a).__name__)
                    lid = getattr(a, "line_id", None)
                    lnum = line_number_by_id.get(lid) if lid else None
                    sku = getattr(a, "sku", None)
                    reason = getattr(a, "reason", None)
                    actions_dto.append(
                        CounterfactualActionItem(
                            action_type=atype,
                            line_id=lid,
                            line_number=lnum,
                            sku=sku,
                            reason=reason,
                        )
                    )

                remaining = [
                    PlanBlocker(
                        category=b.category,
                        scope=b.scope,
                        line_id=b.line_id,
                        line_number=b.line_number,
                        explanation=b.explanation,
                    )
                    for b in p.remaining_blockers
                ]

                return CounterfactualPlan(
                    plan_id=_format_plan_id(p.actions),
                    actions=actions_dto,
                    outcome=p.outcome,
                    simulated_status=p.simulated_status,
                    is_successful=p.is_successful,
                    remaining_blockers=remaining,
                    incompleteness_reasons=list(p.incompleteness_reasons),
                    explanation=p.explanation,
                )

            if not cf_res.is_already_ready:
                for p in cf_res.successful_plans:
                    successful_cf_plans.append(_to_cf_plan(p))

            for p in cf_res.all_evaluated_plans:
                if not p.is_successful and p.outcome != "invalid":
                    blocked_cf_plans.append(_to_cf_plan(p))
                elif p.outcome == "invalid":
                    invalid_cf_plans.append(_to_cf_plan(p))

            # Sort plans: shortest-first, then lexicographically by plan_id
            successful_cf_plans.sort(key=lambda p: (len(p.actions), p.plan_id))
            blocked_cf_plans.sort(key=lambda p: (len(p.actions), p.plan_id))
            invalid_cf_plans.sort(key=lambda p: (len(p.actions), p.plan_id))

            # Action types evaluated
            select_sku_count = 0
            remove_line_count = 0
            for l in active_lines_orm:
                cand = _parse_candidate_skus(l.candidate_skus_json)
                select_sku_count += len(cand)
                remove_line_count += 1

            actions_considered = [
                ActionCountInfo(action_type="SelectSKU", count=select_sku_count),
                ActionCountInfo(action_type="RemoveLine", count=remove_line_count),
                ActionCountInfo(action_type="RequestCorrectedPO", count=1),
            ]

            # Determine external input requirement
            # If no successful internal plans exist, identify if commercial blockers require customer PO correction
            if not successful_cf_plans and baseline_blockers:
                commercial_reasons: list[str] = []
                for b in baseline_blockers:
                    if b.discrepancy_type in ("PriceMismatch", "QuantityOrPackagingBreach", "ArithmeticMismatch"):
                        commercial_reasons.append(f"{b.discrepancy_type}: {b.explanation}")
                if commercial_reasons:
                    ext_input_info = ExternalInputInfo(
                        is_required=True,
                        reasons=commercial_reasons,
                        explanation=(
                            "No internal operator action plan achieves 'Ready for Approval' without prohibited commercial overrides. "
                            "A corrected purchase order from the customer is required."
                        ),
                    )

            interpretation_note = (
                "Search is bounded by max_depth and max_scenarios. When truncated, the absence of successful plans "
                "within the evaluated bounds does not establish global insolvability. Commercial overrides remain prohibited."
            )

            counterfactuals_section = CounterfactualsSection(
                limits=CounterfactualLimits(
                    max_depth=cf_res.coverage.max_depth,
                    max_scenarios=cf_res.coverage.max_scenarios,
                ),
                coverage=CounterfactualCoverage(
                    total_scenarios_evaluated=cf_res.coverage.total_scenarios_evaluated,
                    is_truncated=cf_res.coverage.is_truncated,
                    search_is_exhaustive=not cf_res.coverage.is_truncated,
                    candidate_actions_count=cf_res.coverage.candidate_actions_count,
                    search_space_description=cf_res.coverage.search_space_description,
                    interpretation_note=interpretation_note,
                ),
                actions_considered=actions_considered,
                successful_plans=successful_cf_plans,
                blocked_plans=blocked_cf_plans,
                requires_external_input=ext_input_info,
                invalid_plans=invalid_cf_plans,
            )

    # 7. Advisory Status Derivation
    if is_terminal:
        advisory_status = AdvisoryStatus.NOT_APPLICABLE_TERMINAL
    elif simulated_status == "Ready for Approval" and not baseline_blockers:
        advisory_status = AdvisoryStatus.READY_NO_ACTION_NEEDED
    elif "counterfactuals" in included and successful_cf_plans:
        advisory_status = AdvisoryStatus.ACTIONABLE_PLANS_FOUND
    else:
        advisory_status = AdvisoryStatus.BLOCKED_NO_INTERNAL_PLAN

    # 8. Review Priority Section
    review_priority_section: ReviewPrioritySection | None = None
    if "review_priority" in included:
        discrepancy_count = len(baseline_blockers)
        discrepancy_items: list[DiscrepancyItem] = []
        max_p_dev = 0.0

        for b in baseline_blockers:
            item_sev = b.severity
            item_dev: float | None = None

            if b.discrepancy_type == "PriceMismatch" and b.price_source:
                # Calculate price deviation pct
                try:
                    exp_cents = int(Decimal(b.price_source.tier_price) * 100)
                    line_target = next((l for l in lines_orm if l.id == b.line_id), None)
                    if line_target and line_target.extracted_unit_price_cents is not None:
                        item_dev = calculate_price_deviation_pct(
                            line_target.extracted_unit_price_cents,
                            exp_cents,
                        )
                        max_p_dev = max(max_p_dev, item_dev)
                except Exception:
                    pass

            discrepancy_items.append(
                DiscrepancyItem(
                    severity=item_sev,
                    price_deviation_pct=item_dev,
                )
            )

        # SKU match confidence determination from active lines
        line_conf_scores: list[float] = []
        has_persisted_label = False
        primary_label: str | None = None

        for l in active_lines_orm:
            lbl = l.sku_confidence
            if lbl:
                has_persisted_label = True
                if primary_label is None:
                    primary_label = lbl
                if lbl in LABEL_TO_CONFIDENCE:
                    line_conf_scores.append(LABEL_TO_CONFIDENCE[lbl])

        if line_conf_scores:
            order_match_conf = min(line_conf_scores)
            conf_source = "mapped_from_label"
        else:
            order_match_conf = None
            conf_source = "unavailable"

        eff_uncertainty = 1.0 - (order_match_conf if order_match_conf is not None else 0.0)

        # Execute existing symbolic review priority module
        rp_res = evaluate_review_priority(
            discrepancy_count=discrepancy_count,
            discrepancies=discrepancy_items if discrepancy_items else None,
            price_deviation_pct=max_p_dev if max_p_dev > 0.0 else None,
            order_total_cents=calculated_subtotal_cents if calculated_subtotal_cents > 0 else None,
            match_confidence=order_match_conf,
        )

        inputs_used = ReviewPriorityInputsUsed(
            discrepancy_count=discrepancy_count,
            discrepancy_severity=round(rp_res.features_used.get("discrepancy_severity", 0.0), 4),
            price_deviation_pct=round(max_p_dev, 4),
            order_total_cents=calculated_subtotal_cents if calculated_subtotal_cents > 0 else None,
            order_total_formatted=_money(calculated_subtotal_cents),
            normalized_order_total=round(rp_res.features_used.get("normalized_order_total", 0.0), 4),
            match_confidence=round(order_match_conf, 4) if order_match_conf is not None else None,
            match_confidence_source=conf_source,
            persisted_match_confidence_label=primary_label,
            effective_uncertainty=round(eff_uncertainty, 4),
        )

        review_priority_section = ReviewPrioritySection(
            review_priority=round(rp_res.review_priority, 4),
            review_priority_label=rp_res.review_priority_label,
            raw_fuzzy_score=round(rp_res.raw_fuzzy_score, 4),
            has_incomplete_data=rp_res.has_incomplete_data,
            monotonic_adjustment=rp_res.monotonic_adjustment,
            applied_rules=rp_res.applied_rules,
            inputs_used=inputs_used,
        )

    # 9. SKU Confidence Section
    sku_confidence_section: SKUConfidenceSection | None = None
    if "sku_confidence" in included:
        line_confidences: list[LineSKUConfidence] = []

        for l in lines_orm:
            lbl = l.sku_confidence
            conf_val: float | None = None
            conf_lbl: str | None = None
            source: str = "unavailable"
            reason: str | None = None

            if lbl in LABEL_TO_CONFIDENCE:
                conf_val = LABEL_TO_CONFIDENCE[lbl]
                source = "mapped_from_label"
                if lbl == "High":
                    conf_lbl = "high"
                    reason = "Persisted label 'High' mapped to advisory confidence 0.85."
                elif lbl == "Ambiguous":
                    conf_lbl = "low"
                    reason = "Persisted label 'Ambiguous' mapped to advisory confidence 0.25 (ambiguity downgrade)."
                elif lbl == "Unrecognized":
                    conf_lbl = "low"
                    reason = "Persisted label 'Unrecognized' mapped to advisory confidence 0.10."
            else:
                conf_val = None
                conf_lbl = None
                source = "unavailable"
                reason = "No SKU confidence label persisted on line item."

            line_confidences.append(
                LineSKUConfidence(
                    line_id=l.id,
                    line_number=l.line_number,
                    customer_description=l.customer_description,
                    persisted_label=lbl,
                    matched_sku=l.matched_sku,
                    evaluated_confidence=round(conf_val, 4) if conf_val is not None else None,
                    evaluated_label=conf_lbl,
                    confidence_source=source,
                    score_margin=None,
                    reason=reason,
                    candidates=[],
                )
            )

        line_confidences.sort(key=lambda lc: (lc.line_number, lc.line_id))
        sku_confidence_section = SKUConfidenceSection(lines=line_confidences)

    # 10. Trace Section Construction with Verifiable Grounding
    trace_section: TraceSection | None = None
    if "trace" in included:
        trace_steps: list[TraceStepItem] = []
        step_idx = 1

        # Provenance index by (line_id or None, field_name)
        provenance_by_key: dict[tuple[str | None, str], FieldProvenance] = {
            (p.line_item_id, p.field_name): p for p in provenance_orm
        }

        def _make_doc_evidence(field_name: str, line_item_id: str | None) -> TraceEvidence | None:
            p = provenance_by_key.get((line_item_id, field_name))
            if p is None:
                return None
            try:
                loc_dict = json.loads(p.location_data_json)
                loc_schema = LocationDataSchema.model_validate(loc_dict)
                return TraceEvidence(
                    source_type="document",
                    document_evidence=DocumentEvidenceData(
                        field_name=p.field_name,
                        verbatim_snippet=p.verbatim_snippet,
                        location=loc_schema,
                    ),
                )
            except Exception:
                return None

        # Step: Input Completeness
        ev_comp: list[TraceEvidence] = []
        ev_cust_name = _make_doc_evidence("customer_name", None)
        if ev_cust_name:
            ev_comp.append(ev_cust_name)
        ev_po_num = _make_doc_evidence("po_number", None)
        if ev_po_num:
            ev_comp.append(ev_po_num)

        trace_steps.append(
            TraceStepItem(
                step_index=step_idx,
                stage=TraceStage.INPUT_COMPLETENESS,
                subject="order",
                inputs={
                    "customer_id": customer_id,
                    "customer_name": customer_name,
                    "po_number": po_number,
                    "active_lines_count": len(active_lines_orm),
                },
                rule_or_decision="input_completeness",
                outcome="blocking" if incomplete_inputs else "ok",
                explanation=(
                    "Mandatory inputs check complete. "
                    + (f"Missing: {incomplete_inputs}" if incomplete_inputs else "All header fields and active lines present.")
                ),
                evidence=ev_comp,
            )
        )
        step_idx += 1

        # Per-Line Steps: Catalog resolution, Contract pricing, Line arithmetic, MOQ packaging
        with db.no_autoflush:
            for l in active_lines_orm:
                subj = f"line:{l.line_number}"

                # a. Catalog resolution step
                cat_blocker = next(
                    (b for b in baseline_blockers if b.line_id == l.id and b.discrepancy_type == "CatalogMatchingMismatch"),
                    None,
                )
                ev_cat: list[TraceEvidence] = []
                ev_desc = _make_doc_evidence("customer_description", l.id)
                if ev_desc:
                    ev_cat.append(ev_desc)
                prod = db.get(CatalogProduct, l.matched_sku) if l.matched_sku else None
                if prod:
                    ev_cat.append(
                        TraceEvidence(
                            source_type="reference_data",
                            reference_evidence=ReferenceEvidenceData(sku=prod.sku),
                        )
                    )

                trace_steps.append(
                    TraceStepItem(
                        step_index=step_idx,
                        stage=TraceStage.CATALOG_RESOLUTION,
                        subject=subj,
                        inputs={
                            "customer_description": l.customer_description,
                            "matched_sku": l.matched_sku,
                            "sku_confidence": l.sku_confidence,
                        },
                        rule_or_decision="CatalogMatchingMismatch" if cat_blocker else "catalog_resolution",
                        outcome="blocking" if cat_blocker else "ok",
                        explanation=(
                            cat_blocker.explanation
                            if cat_blocker
                            else f"Line resolved to master catalog SKU '{l.matched_sku}'."
                        ),
                        evidence=ev_cat,
                    )
                )
                step_idx += 1

                # b. Contract pricing step
                price_blocker = next(
                    (b for b in baseline_blockers if b.line_id == l.id and b.discrepancy_type == "PriceMismatch"),
                    None,
                )
                ev_price: list[TraceEvidence] = []
                ev_uprice = _make_doc_evidence("extracted_unit_price", l.id)
                if ev_uprice:
                    ev_price.append(ev_uprice)
                if price_blocker and price_blocker.price_source:
                    ev_price.append(
                        TraceEvidence(
                            source_type="reference_data",
                            reference_evidence=ReferenceEvidenceData(
                                sku=l.matched_sku or "",
                                contract_id=price_blocker.price_source.contract_id,
                                tier_id=price_blocker.price_source.tier_id,
                                min_quantity=price_blocker.price_source.min_quantity,
                                tier_price=price_blocker.price_source.tier_price,
                            ),
                        )
                    )

                trace_steps.append(
                    TraceStepItem(
                        step_index=step_idx,
                        stage=TraceStage.CONTRACT_PRICING,
                        subject=subj,
                        inputs={
                            "matched_sku": l.matched_sku,
                            "extracted_quantity": l.extracted_quantity,
                            "extracted_unit_price": _money(l.extracted_unit_price_cents),
                            "contract_price": _money(l.contract_price_cents),
                        },
                        rule_or_decision="PriceMismatch" if price_blocker else "contract_pricing",
                        outcome="blocking" if price_blocker else "ok",
                        explanation=(
                            price_blocker.explanation
                            if price_blocker
                            else f"Line pricing matches contract tier price '{_money(l.contract_price_cents)}'."
                        ),
                        evidence=ev_price,
                    )
                )
                step_idx += 1

                # c. Line arithmetic step
                ev_arith: list[TraceEvidence] = []
                ev_qty = _make_doc_evidence("extracted_quantity", l.id)
                if ev_qty:
                    ev_arith.append(ev_qty)
                ev_ltot = _make_doc_evidence("extracted_line_total", l.id)
                if ev_ltot:
                    ev_arith.append(ev_ltot)

                line_arith_blocker = next(
                    (b for b in baseline_blockers if b.line_id == l.id and b.discrepancy_type == "ArithmeticMismatch"),
                    None,
                )
                expected_total = (
                    l.extracted_quantity * l.extracted_unit_price_cents
                    if l.extracted_quantity is not None and l.extracted_unit_price_cents is not None
                    else None
                )
                ev_arith.append(
                    TraceEvidence(
                        source_type="derived",
                        derived_evidence=DerivedEvidenceData(
                            formula="extracted_quantity * extracted_unit_price",
                            input_values={
                                "quantity": l.extracted_quantity,
                                "unit_price": _money(l.extracted_unit_price_cents),
                            },
                            result_value=_money(expected_total),
                        ),
                    )
                )

                trace_steps.append(
                    TraceStepItem(
                        step_index=step_idx,
                        stage=TraceStage.LINE_ARITHMETIC,
                        subject=subj,
                        inputs={
                            "extracted_quantity": l.extracted_quantity,
                            "extracted_unit_price": _money(l.extracted_unit_price_cents),
                            "extracted_line_total": _money(l.extracted_line_total_cents),
                        },
                        rule_or_decision="ArithmeticMismatch.line" if line_arith_blocker else "line_arithmetic",
                        outcome="blocking" if line_arith_blocker else "ok",
                        explanation=(
                            line_arith_blocker.explanation
                            if line_arith_blocker
                            else f"Extracted line total matches calculated customer total '{_money(expected_total)}'."
                        ),
                        evidence=ev_arith,
                    )
                )
                step_idx += 1

                # d. MOQ / packaging step
                moq_blocker = next(
                    (b for b in baseline_blockers if b.line_id == l.id and b.discrepancy_type == "QuantityOrPackagingBreach"),
                    None,
                )
                ev_moq: list[TraceEvidence] = []
                if ev_qty:
                    ev_moq.append(ev_qty)
                if prod:
                    ev_moq.append(
                        TraceEvidence(
                            source_type="reference_data",
                            reference_evidence=ReferenceEvidenceData(
                                sku=prod.sku,
                                min_quantity=prod.min_order_quantity,
                            ),
                        )
                    )

                trace_steps.append(
                    TraceStepItem(
                        step_index=step_idx,
                        stage=TraceStage.MOQ_PACKAGING,
                        subject=subj,
                        inputs={
                            "extracted_quantity": l.extracted_quantity,
                            "min_order_quantity": prod.min_order_quantity if prod else None,
                            "package_increment": prod.package_increment if prod else None,
                        },
                        rule_or_decision="QuantityOrPackagingBreach" if moq_blocker else "moq_packaging",
                        outcome="blocking" if moq_blocker else "ok",
                        explanation=(
                            moq_blocker.explanation
                            if moq_blocker
                            else f"Quantity {l.extracted_quantity} satisfies catalog packaging and MOQ constraints."
                        ),
                        evidence=ev_moq,
                    )
                )
                step_idx += 1

        # Step: Order Arithmetic
        order_arith_blocker = next(
            (b for b in baseline_blockers if b.scope == "order" and b.discrepancy_type == "ArithmeticMismatch"),
            None,
        )
        ev_oarith: list[TraceEvidence] = [
            TraceEvidence(
                source_type="derived",
                derived_evidence=DerivedEvidenceData(
                    formula="sum(calculated_line_total_cents)",
                    input_values={"lines": [l.calculated_line_total_cents for l in active_lines_orm]},
                    result_value=_money(calculated_subtotal_cents),
                ),
            )
        ]
        trace_steps.append(
            TraceStepItem(
                step_index=step_idx,
                stage=TraceStage.ORDER_ARITHMETIC,
                subject="order",
                inputs={
                    "calculated_subtotal": _money(calculated_subtotal_cents),
                    "persisted_subtotal": _money(persisted_subtotal_cents),
                    "extracted_order_total": _money(extracted_order_total_cents),
                },
                rule_or_decision="ArithmeticMismatch.order" if order_arith_blocker else "order_arithmetic",
                outcome="blocking" if order_arith_blocker else "ok",
                explanation=(
                    order_arith_blocker.explanation
                    if order_arith_blocker
                    else f"Calculated subtotal is '{_money(calculated_subtotal_cents)}'."
                ),
                evidence=ev_oarith,
            )
        )
        step_idx += 1

        # Step: Staleness (if evaluation is stale)
        if is_stale_evaluation:
            trace_steps.append(
                TraceStepItem(
                    step_index=step_idx,
                    stage=TraceStage.STALENESS,
                    subject="order",
                    inputs={
                        "status_persisted": persisted_status,
                        "simulated_status": simulated_status,
                    },
                    rule_or_decision="status_staleness_check",
                    outcome="warning",
                    explanation=f"Stored status '{persisted_status}' does not match freshly simulated status '{simulated_status}'.",
                    evidence=[],
                )
            )
            step_idx += 1

        # Steps: Counterfactual Actions (if counterfactuals section requested)
        if "counterfactuals" in included:
            for p in successful_cf_plans:
                trace_steps.append(
                    TraceStepItem(
                        step_index=step_idx,
                        stage=TraceStage.COUNTERFACTUAL_ACTION,
                        subject="order",
                        inputs={"plan_id": p.plan_id, "actions": [a.model_dump() for a in p.actions]},
                        rule_or_decision="counterfactual_action_successful",
                        outcome="action_simulated",
                        explanation=f"Plan successfully transitions draft to 'Ready for Approval': {p.explanation}",
                        evidence=[
                            TraceEvidence(
                                source_type="derived",
                                derived_evidence=DerivedEvidenceData(
                                    formula="evaluate_clean_draft(sandbox, applied_plan)",
                                    input_values={"actions": [a.action_type for a in p.actions]},
                                    result_value="Ready for Approval",
                                ),
                            )
                        ],
                    )
                )
                step_idx += 1

            for p in blocked_cf_plans[:5]:  # include up to 5 representative blocked plans in trace
                trace_steps.append(
                    TraceStepItem(
                        step_index=step_idx,
                        stage=TraceStage.COUNTERFACTUAL_ACTION,
                        subject="order",
                        inputs={"plan_id": p.plan_id, "actions": [a.model_dump() for a in p.actions]},
                        rule_or_decision="counterfactual_action_blocked",
                        outcome="blocking",
                        explanation=f"Plan remains blocked: {p.explanation}",
                        evidence=[],
                    )
                )
                step_idx += 1

        # Step: Review Priority
        if "review_priority" in included and review_priority_section:
            trace_steps.append(
                TraceStepItem(
                    step_index=step_idx,
                    stage=TraceStage.REVIEW_PRIORITY,
                    subject="order",
                    inputs=review_priority_section.inputs_used.model_dump(),
                    rule_or_decision="evaluate_review_priority",
                    outcome="info",
                    explanation=(
                        f"Review priority computed as {review_priority_section.review_priority:.4f} "
                        f"({review_priority_section.review_priority_label})."
                    ),
                    evidence=[],
                )
            )
            step_idx += 1

        # Step: Readiness Verdict
        trace_steps.append(
            TraceStepItem(
                step_index=step_idx,
                stage=TraceStage.READINESS,
                subject="order",
                inputs={
                    "simulated_status": simulated_status,
                    "blockers_count": len(baseline_blockers),
                    "advisory_status": advisory_status.value,
                },
                rule_or_decision="readiness_verdict",
                outcome="ok" if advisory_status == AdvisoryStatus.READY_NO_ACTION_NEEDED else "blocking",
                explanation=f"Advisory status verdict: {advisory_status.value}.",
                evidence=[],
            )
        )

        trace_section = TraceSection(steps=trace_steps)

    return DraftAdvisoryResponse(
        schema_version=ADVISORY_SCHEMA_VERSION,
        draft_id=draft_id,
        document_id=document_id,
        customer_id=customer_id,
        customer_name_extracted=customer_name,
        po_number_extracted=po_number,
        status_persisted=persisted_status,
        is_replay_mode=is_replay_mode,
        advisory_status=advisory_status,
        included_sections=included,
        sections_omitted=omitted,
        baseline=baseline_section,
        counterfactuals=counterfactuals_section,
        review_priority=review_priority_section,
        sku_confidence=sku_confidence_section,
        trace=trace_section,
    )
