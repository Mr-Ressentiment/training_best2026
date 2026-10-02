"""Symbolic reasoning and fuzzy logic inference package."""
from app.symbolic.engine import (
    Facts,
    Rule,
    SCHEMA_VERSION,
    State,
    TraceStep,
    compose,
    counterfactual,
    default_verdict,
    log,
    rules_stage,
    run_pipeline,
    to_dict,
    validate,
)
from app.symbolic.facts import (
    FactValue,
    draft_to_facts,
)
from app.symbolic.fuzzy import (
    FuzzyRule,
    FuzzyVar,
    fuzzy_stage,
    trap,
    tri,
)
from app.symbolic.counterfactuals import (
    CounterfactualAnalysisResult,
    LineItemSummary,
    OperatorAction,
    PlanEvaluationResult,
    RemainingBlocker,
    RemoveLineAction,
    RequestCorrectedPOAction,
    SearchCoverageInfo,
    SelectSKUAction,
    evaluate_action_plan,
    search_counterfactual_plans,
)
from app.symbolic.review_priority import (
    DiscrepancyItem,
    OrderReviewInput,
    ReviewPriorityResult,
    evaluate_review_priority,
    review_priority_stage,
)
from app.symbolic.sku_confidence import (
    EvaluatedCandidate,
    SKUCandidate,
    SKUMatchConfidenceResult,
    evaluate_sku_match_confidence,
)

__all__ = [
    "CounterfactualAnalysisResult",
    "DiscrepancyItem",
    "EvaluatedCandidate",
    "FactValue",
    "Facts",
    "FuzzyRule",
    "FuzzyVar",
    "LineItemSummary",
    "OperatorAction",
    "OrderReviewInput",
    "PlanEvaluationResult",
    "RemainingBlocker",
    "RemoveLineAction",
    "RequestCorrectedPOAction",
    "ReviewPriorityResult",
    "Rule",
    "SCHEMA_VERSION",
    "SKUCandidate",
    "SKUMatchConfidenceResult",
    "SearchCoverageInfo",
    "SelectSKUAction",
    "State",
    "TraceStep",
    "compose",
    "counterfactual",
    "default_verdict",
    "draft_to_facts",
    "evaluate_action_plan",
    "evaluate_review_priority",
    "evaluate_sku_match_confidence",
    "fuzzy_stage",
    "log",
    "review_priority_stage",
    "rules_stage",
    "run_pipeline",
    "search_counterfactual_plans",
    "to_dict",
    "trap",
    "tri",
    "validate",
]



