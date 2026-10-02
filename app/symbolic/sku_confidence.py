"""Fuzzy SKU matching confidence evaluator and ambiguity post-processor.

Evaluates SKU candidates using a Mamdani fuzzy inference engine and resolves
selection ambiguity among candidates to compute a calibrated match_confidence
decision in {'low', 'medium', 'high'}.

Architecture:
1. Input Features (per candidate):
   - description_similarity in [0.0, 1.0]: semantic match between customer description and catalog product.
   - price_closeness in [0.0, 1.0]: proximity of customer-stated price to contract/catalog expected price.
   - quantity_plausibility in [0.0, 1.0]: agreement with catalog MOQ and package increments.

2. Fuzzy Inference Stage:
   - Evaluates each candidate independently using the Mamdani engine from app.symbolic.fuzzy.
   - Computes a continuous candidate_score in [0.0, 1.0] via centroid (center of gravity) defuzzification.
   - Assigns individual candidate_label in {'low', 'medium', 'high'}.

3. Ambiguity & Selection Post-Processor:
   - Sorts candidates descending by candidate_score.
   - Identifies best_score, runner_up_score, and score_margin.
   - If two leading candidates are close (score_margin < AMBIGUITY_MARGIN_THRESHOLD),
     downgrades overall match_confidence to 'low' with reason='ambiguity'.
   - If competitor is distinctly weaker (score_margin >= AMBIGUITY_MARGIN_THRESHOLD),
     preserves high confidence.
   - If only a single candidate exists, grants 'high' only when its features are genuinely strong.
   - Enforces strict consistency: numeric match_confidence and label always agree.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

from app.symbolic.engine import State
from app.symbolic.fuzzy import (
    FuzzyRule,
    FuzzyVar,
    fuzzy_stage,
    trap,
    tri,
)

# -----------------------------------------------------------------------------
# Decision Thresholds & Crossover Constants
# -----------------------------------------------------------------------------

# Dominant membership crossovers in the output universe:
# [0.0, 0.40) -> 'low'
# [0.40, 0.70) -> 'medium'
# [0.70, 1.0] -> 'high'
LOW_THRESHOLD: float = 0.40
HIGH_THRESHOLD: float = 0.70

# Margin below which competing candidates are considered ambiguous
AMBIGUITY_MARGIN_THRESHOLD: float = 0.15

# Upper ceiling for match_confidence when ambiguity penalty is active
# Guaranteed to remain strictly inside the 'low' band (< 0.40)
LOW_CEILING: float = 0.35


# -----------------------------------------------------------------------------
# Membership Functions & Linguistic Sets
# -----------------------------------------------------------------------------

DESC_VAR = FuzzyVar(
    name="description_similarity",
    lo=0.0,
    hi=1.0,
    sets={
        "low": trap(0.0, 0.0, 0.25, 0.45),
        "medium": tri(0.30, 0.55, 0.80),
        "high": trap(0.65, 0.85, 1.0, 1.0),
    },
)

PRICE_VAR = FuzzyVar(
    name="price_closeness",
    lo=0.0,
    hi=1.0,
    sets={
        "low": trap(0.0, 0.0, 0.25, 0.45),
        "medium": tri(0.30, 0.55, 0.80),
        "high": trap(0.65, 0.85, 1.0, 1.0),
    },
)

QTY_VAR = FuzzyVar(
    name="quantity_plausibility",
    lo=0.0,
    hi=1.0,
    sets={
        "low": trap(0.0, 0.0, 0.25, 0.45),
        "medium": tri(0.30, 0.55, 0.80),
        "high": trap(0.65, 0.85, 1.0, 1.0),
    },
)

CANDIDATE_SCORE_VAR = FuzzyVar(
    name="candidate_score",
    lo=0.0,
    hi=1.0,
    sets={
        "low": trap(0.0, 0.0, 0.25, 0.45),
        "medium": tri(0.35, 0.55, 0.75),
        "high": trap(0.65, 0.85, 1.0, 1.0),
    },
)


# -----------------------------------------------------------------------------
# Complete 27-Rule Fuzzy Table
# -----------------------------------------------------------------------------
# Format: (description_similarity, price_closeness, quantity_plausibility) -> candidate_score
FUZZY_RULES_TABLE: tuple[tuple[str, str, str, str], ...] = (
    # 1-9: Low description similarity -> ALWAYS low output
    # (Anchor principle: If description does not match, price/qty cannot compensate)
    ("low", "low", "low", "low"),
    ("low", "low", "medium", "low"),
    ("low", "low", "high", "low"),
    ("low", "medium", "low", "low"),
    ("low", "medium", "medium", "low"),
    ("low", "medium", "high", "low"),
    ("low", "high", "low", "low"),
    ("low", "high", "medium", "low"),
    ("low", "high", "high", "low"),

    # 10-18: Medium description similarity
    ("medium", "low", "low", "low"),
    ("medium", "low", "medium", "low"),
    ("medium", "low", "high", "low"),
    ("medium", "medium", "low", "low"),
    ("medium", "medium", "medium", "medium"),
    ("medium", "medium", "high", "medium"),
    ("medium", "high", "low", "low"),
    ("medium", "high", "medium", "medium"),
    ("medium", "high", "high", "medium"),

    # 19-27: High description similarity
    ("high", "low", "low", "low"),
    ("high", "low", "medium", "low"),
    ("high", "low", "high", "medium"),
    ("high", "medium", "low", "low"),
    ("high", "medium", "medium", "medium"),
    ("high", "medium", "high", "high"),
    ("high", "high", "low", "medium"),
    ("high", "high", "medium", "high"),
    ("high", "high", "high", "high"),
)

CANDIDATE_FUZZY_RULES: list[FuzzyRule] = [
    FuzzyRule(
        id=f"SKU_{d[:1].upper()}_{p[:1].upper()}_{q[:1].upper()}",
        ifs={
            "description_similarity": d,
            "price_closeness": p,
            "quantity_plausibility": q,
        },
        then=("candidate_score", out),
    )
    for d, p, q, out in FUZZY_RULES_TABLE
]

_EVALUATE_CANDIDATE_STAGE = fuzzy_stage(
    inputs=[DESC_VAR, PRICE_VAR, QTY_VAR],
    output=CANDIDATE_SCORE_VAR,
    rules=CANDIDATE_FUZZY_RULES,
    resolution=400,
)


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class SKUCandidate:
    """Input representation of a candidate SKU for confidence evaluation."""

    sku: str
    description_similarity: float | None = None
    price_closeness: float | None = None
    quantity_plausibility: float | None = None


@dataclass(frozen=True)
class EvaluatedCandidate:
    """Individual candidate evaluation produced by the fuzzy inference stage."""

    sku: str
    candidate_score: float
    candidate_label: str  # 'low' | 'medium' | 'high'
    description_similarity: float | None
    price_closeness: float | None
    quantity_plausibility: float | None
    is_incomplete: bool = False

    @property
    def score(self) -> float:
        """Alias for candidate_score."""
        return self.candidate_score

    @property
    def label(self) -> str:
        """Alias for candidate_label."""
        return self.candidate_label


@dataclass(frozen=True)
class SKUMatchConfidenceResult:
    """Structured decision returned by evaluate_sku_match_confidence."""

    candidate_scores: list[EvaluatedCandidate]
    match_confidence: float
    match_confidence_label: str  # 'low' | 'medium' | 'high'
    best_candidate: EvaluatedCandidate | None
    best_score: float | None
    runner_up_candidate: EvaluatedCandidate | None
    runner_up_score: float | None
    score_margin: float | None
    reason: str  # 'none' | 'ambiguity' | 'insufficient_data' | 'weak_candidate' | 'moderate_fit' | 'no_candidates'
    details: str = ""

    @property
    def label(self) -> str:
        """Alias for match_confidence_label."""
        return self.match_confidence_label


# -----------------------------------------------------------------------------
# Helpers & Validation
# -----------------------------------------------------------------------------

def score_to_label(score: float) -> str:
    """Convert crisp score into linguistic label matching dominant membership crossover."""
    if score < LOW_THRESHOLD:
        return "low"
    elif score < HIGH_THRESHOLD:
        return "medium"
    return "high"


def _validate_feature_value(name: str, value: Any) -> float | None:
    """Validate normalized feature value strictly in [0.0, 1.0], rejecting NaN and Inf."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a float or None, got {type(value).__name__} ({value!r})")
    f_val = float(value)
    if math.isnan(f_val) or math.isinf(f_val):
        raise ValueError(f"{name} must not be NaN or Inf, got {f_val}")
    if f_val < 0.0 or f_val > 1.0:
        raise ValueError(f"{name} must be within [0.0, 1.0], got {f_val}")
    return f_val


