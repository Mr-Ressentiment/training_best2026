"""Unit tests for app.symbolic.review_priority: fuzzy review priority evaluation,
monotonicity invariant enforcement, safety floors, and public API.
"""
from __future__ import annotations

import math
import random
import pytest

from app.symbolic.engine import State, run_pipeline
from app.symbolic.review_priority import (
    COUNT_CAP,
    CRITICAL_PRICE_DEV_THRESHOLD_PCT,
    CRITICAL_RISK_FLOOR,
    CRITICAL_SEVERITY_THRESHOLD,
    HIGH_THRESHOLD,
    INCOMPLETE_DATA_FLOOR,
    LABEL_RANKS,
    LOW_THRESHOLD,
    ORDER_TOTAL_SATURATION_CENTS,
    PRICE_DEV_SATURATION_PCT,
    UNCERTAIN_MATCH_FLOOR,
    DiscrepancyItem,
    OrderReviewInput,
    ReviewPriorityResult,
    calculate_price_deviation_pct,
    evaluate_review_priority,
    parse_severity,
    review_priority_stage,
    score_to_label,
)


# -----------------------------------------------------------------------------
# Core Acceptance Criteria & Baseline Scenarios
# -----------------------------------------------------------------------------

def test_clean_order_with_confident_match_yields_low():
    """A clean order with 0 discrepancies and high match confidence evaluates to 'low'."""
    res = evaluate_review_priority(
        discrepancy_count=0,
        order_total_cents=50_000,   # $500.00
        match_confidence=0.95,
    )

    assert res.review_priority < LOW_THRESHOLD
    assert res.review_priority_label == "low"
    assert res.label == "low"
    assert res.score == res.review_priority
    assert not res.has_incomplete_data
    assert not res.monotonic_adjustment
    assert res.features_used["discrepancy_count"] == 0.0
    assert res.features_used["discrepancy_severity"] == 0.0


def test_moderate_order_yields_medium():
    """An order with moderate discrepancies and medium size evaluates to 'medium'."""
    res = evaluate_review_priority(
        discrepancy_count=2,
        discrepancy_severity="Warning",   # 0.50
        price_deviation_pct=15.0,
        order_total_cents=200_000,        # $2,000.00
        match_confidence=0.85,
    )

    assert LOW_THRESHOLD <= res.review_priority < HIGH_THRESHOLD
    assert res.review_priority_label == "medium"
    assert res.label == "medium"


def test_severe_order_yields_high():
    """An order with many severe discrepancies and large total evaluates to 'high'."""
    res = evaluate_review_priority(
        discrepancy_count=5,
        discrepancy_severity="Blocking",  # 0.85
        price_deviation_pct=25.0,
        order_total_cents=1_200_000,      # $12,000.00 (saturates)
        match_confidence=0.80,
    )

    assert res.review_priority >= HIGH_THRESHOLD
    assert res.review_priority_label == "high"
    assert res.label == "high"


# -----------------------------------------------------------------------------
# Safety Floor Tests
# -----------------------------------------------------------------------------

def test_critical_price_deviation_floor_triggers_high_when_discrepancy_exists():
    """A single discrepancy with price deviation >= 30% floors review_priority at >= 0.75."""
    res = evaluate_review_priority(
        discrepancy_count=1,
        price_deviation_pct=35.0,
        order_total_cents=10_000,
        match_confidence=0.99,
    )

    assert res.review_priority >= CRITICAL_RISK_FLOOR
    assert res.review_priority_label == "high"


def test_critical_severity_floor_triggers_high_when_discrepancy_exists():
    """A single discrepancy with Blocking severity (>= 0.80) floors review_priority at >= 0.75."""
    res = evaluate_review_priority(
        discrepancy_count=1,
        discrepancy_severity="Blocking",
        order_total_cents=10_000,
        match_confidence=0.99,
    )

    assert res.review_priority >= CRITICAL_RISK_FLOOR
    assert res.review_priority_label == "high"


def test_critical_risk_floor_does_not_apply_to_clean_order():
    """A clean order with 0 discrepancies cannot trigger the critical discrepancy floor."""
    res = evaluate_review_priority(
        discrepancy_count=0,
        price_deviation_pct=40.0,   # Residual or hypothetical
        order_total_cents=50_000,
        match_confidence=0.95,
    )

    assert res.review_priority < LOW_THRESHOLD
    assert res.review_priority_label == "low"


