"""Unit tests for app.symbolic.engine: State, Rule forward chaining, compose, and validate."""
from __future__ import annotations

import pytest

from app.symbolic.engine import (
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


def test_state_creation_and_immutability():
    s = State(facts={"a": 1, "b": 2})
    assert s.facts == {"a": 1, "b": 2}
    assert s.trace == ()
    assert s.verdict is None
    assert s.halted is False

    with pytest.raises(AttributeError):
        s.halted = True  # frozen dataclass


def test_trace_step_creation():
    step = TraceStep(stage="test_stage", rule_id="R1", detail="matched condition", degree=0.85)
    assert step.stage == "test_stage"
    assert step.rule_id == "R1"
    assert step.detail == "matched condition"
    assert step.degree == 0.85


def test_log_function_immutability_and_updates():
    s1 = State(facts={"x": 10})
    step = TraceStep(stage="s1", rule_id="R1", detail="d1")
    s2 = log(s1, step, facts={"x": 20}, verdict="passed", halted=True)

    assert s1.facts == {"x": 10}
    assert s1.trace == ()
    assert s1.verdict is None
    assert s1.halted is False

    assert s2.facts == {"x": 20}
    assert s2.trace == (step,)
    assert s2.verdict == "passed"
    assert s2.halted is True


def test_compose_runs_stages_in_order():
    stage1 = lambda s: log(s, TraceStep("s1", "1", "step 1"), facts={**s.facts, "a": 1})
    stage2 = lambda s: log(s, TraceStep("s2", "2", "step 2"), facts={**s.facts, "b": 2})

    pipe = compose(stage1, stage2)
    res = pipe(State(facts={}))

    assert res.facts == {"a": 1, "b": 2}
    assert len(res.trace) == 2
    assert res.trace[0].rule_id == "1"
    assert res.trace[1].rule_id == "2"


def test_compose_short_circuits_on_halted():
    stage1 = lambda s: log(s, TraceStep("s1", "H", "halted early"), verdict="rejected", halted=True)
    called_stage2 = False

    def stage2(s: State) -> State:
        nonlocal called_stage2
        called_stage2 = True
        return log(s, facts={**s.facts, "never": True})

    pipe = compose(stage1, stage2)
    res = pipe(State(facts={}))

    assert res.halted is True
    assert res.verdict == "rejected"
    assert "never" not in res.facts
    assert called_stage2 is False


def test_rules_stage_priority_and_single_fire():
    # R1 has higher priority than R2, but both conditions are met.
    # R1 modifies facts, allowing R3 to fire.
    rules = [
        Rule("R2", when=lambda f: f.get("x", 0) > 0, then={"r2_fired": True}, priority=1),
        Rule("R1", when=lambda f: f.get("x", 0) > 0, then={"r1_fired": True, "y": 5}, priority=10),
        Rule("R3", when=lambda f: f.get("y", 0) == 5, then={"r3_fired": True}, priority=5),
    ]

    stage = rules_stage("test_rules", rules)
    res = stage(State(facts={"x": 1}))

    assert res.facts.get("r1_fired") is True
    assert res.facts.get("r2_fired") is True
    assert res.facts.get("r3_fired") is True
    # Order of firing in trace: R1 (pri 10), then R3 (pri 5, unblocked by R1), then R2 (pri 1)
    fired_ids = [t.rule_id for t in res.trace]
    assert fired_ids == ["R1", "R3", "R2"]


def test_rules_stage_terminal_verdict_halts():
    rules = [
        Rule("TERMINAL", when=lambda f: f.get("bad"), verdict="failed", priority=10),
        Rule("NORMAL", when=lambda f: True, then={"normal": True}, priority=1),
    ]

    stage = rules_stage("test_terminal", rules)
    res = stage(State(facts={"bad": True}))

    assert res.verdict == "failed"
    assert res.halted is True
    assert "normal" not in res.facts


def test_validate_passes_within_bounds():
    schema = {"age": (18, 65), "score": (0.0, 100.0)}
    validator = validate(schema)

    s = State(facts={"age": 25, "score": 85.5})
    res = validator(s)

    assert res.halted is False
    assert res.verdict is None


def test_validate_fails_below_or_above_bounds():
    schema = {"age": (18, 65)}
    validator = validate(schema)

    # Below
    res_low = validator(State(facts={"age": 17}))
    assert res_low.halted is True
    assert res_low.verdict == "invalid_input"
    assert "вне [18, 65]" in res_low.trace[0].detail

    # Above
    res_high = validator(State(facts={"age": 66}))
    assert res_high.halted is True
    assert res_high.verdict == "invalid_input"


def test_validate_fails_on_non_numeric():
    schema = {"score": (0, 100)}
    validator = validate(schema)

    res = validator(State(facts={"score": "eighty"}))
    assert res.halted is True
    assert res.verdict == "invalid_input"


def test_default_verdict_applies_only_when_verdict_is_none():
    dv = default_verdict("fallback")

    s1 = State(facts={})
    res1 = dv(s1)
    assert res1.verdict == "fallback"

    s2 = State(facts={}, verdict="already_set")
    res2 = dv(s2)
    assert res2.verdict == "already_set"


def test_run_pipeline_and_to_dict():
    rule = Rule("R1", when=lambda f: True, verdict="ok")
    pipe = compose(rules_stage("stage1", [rule]))

    state = run_pipeline(pipe, {"in": 123})
    assert state.verdict == "ok"

    d = to_dict(state)
    assert d["schema_version"] == SCHEMA_VERSION
    assert d["verdict"] == "ok"
    assert d["facts"] == {"in": 123}
    assert len(d["trace"]) == 1
    assert d["trace"][0]["rule_id"] == "R1"


def test_counterfactual_finds_minimal_change():
    def simple_pipe(s: State) -> State:
        # approve if income >= 50000
        income = s.facts.get("income", 0)
        verdict = "approve" if income >= 50000 else "reject"
        return log(s, verdict=verdict)

    base_facts = {"income": 40000}
    cfs = counterfactual(simple_pipe, base_facts, wanted="approve", keys=["income"], span=0.5, n=20)

    assert len(cfs) > 0
    # Minimal relative change should be positive (increasing income)
    best = cfs[0]
    assert best["feature"] == "income"
    assert best["from"] == 40000
    assert best["to"] >= 50000
    assert best["rel_change"] > 0
