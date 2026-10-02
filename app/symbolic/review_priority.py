"""Fuzzy evaluation of purchase order manual review priority.

Computes a calibrated `review_priority` score in [0.0, 1.0] and linguistic label
in {'low', 'medium', 'high'} based on:
1. `discrepancy_count`: number of detected discrepancies (non-negative int).
2. `discrepancy_severity`: severity of discrepancies in [0.0, 1.0] or string
   ('Info', 'Warning', 'Blocking'), combined with `price_deviation_pct`.
3. `price_deviation_pct`: relative percentage deviation (e.g. 20.0 for 20%),
   symmetrical (|dev|), aggregated via max.
4. `order_total_cents`: integer cents, normalized with saturation ceiling.
5. `match_confidence`: [0.0, 1.0] from SKU match confidence evaluation.

Guarantees strict monotonicity with respect to discrepancy count by design:
all else being equal, increasing discrepancy_count CANNOT decrease review_priority
or its linguistic label. This is guaranteed via the monotonic upper envelope:
    final(n, x) = max_{k in {0, ..., min(n, COUNT_CAP)}} base(k, x)
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Callable, Mapping, Sequence

from app.symbolic.engine import State, TraceStep, log
from app.symbolic.fuzzy import (
    FuzzyRule,
    FuzzyVar,
    fuzzy_stage,
    trap,
    tri,
)

# -----------------------------------------------------------------------------
# Constants & Thresholds
# -----------------------------------------------------------------------------

# Dominant membership crossovers in the output universe:
# [0.0, 0.40) -> 'low'
# [0.40, 0.70) -> 'medium'
# [0.70, 1.0] -> 'high'
LOW_THRESHOLD: float = 0.40
HIGH_THRESHOLD: float = 0.70

# Saturation cap for discrepancy count in fuzzy inference
COUNT_CAP: int = 5

# Saturation ceiling for order total: $10,000.00 = 1,000,000 cents
ORDER_TOTAL_SATURATION_CENTS: int = 1_000_000

# Saturation ceiling for relative price deviation: 50.0%
PRICE_DEV_SATURATION_PCT: float = 50.0

# Critical risk thresholds
CRITICAL_PRICE_DEV_THRESHOLD_PCT: float = 30.0
CRITICAL_SEVERITY_THRESHOLD: float = 0.80

# Safety floor values
CRITICAL_RISK_FLOOR: float = 0.75       # Guarantees 'high' label (>= 0.70)
UNCERTAIN_MATCH_FLOOR: float = 0.45     # Guarantees at least 'medium' label (>= 0.40)
INCOMPLETE_DATA_FLOOR: float = 0.45     # Guarantees at least 'medium' label (>= 0.40)

# SKU confidence threshold below which match is considered uncertain
LOW_MATCH_CONFIDENCE_THRESHOLD: float = 0.40

# Severity string mapping
SEVERITY_STRING_MAP: dict[str, float] = {
    "info": 0.20,
    "low": 0.20,
    "warning": 0.50,
    "warn": 0.50,
    "medium": 0.50,
    "blocking": 0.85,
    "error": 0.85,
    "critical": 0.85,
    "high": 0.85,
}

# Label ordinal ranking for monotonicity assertions
LABEL_RANKS: dict[str, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
}


# -----------------------------------------------------------------------------
# Membership Functions & Linguistic Variables
# -----------------------------------------------------------------------------

COUNT_VAR = FuzzyVar(
    name="count",
    lo=0.0,
    hi=5.0,
    sets={
        "zero": trap(0.0, 0.0, 0.2, 0.8),
        "few": tri(0.5, 1.8, 3.2),
        "many": trap(2.5, 3.8, 5.0, 5.0),
    },
)

SEV_VAR = FuzzyVar(
    name="severity",
    lo=0.0,
    hi=1.0,
    sets={
        "low": trap(0.0, 0.0, 0.25, 0.45),
        "medium": tri(0.30, 0.55, 0.80),
        "high": trap(0.65, 0.85, 1.0, 1.0),
    },
)

TOT_VAR = FuzzyVar(
    name="order_total",
    lo=0.0,
    hi=1.0,
    sets={
        "small": trap(0.0, 0.0, 0.25, 0.45),
        "medium": tri(0.30, 0.55, 0.80),
        "large": trap(0.65, 0.85, 1.0, 1.0),
    },
)

UNC_VAR = FuzzyVar(
    name="uncertainty",
    lo=0.0,
    hi=1.0,
    sets={
        "low": trap(0.0, 0.0, 0.25, 0.45),
        "medium": tri(0.30, 0.55, 0.80),
        "high": trap(0.65, 0.85, 1.0, 1.0),
    },
)

PRIO_VAR = FuzzyVar(
    name="review_priority",
    lo=0.0,
    hi=1.0,
    sets={
        "low": trap(0.0, 0.0, 0.25, 0.45),
        "medium": tri(0.35, 0.55, 0.75),
        "high": trap(0.65, 0.85, 1.0, 1.0),
    },
)


# -----------------------------------------------------------------------------
# Complete 81-Rule Fuzzy System
# -----------------------------------------------------------------------------

def _build_rules() -> list[FuzzyRule]:
    c_levels = (("zero", 0), ("few", 1), ("many", 2))
    s_levels = (("low", 0), ("medium", 1), ("high", 2))
    t_levels = (("small", 0), ("medium", 1), ("large", 2))
    u_levels = (("low", 0), ("medium", 1), ("high", 2))

    rules: list[FuzzyRule] = []
    for c_lbl, c_v in c_levels:
        for s_lbl, s_v in s_levels:
            for t_lbl, t_v in t_levels:
                for u_lbl, u_v in u_levels:
                    # Non-decreasing scoring function guaranteed monotonic in all 4 inputs
                    pts = 3 * c_v + 3 * s_v + t_v + u_v
                    out_lbl = "low" if pts <= 3 else ("medium" if pts <= 7 else "high")
                    rule_id = f"REV_{c_lbl[:1].upper()}_{s_lbl[:1].upper()}_{t_lbl[:1].upper()}_{u_lbl[:1].upper()}"
                    rules.append(
                        FuzzyRule(
                            id=rule_id,
                            ifs={
                                "count": c_lbl,
                                "severity": s_lbl,
                                "order_total": t_lbl,
                                "uncertainty": u_lbl,
                            },
                            then=("review_priority", out_lbl),
                        )
                    )
    return rules


REVIEW_FUZZY_RULES: list[FuzzyRule] = _build_rules()

_REVIEW_FUZZY_STAGE = fuzzy_stage(
    inputs=[COUNT_VAR, SEV_VAR, TOT_VAR, UNC_VAR],
    output=PRIO_VAR,
    rules=REVIEW_FUZZY_RULES,
    resolution=200,
)


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class DiscrepancyItem:
    """Individual discrepancy item for review priority assessment."""

    discrepancy_type: str = ""
    severity: float | str | None = None
    price_deviation_pct: float | None = None
    stated_price_cents: int | None = None
    expected_price_cents: int | None = None
    details: str = ""


@dataclass(frozen=True)
class OrderReviewInput:
    """Input parameters for manual review priority evaluation."""

    discrepancy_count: int | None = None
    discrepancy_severity: float | str | None = None
    price_deviation_pct: float | None = None
    order_total_cents: int | None = None
    match_confidence: float | None = None
    discrepancies: Sequence[DiscrepancyItem | dict[str, Any] | Any] | None = None
    order_id: str | None = None


@dataclass(frozen=True)
class ReviewPriorityResult:
    """Decision output produced by evaluate_review_priority."""

    review_priority: float
    review_priority_label: str  # 'low' | 'medium' | 'high'
    raw_fuzzy_score: float
    features_used: dict[str, float]
    applied_rules: list[str]
    has_incomplete_data: bool
    monotonic_adjustment: bool
    details: str = ""

    @property
    def score(self) -> float:
        """Alias for review_priority."""
        return self.review_priority

    @property
    def label(self) -> str:
        """Alias for review_priority_label."""
        return self.review_priority_label


# -----------------------------------------------------------------------------
# Validation & Normalization Helpers
# -----------------------------------------------------------------------------

def score_to_label(score: float) -> str:
    """Convert crisp priority score into linguistic label matching dominant crossovers."""
    if score < LOW_THRESHOLD:
        return "low"
    elif score < HIGH_THRESHOLD:
        return "medium"
    return "high"


def parse_severity(val: Any) -> float | None:
    """Parse severity from float [0, 1] or string into float [0.0, 1.0]."""
    if val is None:
        return None
    if isinstance(val, bool):
        raise TypeError(f"severity must not be a boolean, got {val!r}")
    if isinstance(val, str):
        cleaned = val.strip().lower()
        if cleaned in SEVERITY_STRING_MAP:
            return SEVERITY_STRING_MAP[cleaned]
        raise ValueError(
            f"Unknown discrepancy severity string: {val!r}. "
            f"Expected one of: {list(SEVERITY_STRING_MAP.keys())}"
        )
    if isinstance(val, (int, float)):
        f_val = float(val)
        if math.isnan(f_val) or math.isinf(f_val):
            raise ValueError(f"severity must not be NaN or Inf, got {f_val}")
        if f_val < 0.0 or f_val > 1.0:
            raise ValueError(f"severity must be in [0.0, 1.0], got {f_val}")
        return f_val
    raise TypeError(f"severity must be float, int, str, or None, got {type(val).__name__} ({val!r})")


def calculate_price_deviation_pct(stated_price_cents: int, expected_price_cents: int) -> float:
    """Calculate symmetrical relative percentage price deviation.

    Example: stated=120, expected=100 -> 20.0 (meaning 20%).
    Example: stated=80, expected=100 -> 20.0 (meaning 20%).
    """
    if isinstance(stated_price_cents, bool) or not isinstance(stated_price_cents, int):
        raise TypeError(f"stated_price_cents must be an integer, got {type(stated_price_cents).__name__}")
    if isinstance(expected_price_cents, bool) or not isinstance(expected_price_cents, int):
        raise TypeError(f"expected_price_cents must be an integer, got {type(expected_price_cents).__name__}")
    if stated_price_cents < 0 or expected_price_cents < 0:
        raise ValueError("Price cents must be non-negative")

    if expected_price_cents == 0:
        return 0.0 if stated_price_cents == 0 else 100.0

    return abs(stated_price_cents - expected_price_cents) / expected_price_cents * 100.0


def _validate_price_deviation_pct(val: Any) -> float | None:
    """Validate relative percentage price deviation (e.g. 20.0 = 20%). Symmetrical."""
    if val is None:
        return None
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise TypeError(f"price_deviation_pct must be float, int, or None, got {type(val).__name__}")
    f_val = float(val)
    if math.isnan(f_val) or math.isinf(f_val):
        raise ValueError(f"price_deviation_pct must not be NaN or Inf, got {f_val}")
    return abs(f_val)


def _validate_order_total_cents(val: Any) -> int | None:
    """Validate integer order total cents."""
    if val is None:
        return None
    if isinstance(val, bool) or not isinstance(val, int):
        raise TypeError(f"order_total_cents must be an integer or None, got {type(val).__name__} ({val!r})")
    if val < 0:
        raise ValueError(f"order_total_cents must be non-negative, got {val}")
    return val


def _validate_match_confidence(val: Any) -> float | None:
    """Validate match confidence strictly in [0.0, 1.0]."""
    if val is None:
        return None
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise TypeError(f"match_confidence must be a float or None, got {type(val).__name__} ({val!r})")
    f_val = float(val)
    if math.isnan(f_val) or math.isinf(f_val):
        raise ValueError(f"match_confidence must not be NaN or Inf, got {f_val}")
    if f_val < 0.0 or f_val > 1.0:
        raise ValueError(f"match_confidence must be in [0.0, 1.0], got {f_val}")
    return f_val


def _validate_discrepancy_count(val: Any) -> int:
    """Validate discrepancy count non-negative integer."""
    if isinstance(val, bool) or not isinstance(val, int):
        raise TypeError(f"discrepancy_count must be an integer, got {type(val).__name__} ({val!r})")
    if val < 0:
        raise ValueError(f"discrepancy_count must be non-negative, got {val}")
    return val


def _extract_discrepancy_item(item: Any) -> tuple[float | None, float | None]:
    """Extract (parsed_severity, price_dev_pct) from DiscrepancyItem, dict, or object."""
    if isinstance(item, dict):
        sev = item.get("severity")
        p_dev = item.get("price_deviation_pct")
        stated = item.get("stated_price_cents")
        expected = item.get("expected_price_cents")
    elif hasattr(item, "severity"):
        sev = getattr(item, "severity", None)
        p_dev = getattr(item, "price_deviation_pct", None)
        stated = getattr(item, "stated_price_cents", None)
        expected = getattr(item, "expected_price_cents", None)
    else:
        raise TypeError(
            f"Expected DiscrepancyItem, dict, or object with discrepancy attributes; got {type(item).__name__}"
        )

    parsed_sev = parse_severity(sev)
    if p_dev is not None:
        parsed_p_dev = _validate_price_deviation_pct(p_dev)
    elif stated is not None and expected is not None:
        parsed_p_dev = calculate_price_deviation_pct(stated, expected)
    else:
        parsed_p_dev = None

    return parsed_sev, parsed_p_dev


# -----------------------------------------------------------------------------
# Base Fuzzy Evaluation & Safety Floors
# -----------------------------------------------------------------------------

def _evaluate_base(
    k: int,
    eff_sev: float,
    eff_tot: float,
    eff_unc: float,
    max_price_dev: float,
    match_conf: float | None,
    has_incomplete: bool,
) -> tuple[float, State]:
    """Evaluate base fuzzy inference score and apply safety floors for count k."""
    facts = {
        "count": float(min(k, COUNT_CAP)),
        "severity": eff_sev,
        "order_total": eff_tot,
        "uncertainty": eff_unc,
    }
    state = State(facts=facts)
    res = _REVIEW_FUZZY_STAGE(state)
    raw_crisp = float(res.facts["review_priority"])

    score = raw_crisp

    # Safety Floor 1: Critical risk (severity >= 0.80 or price_deviation >= 30%)
    # Only applies when discrepancies exist (k > 0)
    if k > 0 and (eff_sev >= CRITICAL_SEVERITY_THRESHOLD or max_price_dev >= CRITICAL_PRICE_DEV_THRESHOLD_PCT):
        score = max(score, CRITICAL_RISK_FLOOR)

    # Safety Floor 2: Uncertain or low SKU match confidence
    if match_conf is None or match_conf < LOW_MATCH_CONFIDENCE_THRESHOLD:
        score = max(score, UNCERTAIN_MATCH_FLOOR)

    # Safety Floor 3: Incomplete input data
    if has_incomplete:
        score = max(score, INCOMPLETE_DATA_FLOOR)

    score = min(max(score, 0.0), 1.0)
    return score, res


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def evaluate_review_priority(
    input_data: OrderReviewInput | dict[str, Any] | None = None,
    *,
    discrepancy_count: int | None = None,
    discrepancy_severity: float | str | None = None,
    price_deviation_pct: float | None = None,
    order_total_cents: int | None = None,
    match_confidence: float | None = None,
    discrepancies: Sequence[DiscrepancyItem | dict[str, Any] | Any] | None = None,
) -> ReviewPriorityResult:
    """Evaluate purchase order manual review priority with guaranteed count monotonicity.

    Args:
        input_data: Optional OrderReviewInput or dict containing evaluation parameters.
        discrepancy_count: Explicit non-negative discrepancy count.
        discrepancy_severity: Severity in [0.0, 1.0] or string ('Info', 'Warning', 'Blocking').
        price_deviation_pct: Symmetrical price deviation percentage (e.g. 20.0 = 20%).
        order_total_cents: Non-negative integer order total in cents.
        match_confidence: Match confidence in [0.0, 1.0] from SKU confidence module.
        discrepancies: Sequence of individual DiscrepancyItem objects or dicts.

    Returns:
        ReviewPriorityResult: Structured evaluation with review_priority in [0.0, 1.0],
            review_priority_label ('low' | 'medium' | 'high'), applied rules, and metadata.

    Raises:
        TypeError: If any input has an invalid type (e.g. booleans passed for numeric fields).
        ValueError: If numeric inputs are out of bounds (e.g. negative cents, NaN, Inf).
    """
    # 1. Coerce input_data if provided
    if input_data is not None:
        if isinstance(input_data, dict):
            if discrepancy_count is None:
                discrepancy_count = input_data.get("discrepancy_count")
            if discrepancy_severity is None:
                discrepancy_severity = input_data.get("discrepancy_severity")
            if price_deviation_pct is None:
                price_deviation_pct = input_data.get("price_deviation_pct")
            if order_total_cents is None:
                order_total_cents = input_data.get("order_total_cents")
            if match_confidence is None:
                match_confidence = input_data.get("match_confidence")
            if discrepancies is None:
                discrepancies = input_data.get("discrepancies")
        elif isinstance(input_data, OrderReviewInput):
            if discrepancy_count is None:
                discrepancy_count = input_data.discrepancy_count
            if discrepancy_severity is None:
                discrepancy_severity = input_data.discrepancy_severity
            if price_deviation_pct is None:
                price_deviation_pct = input_data.price_deviation_pct
            if order_total_cents is None:
                order_total_cents = input_data.order_total_cents
            if match_confidence is None:
                match_confidence = input_data.match_confidence
            if discrepancies is None:
                discrepancies = input_data.discrepancies
        else:
            raise TypeError(
                f"input_data must be OrderReviewInput, dict, or None; got {type(input_data).__name__}"
            )

    # 2. Process discrepancies list if provided
    item_severities: list[float] = []
    item_deviations: list[float] = []
    if discrepancies is not None:
        for item in discrepancies:
            s_val, d_val = _extract_discrepancy_item(item)
            if s_val is not None:
                item_severities.append(s_val)
            if d_val is not None:
                item_deviations.append(d_val)

        inferred_count = len(discrepancies)
        if discrepancy_count is not None and discrepancy_count != inferred_count:
            raise ValueError(
                f"discrepancy_count ({discrepancy_count}) does not match "
                f"len(discrepancies) ({inferred_count})"
            )
        discrepancy_count = inferred_count

    # 3. Validate discrepancy_count
    if discrepancy_count is None:
        discrepancy_count = 0
    final_count = _validate_discrepancy_count(discrepancy_count)

    # 4. Parse & aggregate severity and price deviation
    top_sev = parse_severity(discrepancy_severity)
    if top_sev is not None:
        item_severities.append(top_sev)

    top_p_dev = _validate_price_deviation_pct(price_deviation_pct)
    if top_p_dev is not None:
        item_deviations.append(top_p_dev)

    # Monotonic non-decreasing aggregation via max()
    base_sev = max(item_severities, default=0.0) if final_count > 0 else 0.0
    max_p_dev = max(item_deviations, default=0.0) if final_count > 0 else 0.0

    # Effective severity combines base severity and normalized price deviation
    norm_p_dev = min(1.0, max_p_dev / PRICE_DEV_SATURATION_PCT)
    effective_severity = max(base_sev, norm_p_dev)

    # 5. Validate & normalize order total
    v_order_total = _validate_order_total_cents(order_total_cents)
    has_incomplete_data = False
    if v_order_total is None:
        has_incomplete_data = True
        # Conservative assumption when order total is unknown: moderate exposure
        effective_order_total = 0.50
    else:
        effective_order_total = min(1.0, v_order_total / ORDER_TOTAL_SATURATION_CENTS)

    # 6. Validate & normalize match confidence
    v_match_conf = _validate_match_confidence(match_confidence)
    if v_match_conf is None:
        has_incomplete_data = True
        # Conservative assumption: maximum uncertainty when confidence is missing
        effective_uncertainty = 1.0
    else:
        effective_uncertainty = 1.0 - v_match_conf

    # 7. Monotonic Upper Envelope evaluation:
    # final(n, x) = max_{k in {0, ..., min(n, COUNT_CAP)}} base(k, x)
    cap = min(final_count, COUNT_CAP)
    base_scores: list[float] = []
    last_res: State | None = None

    for k in range(cap + 1):
        k_score, k_state = _evaluate_base(
            k=k,
            eff_sev=effective_severity,
            eff_tot=effective_order_total,
            eff_unc=effective_uncertainty,
            max_price_dev=max_p_dev,
            match_conf=v_match_conf,
            has_incomplete=has_incomplete_data,
        )
        base_scores.append(k_score)
        if k == cap:
            last_res = k_state

    assert last_res is not None
    final_score = max(base_scores)
    raw_at_n = base_scores[cap]
    is_adjusted = (final_score > raw_at_n + 1e-9)

    final_label = score_to_label(final_score)

    applied_rules = [
        step.rule_id
        for step in last_res.trace
        if step.stage == "fuzzy"
    ]

    features_used = {
        "discrepancy_count": float(final_count),
        "discrepancy_severity": effective_severity,
        "price_deviation_pct": max_p_dev,
        "order_total_cents": float(v_order_total) if v_order_total is not None else 0.0,
        "normalized_order_total": effective_order_total,
        "match_confidence": v_match_conf if v_match_conf is not None else 0.0,
        "uncertainty": effective_uncertainty,
    }

    details = (
        f"Evaluated review_priority={final_score:.4f} ({final_label}) "
        f"across count={final_count} (capped={cap}). "
        f"Envelope adjustment={is_adjusted}. Incomplete data={has_incomplete_data}."
    )

    return ReviewPriorityResult(
        review_priority=final_score,
        review_priority_label=final_label,
        raw_fuzzy_score=raw_at_n,
        features_used=features_used,
        applied_rules=applied_rules,
        has_incomplete_data=has_incomplete_data,
        monotonic_adjustment=is_adjusted,
        details=details,
    )


def review_priority_stage(
    output_key: str = "review_priority",
) -> Callable[[State], State]:
    """Symbolic pipeline stage to evaluate purchase order review priority."""
    def run(s: State) -> State:
        count = int(s.facts.get("discrepancy_count", 0))
        sev = s.facts.get("discrepancy_severity")
        p_dev = s.facts.get("price_deviation_pct")
        tot = s.facts.get("order_total_cents")
        match_conf = s.facts.get("match_confidence")

        res = evaluate_review_priority(
            discrepancy_count=count,
            discrepancy_severity=sev,
            price_deviation_pct=p_dev,
            order_total_cents=tot,
            match_confidence=match_conf,
        )

        trace_step = TraceStep(
            stage="review_priority",
            rule_id="EVALUATE_REVIEW_PRIORITY",
            detail=f"{output_key}={res.review_priority:.4f}, label={res.review_priority_label}",
            degree=res.review_priority,
        )

        return log(
            s,
            trace_step,
            facts={
                **s.facts,
                output_key: res.review_priority,
                f"{output_key}_label": res.review_priority_label,
                f"{output_key}_raw": res.raw_fuzzy_score,
                f"{output_key}_monotonic_adj": res.monotonic_adjustment,
            },
        )

    return run