def test_uncertain_sku_match_floors_at_medium():
    """An order with low/ambiguous SKU match confidence (< 0.40) is floored at >= 0.45 ('medium')."""
    res = evaluate_review_priority(
        discrepancy_count=0,
        order_total_cents=50_000,
        match_confidence=0.25,      # Weak/ambiguous SKU match
    )

    assert res.review_priority >= UNCERTAIN_MATCH_FLOOR
    assert res.review_priority_label in ("medium", "high")


def test_missing_data_floors_at_medium_and_sets_flag():
    """Missing match confidence or order total triggers incomplete data floor and flag."""
    res = evaluate_review_priority(
        discrepancy_count=0,
        # order_total_cents and match_confidence omitted
    )

    assert res.has_incomplete_data
    assert res.review_priority >= INCOMPLETE_DATA_FLOOR
    assert res.review_priority_label in ("medium", "high")


# -----------------------------------------------------------------------------
# Monotonicity Invariant Tests (Core Requirement)
# -----------------------------------------------------------------------------

@pytest.mark.parametrize("profile_name, profile_kwargs", [
    (
        "minor_discrepancies_small_order",
        {"discrepancy_severity": 0.15, "price_deviation_pct": 2.0, "order_total_cents": 20_000, "match_confidence": 0.95},
    ),
    (
        "medium_discrepancies_medium_order",
        {"discrepancy_severity": 0.50, "price_deviation_pct": 12.0, "order_total_cents": 300_000, "match_confidence": 0.80},
    ),
    (
        "severe_discrepancies_large_order",
        {"discrepancy_severity": 0.85, "price_deviation_pct": 35.0, "order_total_cents": 1_000_000, "match_confidence": 0.70},
    ),
    (
        "uncertain_match_small_order",
        {"discrepancy_severity": 0.30, "price_deviation_pct": 5.0, "order_total_cents": 10_000, "match_confidence": 0.20},
    ),
])
def test_monotonicity_by_discrepancy_count_sweep(profile_name, profile_kwargs):
    """Assert review_priority(n+1) >= review_priority(n) and rank(label(n+1)) >= rank(label(n))."""
    counts = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 20, 50, 100]
    results = [
        evaluate_review_priority(discrepancy_count=n, **profile_kwargs)
        for n in counts
    ]

    for i in range(len(results) - 1):
        prev_res = results[i]
        next_res = results[i + 1]
        n_prev = counts[i]
        n_next = counts[i + 1]

        # Numeric priority must be strictly non-decreasing
        assert next_res.review_priority >= prev_res.review_priority - 1e-12, (
            f"Monotonicity violation in profile '{profile_name}' between count={n_prev} ({prev_res.review_priority}) "
            f"and count={n_next} ({next_res.review_priority})"
        )

        # Linguistic label rank must be non-decreasing
        prev_rank = LABEL_RANKS[prev_res.review_priority_label]
        next_rank = LABEL_RANKS[next_res.review_priority_label]
        assert next_rank >= prev_rank, (
            f"Label rank violation in profile '{profile_name}' between count={n_prev} ({prev_res.review_priority_label}) "
            f"and count={n_next} ({next_res.review_priority_label})"
        )


def test_property_based_randomized_monotonicity():
    """Verify monotonicity across 100 randomized profiles for n in range(0, 11)."""
    rng = random.Random(20261002)

    for trial in range(100):
        sev = rng.choice([None, rng.uniform(0.0, 1.0), "Info", "Warning", "Blocking"])
        p_dev = rng.choice([None, rng.uniform(0.0, 60.0), -rng.uniform(0.0, 60.0)])
        tot = rng.choice([None, rng.randint(0, 2_000_000)])
        match_conf = rng.choice([None, rng.uniform(0.0, 1.0)])

        prev_score = -1.0
        prev_rank = -1

        for n in range(11):
            res = evaluate_review_priority(
                discrepancy_count=n,
                discrepancy_severity=sev,
                price_deviation_pct=p_dev,
                order_total_cents=tot,
                match_confidence=match_conf,
            )

            score = res.review_priority
            rank = LABEL_RANKS[res.review_priority_label]

            if n > 0:
                assert score >= prev_score - 1e-12, (
                    f"Trial {trial}: Score dropped at n={n}: {prev_score:.6f} -> {score:.6f} "
                    f"with inputs (sev={sev}, p_dev={p_dev}, tot={tot}, match_conf={match_conf})"
                )
                assert rank >= prev_rank, (
                    f"Trial {trial}: Label dropped at n={n}: rank {prev_rank} -> {rank} "
                    f"with inputs (sev={sev}, p_dev={p_dev}, tot={tot}, match_conf={match_conf})"
                )

            prev_score = score
            prev_rank = rank


