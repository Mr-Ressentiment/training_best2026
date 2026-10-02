"""Compatibility facade re-exporting symbolic engine and fuzzy modules."""
from __future__ import annotations

import json
from typing import Callable

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
from app.symbolic.fuzzy import (
    FuzzyRule,
    FuzzyVar,
    fuzzy_stage,
    trap,
    tri,
)

__all__ = [
    "Facts",
    "FuzzyRule",
    "FuzzyVar",
    "Rule",
    "SCHEMA_VERSION",
    "State",
    "TraceStep",
    "build_credit_pipeline",
    "compose",
    "counterfactual",
    "default_verdict",
    "fuzzy_stage",
    "log",
    "rules_stage",
    "run_pipeline",
    "to_dict",
    "trap",
    "tri",
    "validate",
]


# ================= ДЕМО: кредитный скоринг =================
def build_credit_pipeline() -> Callable[[State], State]:
    hard = [
        Rule("H1", lambda f: f["age"] < 18, verdict="reject", priority=10, why="возраст < 18"),
        Rule("H2", lambda f: f["bankruptcy"], verdict="reject", priority=10, why="есть банкротство"),
        Rule("D1", lambda f: f["income"] > 0 and "dti" not in f,
             lambda f: {"dti": f["debt"] / f["income"],
                        "lti": f["amount"] / (f["income"] * 12)}, priority=5,
             why="вычислены dti и lti"),
    ]
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
    post = [
        Rule("P1", lambda f: f["risk_label"] == "high" and f["amount"] > 500_000,
             verdict="reject", priority=5, why="высокий риск и сумма > 500k"),
        Rule("P2", lambda f: f["risk_label"] == "high",
             verdict="manual_review", priority=3, why="высокий риск"),
        Rule("P3", lambda f: f["risk_label"] == "medium" and f["amount"] > 300_000,
             verdict="manual_review", priority=1, why="средний риск и сумма > 300k"),
    ]
    return compose(
        validate({"age": (0, 120), "income": (0, 1e9), "debt": (0, 1e9), "amount": (0, 1e10)}),
        rules_stage("hard", hard),
        fuzzy_stage([dti, lti], risk, frules),
        rules_stage("post", post),
        default_verdict("approve"),
    )


if __name__ == "__main__":
    pipe = build_credit_pipeline()
    applicant = {"age": 30, "income": 100_000, "debt": 45_000, "amount": 400_000, "bankruptcy": False}
    res = run_pipeline(pipe, applicant)
    print(json.dumps(to_dict(res), ensure_ascii=False, indent=2, default=str))
    if res.verdict != "approve":
        print("Что изменить для одобрения:",
              counterfactual(pipe, applicant, "approve", ["income", "debt", "amount"]))
