"""Unit tests for app.symbolic.sku_confidence: fuzzy candidate scoring, ambiguity resolution, and public API."""
from __future__ import annotations

import math
import pytest

from app.symbolic.sku_confidence import (
    AMBIGUITY_MARGIN_THRESHOLD,
    HIGH_THRESHOLD,
    LOW_CEILING,
    LOW_THRESHOLD,
    EvaluatedCandidate,
    SKUCandidate,
    SKUMatchConfidenceResult,
    evaluate_sku_match_confidence,
    score_to_label,
)


# -----------------------------------------------------------------------------
# Core Acceptance Criteria Tests
# -----------------------------------------------------------------------------

def test_scenario_a_single_strong_candidate_yields_high():
    """Scenario A: One clearly matching candidate receives 'high' confidence.

    description_similarity=0.98, price_closeness=0.97, quantity_plausibility=0.95 -> high.
    """
    candidates = [
        SKUCandidate("SKU-WRAP-18", 0.98, 0.97, 0.95),
    ]
    res = evaluate_sku_match_confidence(candidates)

    assert len(res.candidate_scores) == 1
    best = res.best_candidate
    assert best is not None
    assert best.sku == "SKU-WRAP-18"
    assert best.candidate_label == "high"
    assert best.candidate_score >= HIGH_THRESHOLD

    assert res.runner_up_candidate is None
    assert res.runner_up_score is None
    assert res.score_margin is None

    assert res.match_confidence == best.candidate_score
    assert res.match_confidence >= HIGH_THRESHOLD
    assert res.match_confidence_label == "high"
    assert res.label == "high"
    assert res.reason == "none"


def test_scenario_b_two_close_strong_candidates_yield_low_via_ambiguity():
    """Scenario B: Two almost equally matching strong candidates receive 'low' due to ambiguity.

    Candidate 1: 0.98 / 0.97 / 0.95
    Candidate 2: 0.97 / 0.96 / 0.95
    Both individual scores must be genuinely high.
    Selection match_confidence must be 'low' with reason='ambiguity'.
    """
    c1 = SKUCandidate("SKU-WRAP-18", 0.98, 0.97, 0.95)
    c2 = SKUCandidate("SKU-WRAP-15", 0.97, 0.96, 0.95)

    res = evaluate_sku_match_confidence([c1, c2])

    assert len(res.candidate_scores) == 2
    # Individual candidate evaluations MUST be genuinely high
    for cand in res.candidate_scores:
        assert cand.candidate_label == "high", f"Candidate {cand.sku} score {cand.candidate_score} was not high"
        assert cand.candidate_score >= HIGH_THRESHOLD

    # Ambiguity check
    assert res.best_candidate is not None
    assert res.runner_up_candidate is not None
    assert res.best_score is not None
    assert res.runner_up_score is not None
    assert res.score_margin is not None

    # Score margin is below threshold (here exactly 0.0)
    assert res.score_margin < AMBIGUITY_MARGIN_THRESHOLD
    assert res.score_margin == 0.0

    # Overall match confidence must be downgraded to 'low'
    assert res.match_confidence < LOW_THRESHOLD
    assert res.match_confidence_label == "low"
    assert res.reason == "ambiguity"
    assert "Ambiguity detected" in res.details


# -----------------------------------------------------------------------------
# Competition & Separation Scenarios
# -----------------------------------------------------------------------------

def test_strong_candidate_with_weak_competitor_yields_high():
    """A strong candidate with a clearly weaker competitor retains 'high' confidence."""
    candidates = [
        SKUCandidate("SKU-WRAP-18", 0.98, 0.97, 0.95),
        SKUCandidate("SKU-TAPE-03", 0.20, 0.20, 0.20),
    ]
    res = evaluate_sku_match_confidence(candidates)

    assert res.best_candidate is not None
    assert res.best_candidate.sku == "SKU-WRAP-18"
    assert res.best_score >= HIGH_THRESHOLD

    assert res.runner_up_candidate is not None
    assert res.runner_up_candidate.sku == "SKU-TAPE-03"
    assert res.runner_up_score < LOW_THRESHOLD

    assert res.score_margin is not None
    assert res.score_margin >= AMBIGUITY_MARGIN_THRESHOLD

    assert res.match_confidence == res.best_score
    assert res.match_confidence_label == "high"
    assert res.reason == "none"


