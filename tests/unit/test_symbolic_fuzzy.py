"""Unit tests for app.symbolic.fuzzy: membership functions (trap, tri), FuzzyVar, fuzzy_stage, and monotonicity."""
from __future__ import annotations

import pytest

from app.symbolic.engine import State
from app.symbolic.fuzzy import (
    FuzzyRule,
    FuzzyVar,
    fuzzy_stage,
    trap,
    tri,
)


def test_trap_membership_plateau_and_bounds():
    fn = trap(10.0, 20.0, 30.0, 40.0)

    # Outside bounds
    assert fn(9.9) == 0.0
    assert fn(40.1) == 0.0

    # Plateau
    assert fn(20.0) == 1.0
    assert fn(25.0) == 1.0
    assert fn(30.0) == 1.0

    # Ramps
    assert pytest.approx(fn(15.0), rel=1e-5) == 0.5
    assert pytest.approx(fn(35.0), rel=1e-5) == 0.5


def test_trap_left_and_right_shoulders():
    left_shoulder = trap(10.0, 10.0, 20.0, 30.0)
    assert left_shoulder(5.0) == 0.0
    assert left_shoulder(10.0) == 1.0
    assert left_shoulder(15.0) == 1.0

    right_shoulder = trap(10.0, 20.0, 30.0, 30.0)
    assert right_shoulder(25.0) == 1.0
    assert right_shoulder(30.0) == 1.0
    assert right_shoulder(35.0) == 0.0


def test_tri_membership_function():
    fn = tri(0.0, 5.0, 10.0)

    assert fn(-1.0) == 0.0
    assert fn(11.0) == 0.0
    assert fn(5.0) == 1.0
    assert pytest.approx(fn(2.5), rel=1e-5) == 0.5
    assert pytest.approx(fn(7.5), rel=1e-5) == 0.5


def test_fuzzy_var_and_rule_dataclasses():
    v = FuzzyVar("test_var", 0.0, 10.0, {"low": trap(0, 0, 2, 5)})
    assert v.name == "test_var"
    assert v.lo == 0.0
    assert v.hi == 10.0
    assert "low" in v.sets

    r = FuzzyRule("R1", {"test_var": "low"}, ("output_var", "high"))
    assert r.id == "R1"
    assert r.ifs == {"test_var": "low"}
    assert r.then == ("output_var", "high")


def test_fuzzy_stage_execution_and_trace():
    dti = FuzzyVar("dti", 0, 2, {"low": trap(0, 0, .2, .35), "mid": tri(.2, .35, .5),
                                 "high": trap(.35, .5, 2, 2)})
    lti = FuzzyVar("lti", 0, 10, {"small": trap(0, 0, .5, 1.5), "medium": tri(.5, 1.5, 3),
                                  "large": trap(1.5, 3, 10, 10)})
    risk = FuzzyVar("risk", 0, 100, {"low": trap(0, 0, 20, 45), "medium": tri(25, 50, 75),
                                     "high": trap(55, 80, 100, 100)})

    frules = [
        FuzzyRule("F1", {"dti": "low", "lti": "small"}, ("risk", "low")),
        FuzzyRule("F5", {"dti": "mid", "lti": "medium"}, ("risk", "medium")),
        FuzzyRule("F9", {"dti": "high", "lti": "large"}, ("risk", "high")),
    ]

    stage = fuzzy_stage([dti, lti], risk, frules)
    s = State(facts={"dti": 0.1, "lti": 0.5})
    res = stage(s)

    assert "risk" in res.facts
    assert "risk_label" in res.facts
    assert res.facts["risk_label"] == "low"
    assert res.facts["risk"] >= 0 and res.facts["risk"] <= 100

    # Trace steps
    fuzzy_steps = [t for t in res.trace if t.stage == "fuzzy"]
    defuzz_steps = [t for t in res.trace if t.stage == "defuzz"]

    assert len(fuzzy_steps) >= 1
    assert len(defuzz_steps) == 1
    assert defuzz_steps[0].rule_id == "risk"