# -----------------------------------------------------------------------------
# Discrepancy List & Aggregation Tests
# -----------------------------------------------------------------------------

def test_adding_minor_discrepancy_never_decreases_priority():
    """Adding an additional minor discrepancy to an order cannot lower its review priority."""
    d_severe = DiscrepancyItem(
        discrepancy_type="PriceMismatch",
        severity="Blocking",
        price_deviation_pct=35.0,
    )
    d_minor1 = DiscrepancyItem(
        discrepancy_type="QuantityOrPackagingBreach",
        severity="Info",
        price_deviation_pct=1.0,
    )
    d_minor2 = DiscrepancyItem(
        discrepancy_type="ArithmeticMismatch",
        severity=0.1,
        price_deviation_pct=0.5,
    )

    r1 = evaluate_review_priority(
        discrepancies=[d_severe],
        order_total_cents=100_000,
        match_confidence=0.90,
    )
    r2 = evaluate_review_priority(
        discrepancies=[d_severe, d_minor1],
        order_total_cents=100_000,
        match_confidence=0.90,
    )
    r3 = evaluate_review_priority(
        discrepancies=[d_severe, d_minor1, d_minor2],
        order_total_cents=100_000,
        match_confidence=0.90,
    )

    assert r2.review_priority >= r1.review_priority - 1e-12
    assert LABEL_RANKS[r2.review_priority_label] >= LABEL_RANKS[r1.review_priority_label]

    assert r3.review_priority >= r2.review_priority - 1e-12
    assert LABEL_RANKS[r3.review_priority_label] >= LABEL_RANKS[r2.review_priority_label]


def test_discrepancy_items_with_stated_and_expected_prices():
    """Discrepancy items can supply stated_price_cents and expected_price_cents."""
    item = DiscrepancyItem(
        discrepancy_type="PriceMismatch",
        severity="Warning",
        stated_price_cents=1200,    # $12.00
        expected_price_cents=1000,  # $10.00 -> 20.0% deviation
    )

    res = evaluate_review_priority(
        discrepancies=[item],
        order_total_cents=50_000,
        match_confidence=0.90,
    )

    assert res.features_used["discrepancy_count"] == 1.0
    assert abs(res.features_used["price_deviation_pct"] - 20.0) < 1e-6


def test_mismatched_discrepancy_count_and_list_raises_value_error():
    """Specifying discrepancy_count that contradicts len(discrepancies) raises ValueError."""
    d = DiscrepancyItem(discrepancy_type="PriceMismatch", severity=0.5)
    with pytest.raises(ValueError, match="does not match len"):
        evaluate_review_priority(
            discrepancy_count=2,
            discrepancies=[d],
        )


# -----------------------------------------------------------------------------
# Price Deviation Calculations & Symmetrical Behavior
# -----------------------------------------------------------------------------

def test_price_deviation_is_symmetrical():
    """Positive and negative price deviations of the same magnitude yield identical priority."""
    r_pos = evaluate_review_priority(
        discrepancy_count=2,
        price_deviation_pct=20.0,
        order_total_cents=100_000,
        match_confidence=0.85,
    )
    r_neg = evaluate_review_priority(
        discrepancy_count=2,
        price_deviation_pct=-20.0,
        order_total_cents=100_000,
        match_confidence=0.85,
    )

    assert r_pos.review_priority == pytest.approx(r_neg.review_priority)
    assert r_pos.review_priority_label == r_neg.review_priority_label


def test_calculate_price_deviation_pct_helper():
    """Verify relative percentage calculation and zero handling."""
    # Stated > Expected (+20%)
    assert calculate_price_deviation_pct(120, 100) == pytest.approx(20.0)
    # Stated < Expected (-20% magnitude)
    assert calculate_price_deviation_pct(80, 100) == pytest.approx(20.0)
    # Both zero
    assert calculate_price_deviation_pct(0, 0) == 0.0
    # Expected zero, stated non-zero
    assert calculate_price_deviation_pct(50, 0) == 100.0


# -----------------------------------------------------------------------------
# Input Validation & Strict Error Handling
# -----------------------------------------------------------------------------

@pytest.mark.parametrize("invalid_count", [-1, -100])
def test_negative_discrepancy_count_raises_value_error(invalid_count):
    with pytest.raises(ValueError, match="non-negative"):
        evaluate_review_priority(discrepancy_count=invalid_count)


@pytest.mark.parametrize("invalid_count_type", [True, False, 1.5, "2", [1]])
def test_invalid_discrepancy_count_type_raises_type_error(invalid_count_type):
    with pytest.raises(TypeError, match="discrepancy_count must be an integer"):
        evaluate_review_priority(discrepancy_count=invalid_count_type)