def _coerce_candidate_item(item: Any) -> tuple[str, float | None, float | None, float | None]:
    """Extract and validate candidate attributes from dataclass, dict, or object."""
    if isinstance(item, dict):
        sku = item.get("sku")
        d = item.get("description_similarity")
        p = item.get("price_closeness")
        q = item.get("quantity_plausibility")
    elif hasattr(item, "sku"):
        sku = getattr(item, "sku")
        d = getattr(item, "description_similarity", None)
        p = getattr(item, "price_closeness", None)
        q = getattr(item, "quantity_plausibility", None)
    else:
        raise TypeError(
            f"Expected SKUCandidate, dict, or object with 'sku' attribute; got {type(item).__name__}"
        )

    if not isinstance(sku, str) or not sku.strip():
        raise ValueError(f"Candidate SKU must be a non-empty string, got {sku!r}")

    v_d = _validate_feature_value("description_similarity", d)
    v_p = _validate_feature_value("price_closeness", p)
    v_q = _validate_feature_value("quantity_plausibility", q)

    return sku.strip(), v_d, v_p, v_q


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def evaluate_sku_match_confidence(
    candidates: Sequence[Any],
    *,
    ambiguity_threshold: float = AMBIGUITY_MARGIN_THRESHOLD,
) -> SKUMatchConfidenceResult:
    """Evaluate SKU candidates using fuzzy inference and resolve selection ambiguity.

    Args:
        candidates: Sequence of candidate objects (SKUCandidate, dict, or matching object).
        ambiguity_threshold: Minimum margin between best and runner-up candidate scores
            required to avoid ambiguity downgrade (default: 0.15).

    Returns:
        SKUMatchConfidenceResult: Fully structured evaluation containing candidate_scores,
            match_confidence in [0.0, 1.0], match_confidence_label ('low' | 'medium' | 'high'),
            margin analysis, and explicit downgrade reasons.

    Raises:
        TypeError: If candidate features have invalid types.
        ValueError: If features are NaN, Inf, out of [0.0, 1.0], or if duplicate SKUs are passed.
    """
    if ambiguity_threshold <= 0.0 or ambiguity_threshold > 1.0:
        raise ValueError(
            f"ambiguity_threshold must be strictly in (0.0, 1.0], got {ambiguity_threshold}"
        )

    # 1. Parse and validate all candidates
    parsed_items: list[tuple[str, float | None, float | None, float | None]] = []
    seen_skus: set[str] = set()

    for item in candidates:
        sku, d, p, q = _coerce_candidate_item(item)
        if sku in seen_skus:
            raise ValueError(f"Duplicate candidate SKU in input list: {sku!r}")
        seen_skus.add(sku)
        parsed_items.append((sku, d, p, q))

    # Empty candidate list case
    if not parsed_items:
        return SKUMatchConfidenceResult(
            candidate_scores=[],
            match_confidence=0.0,
            match_confidence_label="low",
            best_candidate=None,
            best_score=None,
            runner_up_candidate=None,
            runner_up_score=None,
            score_margin=None,
            reason="no_candidates",
            details="No SKU candidates provided for evaluation.",
        )

    # 2. Evaluate individual candidate scores via fuzzy engine
    evaluated: list[EvaluatedCandidate] = []

    for sku, d, p, q in parsed_items:
        is_incomplete = (d is None or p is None or q is None)

        # Conservative incomplete data policy:
        # Missing features default to 0.0 (worst-case assumption).
        d_eff = 0.0 if d is None else d
        p_eff = 0.0 if p is None else p
        q_eff = 0.0 if q is None else q

        state = State(facts={
            "description_similarity": d_eff,
            "price_closeness": p_eff,
            "quantity_plausibility": q_eff,
        })
        res_state = _EVALUATE_CANDIDATE_STAGE(state)
        score = round(float(res_state.facts["candidate_score"]), 4)

        # Explicit policy: Absence of description_similarity MUST NEVER lead to high or medium.
        # Imputing 0.0 already triggers rule (low, *, *) -> low, and we additionally cap score.
        if d is None:
            score = min(score, LOW_CEILING)

        label = score_to_label(score)
        evaluated.append(
            EvaluatedCandidate(
                sku=sku,
                candidate_score=score,
                candidate_label=label,
                description_similarity=d,
                price_closeness=p,
                quantity_plausibility=q,
                is_incomplete=is_incomplete,
            )
        )

    # 3. Sort candidates descending by score (deterministic tie-breaker by SKU)
    evaluated.sort(key=lambda c: (-c.candidate_score, c.sku))

    # 4. Selection post-processing
    if len(evaluated) == 1:
        best = evaluated[0]
        best_score = best.candidate_score
        match_conf = best_score
        label = score_to_label(match_conf)

        if best.is_incomplete:
            reason = "insufficient_data"
            details = f"Single candidate '{best.sku}' evaluated with incomplete features."
        elif label == "high":
            reason = "none"
            details = f"Single candidate '{best.sku}' has strong feature evidence without competition."
        elif label == "medium":
            reason = "moderate_fit"
            details = f"Single candidate '{best.sku}' has moderate feature evidence."
        else:
            reason = "weak_candidate"
            details = f"Single candidate '{best.sku}' has weak feature evidence."

        return SKUMatchConfidenceResult(
            candidate_scores=evaluated,
            match_confidence=match_conf,
            match_confidence_label=label,
            best_candidate=best,
            best_score=best_score,
            runner_up_candidate=None,
            runner_up_score=None,
            score_margin=None,
            reason=reason,
            details=details,
        )

    # 5. Multiple candidates competition analysis
    best = evaluated[0]
    runner = evaluated[1]
    best_score = best.candidate_score
    runner_score = runner.candidate_score
    score_margin = round(best_score - runner_score, 4)

    if score_margin < ambiguity_threshold:
        # Ambiguity: competing candidates are too close
        ratio = score_margin / ambiguity_threshold
        # Linearly scale within low band [0.0, LOW_CEILING]
        match_conf = round(best_score * ratio * LOW_CEILING, 4)
        label = "low"
        reason = "ambiguity"
        details = (
            f"Ambiguity detected: best candidate '{best.sku}' (score={best_score:.4f}) and "
            f"runner-up '{runner.sku}' (score={runner_score:.4f}) differ by margin "
            f"{score_margin:.4f} < threshold {ambiguity_threshold:.2f}."
        )
    else:
        # Clear separation: runner-up is distinctly weaker
        match_conf = best_score
        label = score_to_label(match_conf)

        if best.is_incomplete:
            reason = "insufficient_data"
            details = f"Leading candidate '{best.sku}' evaluated with incomplete features."
        elif label == "high":
            reason = "none"
            details = (
                f"Leading candidate '{best.sku}' is strong and distinctly separated from "
                f"runner-up '{runner.sku}' (margin={score_margin:.4f} >= {ambiguity_threshold:.2f})."
            )
        elif label == "medium":
            reason = "moderate_fit"
            details = (
                f"Leading candidate '{best.sku}' has moderate evidence (margin={score_margin:.4f})."
            )
        else:
            reason = "weak_candidate"
            details = f"Leading candidate '{best.sku}' has weak evidence (score={best_score:.4f})."

    return SKUMatchConfidenceResult(
        candidate_scores=evaluated,
        match_confidence=match_conf,
        match_confidence_label=label,
        best_candidate=best,
        best_score=best_score,
        runner_up_candidate=runner,
        runner_up_score=runner_score,
        score_margin=score_margin,
        reason=reason,
        details=details,
    )
