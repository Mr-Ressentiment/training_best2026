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
from app.symbolic.sku_confidence import (
    EvaluatedCandidate,
    SKUCandidate,
    SKUMatchConfidenceResult,
    evaluate_sku_match_confidence,
)

__all__ = [
    "EvaluatedCandidate",
    "FactValue",
    "Facts",
    "FuzzyRule",
    "FuzzyVar",
    "Rule",
    "SCHEMA_VERSION",
    "SKUCandidate",
    "SKUMatchConfidenceResult",
    "State",
    "TraceStep",
    "compose",
    "counterfactual",
    "default_verdict",
    "draft_to_facts",
    "evaluate_sku_match_confidence",
    "fuzzy_stage",
    "log",
    "rules_stage",
    "run_pipeline",
    "to_dict",
    "trap",
    "tri",
    "validate",
]