@pytest.mark.parametrize("invalid_cents", [-1, -500])
def test_negative_order_total_cents_raises_value_error(invalid_cents):
    with pytest.raises(ValueError, match="non-negative"):
        evaluate_review_priority(order_total_cents=invalid_cents)


@pytest.mark.parametrize("invalid_cents_type", [True, False, 100.5, "1000"])
def test_invalid_order_total_cents_type_raises_type_error(invalid_cents_type):
    with pytest.raises(TypeError, match="order_total_cents must be an integer"):
        evaluate_review_priority(order_total_cents=invalid_cents_type)


@pytest.mark.parametrize("invalid_conf", [-0.1, 1.1, float("nan"), float("inf"), float("-inf")])
def test_out_of_bounds_match_confidence_raises_value_error(invalid_conf):
    with pytest.raises(ValueError):
        evaluate_review_priority(match_confidence=invalid_conf)


@pytest.mark.parametrize("invalid_conf_type", [True, False, "0.5"])
def test_invalid_match_confidence_type_raises_type_error(invalid_conf_type):
    with pytest.raises(TypeError, match="match_confidence must be a float"):
        evaluate_review_priority(match_confidence=invalid_conf_type)


@pytest.mark.parametrize("invalid_sev", [-0.1, 1.1, float("nan"), float("inf")])
def test_out_of_bounds_severity_raises_value_error(invalid_sev):
    with pytest.raises(ValueError):
        parse_severity(invalid_sev)


def test_unknown_severity_string_raises_value_error():
    with pytest.raises(ValueError, match="Unknown discrepancy severity string"):
        parse_severity("Catastrophic")


@pytest.mark.parametrize("invalid_sev_type", [True, False, [0.5]])
def test_invalid_severity_type_raises_type_error(invalid_sev_type):
    with pytest.raises(TypeError):
        parse_severity(invalid_sev_type)


# -----------------------------------------------------------------------------
# Input DTO & Calling Styles
# -----------------------------------------------------------------------------

def test_evaluate_with_order_review_input_dataclass():
    """Can pass parameters wrapped in OrderReviewInput dataclass."""
    inp = OrderReviewInput(
        discrepancy_count=2,
        discrepancy_severity="Warning",
        price_deviation_pct=10.0,
        order_total_cents=150_000,
        match_confidence=0.85,
    )
    res = evaluate_review_priority(inp)

    assert res.review_priority_label == "medium"
    assert res.features_used["discrepancy_count"] == 2.0


def test_evaluate_with_dict_input():
    """Can pass parameters wrapped in a dictionary."""
    d = {
        "discrepancy_count": 0,
        "order_total_cents": 50_000,
        "match_confidence": 0.95,
    }
    res = evaluate_review_priority(d)

    assert res.review_priority_label == "low"


# -----------------------------------------------------------------------------
# Symbolic Pipeline Stage Integration
# -----------------------------------------------------------------------------

def test_review_priority_stage_in_symbolic_pipeline():
    """review_priority_stage integrates seamlessly with State and run_pipeline."""
    stage = review_priority_stage(output_key="review_priority")

    input_facts = {
        "discrepancy_count": 3,
        "discrepancy_severity": "Warning",
        "price_deviation_pct": 15.0,
        "order_total_cents": 250_000,
        "match_confidence": 0.85,
    }

    final_state = run_pipeline(stage, input_facts)

    assert "review_priority" in final_state.facts
    assert "review_priority_label" in final_state.facts
    assert final_state.facts["review_priority_label"] in ("medium", "high")

    # Trace step was recorded
    assert any(
        step.stage == "review_priority" and step.rule_id == "EVALUATE_REVIEW_PRIORITY"
        for step in final_state.trace
    )


# -----------------------------------------------------------------------------
# Result Immutability & Helpers
# -----------------------------------------------------------------------------

def test_result_immutability():
    """ReviewPriorityResult is a frozen dataclass."""
    res = evaluate_review_priority(discrepancy_count=0)
    with pytest.raises(Exception):
        res.review_priority = 0.99  # type: ignore[misc]


def test_score_to_label_boundaries():
    """Dominant membership crossovers: <0.40 -> low, 0.40..0.70 -> medium, >=0.70 -> high."""
    assert score_to_label(0.0) == "low"
    assert score_to_label(0.3999) == "low"
    assert score_to_label(0.40) == "medium"
    assert score_to_label(0.6999) == "medium"
    assert score_to_label(0.70) == "high"
    assert score_to_label(1.0) == "high"