def test_single_weak_candidate_yields_low():
    """A single candidate with poor features receives 'low' with reason='weak_candidate'."""
    candidates = [
        SKUCandidate("SKU-POOR-01", 0.15, 0.20, 0.25),
    ]
    res = evaluate_sku_match_confidence(candidates)

    assert res.best_candidate is not None
    assert res.best_score < LOW_THRESHOLD
    assert res.match_confidence < LOW_THRESHOLD
    assert res.match_confidence_label == "low"
    assert res.reason == "weak_candidate"


def test_two_candidates_with_identical_features_yield_low_ambiguity():
    """Two distinct SKUs with identical features result in margin 0.0 and 'low' confidence."""
    candidates = [
        SKUCandidate("SKU-A", 0.85, 0.85, 0.85),
        SKUCandidate("SKU-B", 0.85, 0.85, 0.85),
    ]
    res = evaluate_sku_match_confidence(candidates)

    assert res.score_margin == 0.0
    assert res.match_confidence == 0.0
    assert res.match_confidence_label == "low"
    assert res.reason == "ambiguity"


def test_candidate_order_permutation_invariance():
    """Input candidate order must not alter the final match confidence or ranking."""
    c1 = SKUCandidate("SKU-STRONG", 0.98, 0.97, 0.95)
    c2 = SKUCandidate("SKU-WEAK", 0.20, 0.20, 0.20)

    res_forward = evaluate_sku_match_confidence([c1, c2])
    res_reversed = evaluate_sku_match_confidence([c2, c1])

    assert res_forward.best_candidate.sku == res_reversed.best_candidate.sku
    assert res_forward.best_score == res_reversed.best_score
    assert res_forward.runner_up_candidate.sku == res_reversed.runner_up_candidate.sku
    assert res_forward.runner_up_score == res_reversed.runner_up_score
    assert res_forward.score_margin == res_reversed.score_margin
    assert res_forward.match_confidence == res_reversed.match_confidence
    assert res_forward.match_confidence_label == res_reversed.match_confidence_label
    assert res_forward.reason == res_reversed.reason


def test_empty_candidates_list_yields_zero_low():
    """Empty candidate sequence returns 0.0 confidence, label='low', reason='no_candidates'."""
    res = evaluate_sku_match_confidence([])

    assert res.candidate_scores == []
    assert res.match_confidence == 0.0
    assert res.match_confidence_label == "low"
    assert res.best_candidate is None
    assert res.best_score is None
    assert res.runner_up_candidate is None
    assert res.runner_up_score is None
    assert res.score_margin is None
    assert res.reason == "no_candidates"


def test_intermediate_candidate_yields_medium():
    """A single candidate with moderate features receives 'medium' confidence."""
    candidates = [
        SKUCandidate("SKU-MED-01", 0.55, 0.55, 0.55),
    ]
    res = evaluate_sku_match_confidence(candidates)

    assert LOW_THRESHOLD <= res.match_confidence < HIGH_THRESHOLD
    assert res.match_confidence_label == "medium"
    assert res.reason == "moderate_fit"


# -----------------------------------------------------------------------------
# Extreme Boundaries & Crossover Tests
# -----------------------------------------------------------------------------

def test_extreme_boundaries_zero_and_one():
    """Evaluate candidates at exact universe boundaries 0.0 and 1.0."""
    res_zero = evaluate_sku_match_confidence([SKUCandidate("SKU-ZERO", 0.0, 0.0, 0.0)])
    assert res_zero.best_score < LOW_THRESHOLD
    assert res_zero.match_confidence_label == "low"

    res_one = evaluate_sku_match_confidence([SKUCandidate("SKU-ONE", 1.0, 1.0, 1.0)])
    assert res_one.best_score >= HIGH_THRESHOLD
    assert res_one.match_confidence_label == "high"