def test_fuzzy_stage_clamps_out_of_bounds_inputs():
    x = FuzzyVar("x", 0.0, 10.0, {"val": trap(0, 0, 10, 10)})
    y = FuzzyVar("y", 0.0, 100.0, {"out": trap(0, 0, 100, 100)})
    rules = [FuzzyRule("R1", {"x": "val"}, ("y", "out"))]

    stage = fuzzy_stage([x], y, rules)

    # Input way beyond universe hi=10.0 (e.g. 50.0); clamped to 10.0 and fires mu=1.0
    s = State(facts={"x": 50.0})
    res = stage(s)
    assert res.facts["y_label"] == "out"


def test_fuzzy_stage_stage_order_monotonicity():
    """Monotonicity contract test for fuzzy_stage output classification.

    Contract:
    - Input 'dti' (debt-to-income) is an ordered physical magnitude in [0, 2].
    - As 'dti' increases (representing increasing debt relative to income),
      the classified risk stage order ('low' -> 'medium' -> 'high') must be
      monotonically non-decreasing.
    - Slices are evaluated across representative fixed values of 'lti'
      (loan-to-income) covering small, medium, and large bounds.
    - Testing grid covers 41 discrete values per slice, explicitly capturing
      the universe bounds (0.0, 2.0) and transition thresholds (0.2, 0.35, 0.5).
    """
    dti = FuzzyVar("dti", 0, 2, {"low": trap(0, 0, .2, .35), "mid": tri(.2, .35, .5),
                                 "high": trap(.35, .5, 2, 2)})
    lti = FuzzyVar("lti", 0, 10, {"small": trap(0, 0, .5, 1.5), "medium": tri(.5, 1.5, 3),
                                  "large": trap(1.5, 3, 10, 10)})
    risk = FuzzyVar("risk", 0, 100, {"low": trap(0, 0, 20, 45), "medium": tri(25, 50, 75),
                                     "high": trap(55, 80, 100, 100)})

    frules = [
        FuzzyRule("F1", {"dti": "low", "lti": "small"}, ("risk", "low")),
        FuzzyRule("F2", {"dti": "low", "lti": "medium"}, ("risk", "low")),
        FuzzyRule("F3", {"dti": "low", "lti": "large"}, ("risk", "medium")),
        FuzzyRule("F4", {"dti": "mid", "lti": "small"}, ("risk", "low")),
        FuzzyRule("F5", {"dti": "mid", "lti": "medium"}, ("risk", "medium")),
        FuzzyRule("F6", {"dti": "mid", "lti": "large"}, ("risk", "high")),
        FuzzyRule("F7", {"dti": "high", "lti": "small"}, ("risk", "medium")),
        FuzzyRule("F8", {"dti": "high", "lti": "medium"}, ("risk", "high")),
        FuzzyRule("F9", {"dti": "high", "lti": "large"}, ("risk", "high")),
    ]

    stage = fuzzy_stage([dti, lti], risk, frules)
    stage_ranks = {"low": 0, "medium": 1, "high": 2}

    # Evaluate multiple fixed slices of the secondary dimension (lti)
    test_lti_slices = [0.2, 1.0, 2.0, 5.0]

    for fixed_lti in test_lti_slices:
        prev_rank = 0
        # Sample dti across 41 points from 0.0 to 2.0 (step 0.05)
        for i in range(41):
            cur_dti = round(i * 0.05, 2)
            state = State(facts={"dti": cur_dti, "lti": fixed_lti})
            result = stage(state)
            label = result.facts["risk_label"]
            current_rank = stage_ranks[label]

            # Invariant: stage rank must never regress to a lower stage as dti increases
            assert current_rank >= prev_rank, (
                f"Monotonicity contract violated at lti={fixed_lti}, dti={cur_dti}: "
                f"stage rank regressed from {prev_rank} to {current_rank} ('{label}')"
            )
            prev_rank = current_rank