def test_ambiguity_threshold_boundary_behavior():
    """Verify behavior right around the ambiguity threshold boundary."""
    # Custom threshold 0.15
    thresh = 0.15

    # Case 1: margin is strictly below threshold -> ambiguity downgrade
    c_best = SKUCandidate("SKU-A", 0.98, 0.97, 0.95)   # score ~0.8690
    # Candidate with score ~0.7555 gives margin ~0.1135 < 0.15
    c_close = SKUCandidate("SKU-B", 0.75, 0.75, 0.75)  # score ~0.7555

    res_below = evaluate_sku_match_confidence([c_best, c_close], ambiguity_threshold=thresh)
    assert res_below.score_margin < thresh
    assert res_below.match_confidence < LOW_THRESHOLD
    assert res_below.match_confidence_label == "low"
    assert res_below.reason == "ambiguity"

    # Case 2: margin is above threshold -> clear winner
    c_far = SKUCandidate("SKU-C", 0.55, 0.55, 0.55)    # score ~0.5500, margin ~0.3190 >= 0.15
    res_above = evaluate_sku_match_confidence([c_best, c_far], ambiguity_threshold=thresh)
    assert res_above.score_margin >= thresh
    assert res_above.match_confidence >= HIGH_THRESHOLD
    assert res_above.match_confidence_label == "high"
    assert res_above.reason == "none"


def test_score_to_label_crossover_boundaries():
    """Verify that score_to_label matches the exact crossover constants."""
    assert score_to_label(0.0) == "low"
    assert score_to_label(0.3999) == "low"
    assert score_to_label(0.4000) == "medium"
    assert score_to_label(0.5500) == "medium"
    assert score_to_label(0.6999) == "medium"
    assert score_to_label(0.7000) == "high"
    assert score_to_label(1.0) == "high"


# -----------------------------------------------------------------------------
# Incomplete Data Policy Tests
# -----------------------------------------------------------------------------

def test_missing_description_similarity_never_yields_high():
    """Missing description_similarity is conservatively capped and never reaches high."""
    candidate = SKUCandidate(
        sku="SKU-NO-DESC",
        description_similarity=None,
        price_closeness=1.0,
        quantity_plausibility=1.0,
    )
    res = evaluate_sku_match_confidence([candidate])

    assert res.best_score < LOW_THRESHOLD
    assert res.match_confidence < LOW_THRESHOLD
    assert res.match_confidence_label == "low"
    assert res.reason == "insufficient_data"


def test_missing_price_or_quantity_is_penalized_conservatively():
    """Missing price or quantity features do not artificially inflate confidence."""
    # When price is missing, imputed as 0.0, score drops significantly compared to 1.0
    c_full = SKUCandidate("SKU-FULL", 0.98, 0.97, 0.95)
    c_no_price = SKUCandidate("SKU-NO-PRICE", 0.98, None, 0.95)

    res_full = evaluate_sku_match_confidence([c_full])
    res_no_price = evaluate_sku_match_confidence([c_no_price])

    assert res_no_price.best_score < res_full.best_score
    assert res_no_price.reason == "insufficient_data"


# -----------------------------------------------------------------------------
# Input Validation & Error Handling Tests
# -----------------------------------------------------------------------------

def test_invalid_feature_values_raise_value_error():
    """NaN, Inf, and numbers outside [0.0, 1.0] must raise ValueError."""
    with pytest.raises(ValueError, match="NaN or Inf"):
        evaluate_sku_match_confidence([SKUCandidate("SKU-1", float("nan"), 0.5, 0.5)])

    with pytest.raises(ValueError, match="NaN or Inf"):
        evaluate_sku_match_confidence([SKUCandidate("SKU-1", 0.5, float("inf"), 0.5)])

    with pytest.raises(ValueError, match=r"within \[0\.0, 1\.0\]"):
        evaluate_sku_match_confidence([SKUCandidate("SKU-1", -0.01, 0.5, 0.5)])

    with pytest.raises(ValueError, match=r"within \[0\.0, 1\.0\]"):
        evaluate_sku_match_confidence([SKUCandidate("SKU-1", 0.5, 1.05, 0.5)])


def test_invalid_types_raise_type_error():
    """Booleans, strings, and non-numeric values must raise TypeError."""
    with pytest.raises(TypeError, match="must be a float"):
        evaluate_sku_match_confidence([SKUCandidate("SKU-1", True, 0.5, 0.5)])

    with pytest.raises(TypeError, match="must be a float"):
        evaluate_sku_match_confidence([SKUCandidate("SKU-1", "0.95", 0.5, 0.5)])

    with pytest.raises(TypeError, match="Expected"):
        evaluate_sku_match_confidence(["not_a_candidate"])


def test_invalid_sku_or_duplicates_raise_value_error():
    """Empty SKUs and duplicate candidate SKUs in input sequence raise ValueError."""
    with pytest.raises(ValueError, match="non-empty string"):
        evaluate_sku_match_confidence([SKUCandidate("", 0.5, 0.5, 0.5)])

    with pytest.raises(ValueError, match="Duplicate candidate SKU"):
        evaluate_sku_match_confidence([
            SKUCandidate("SKU-1", 0.8, 0.8, 0.8),
            SKUCandidate("SKU-1", 0.7, 0.7, 0.7),
        ])


def test_invalid_ambiguity_threshold_raises_value_error():
    """Threshold must be within (0.0, 1.0]."""
    with pytest.raises(ValueError, match="ambiguity_threshold"):
        evaluate_sku_match_confidence([SKUCandidate("SKU-1", 0.5, 0.5, 0.5)], ambiguity_threshold=0.0)

    with pytest.raises(ValueError, match="ambiguity_threshold"):
        evaluate_sku_match_confidence([SKUCandidate("SKU-1", 0.5, 0.5, 0.5)], ambiguity_threshold=1.5)


# -----------------------------------------------------------------------------
# Dict Input & Convenience API Tests
# -----------------------------------------------------------------------------

def test_dictionary_input_payload_compatibility():
    """Candidates passed as dictionaries evaluate identically to dataclass instances."""
    dict_candidates = [
        {"sku": "SKU-WRAP-18", "description_similarity": 0.98, "price_closeness": 0.97, "quantity_plausibility": 0.95},
        {"sku": "SKU-TAPE-03", "description_similarity": 0.20, "price_closeness": 0.20, "quantity_plausibility": 0.20},
    ]
    res = evaluate_sku_match_confidence(dict_candidates)

    assert res.best_candidate.sku == "SKU-WRAP-18"
    assert res.match_confidence_label == "high"
    assert res.reason == "none"


# -----------------------------------------------------------------------------
# Monotonicity & Determinism Tests
# -----------------------------------------------------------------------------

def test_evaluation_is_strictly_deterministic():
    """Repeated calls with identical inputs produce identical bit-for-bit results."""
    candidates = [
        SKUCandidate("SKU-1", 0.92, 0.88, 0.90),
        SKUCandidate("SKU-2", 0.75, 0.70, 0.80),
    ]
    res1 = evaluate_sku_match_confidence(candidates)
    res2 = evaluate_sku_match_confidence(candidates)

    assert res1.match_confidence == res2.match_confidence
    assert res1.best_score == res2.best_score
    assert res1.runner_up_score == res2.runner_up_score
    assert res1.score_margin == res2.score_margin
    assert res1.match_confidence_label == res2.match_confidence_label
    assert res1.reason == res2.reason


def test_fuzzy_candidate_evaluation_monotonicity():
    """Increasing any input feature with others held constant must not decrease score."""
    grid = [0.1, 0.3, 0.5, 0.7, 0.9]

    # Monotonicity in description_similarity
    scores_desc = [
        evaluate_sku_match_confidence([SKUCandidate("S", d, 0.6, 0.6)]).best_score
        for d in grid
    ]
    for i in range(len(scores_desc) - 1):
        assert scores_desc[i] <= scores_desc[i + 1] + 1e-6

    # Monotonicity in price_closeness
    scores_price = [
        evaluate_sku_match_confidence([SKUCandidate("S", 0.8, p, 0.6)]).best_score
        for p in grid
    ]
    for i in range(len(scores_price) - 1):
        assert scores_price[i] <= scores_price[i + 1] + 1e-6

    # Monotonicity in quantity_plausibility
    scores_qty = [
        evaluate_sku_match_confidence([SKUCandidate("S", 0.8, 0.6, q)]).best_score
        for q in grid
    ]
    for i in range(len(scores_qty) - 1):
        assert scores_qty[i] <= scores_qty[i + 1] + 1e-6
