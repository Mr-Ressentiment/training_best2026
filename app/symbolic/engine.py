"""Symbolic execution engine: State, Rule forward chaining, pipeline composition, and validation."""
from __future__ import annotations

from dataclasses import dataclass, replace
from functools import reduce
from typing import Any, Callable, Mapping

SCHEMA_VERSION = "1.0"
Facts = Mapping[str, Any]


# ---------- Состояние и трасса ----------
@dataclass(frozen=True)
class TraceStep:
    stage: str
    rule_id: str
    detail: str
    degree: float = 1.0


@dataclass(frozen=True)
class State:
    facts: Facts
    trace: tuple[TraceStep, ...] = ()
    verdict: str | None = None
    halted: bool = False


def log(s: State, *steps: TraceStep, **changes) -> State:
    return replace(s, trace=s.trace + steps, **changes)


def compose(*stages: Callable[[State], State]) -> Callable[[State], State]:
    """Цепочка этапов; после halt (жёсткий отказ) остальные пропускаются."""
    return lambda s: reduce(lambda acc, st: acc if acc.halted else st(acc), stages, s)


# ---------- GOFAI: правила и прямой вывод ----------
@dataclass(frozen=True)
class Rule:
    id: str
    when: Callable[[Facts], bool]
    then: Mapping[str, Any] | Callable[[Facts], Mapping[str, Any]] = None
    verdict: str | None = None  # терминальный вердикт => halt
    priority: int = 0
    why: str = ""


def rules_stage(name: str, rules: list[Rule]) -> Callable[[State], State]:
    ordered = sorted(rules, key=lambda r: -r.priority)

    def run(s: State) -> State:
        fired: set[str] = set()
        while True:  # каждое правило срабатывает максимум раз
            r = next((r for r in ordered if r.id not in fired and r.when(s.facts)), None)
            if r is None:
                return s
            fired.add(r.id)
            new = r.then(s.facts) if callable(r.then) else (r.then or {})
            s = log(s, TraceStep(name, r.id, r.why or f"set {dict(new)}"),
                    facts={**s.facts, **new})
            if r.verdict:
                return log(s, verdict=r.verdict, halted=True)

    return run


def validate(schema: Mapping[str, tuple[float, float]]) -> Callable[[State], State]:
    def run(s: State) -> State:
        for k, (lo, hi) in schema.items():
            v = s.facts.get(k)
            if not isinstance(v, (int, float)) or not lo <= v <= hi:
                return log(s, TraceStep("validate", f"V:{k}", f"{k}={v!r} вне [{lo}, {hi}]"),
                           verdict="invalid_input", halted=True)
        return s
    return run


def default_verdict(v: str) -> Callable[[State], State]:
    return lambda s: s if s.verdict else log(s, TraceStep("default", "D", f"вердикт по умолчанию: {v}"),
                                             verdict=v)


# ---------- Контракт для LLM-команды ----------
def run_pipeline(pipe: Callable[[State], State], facts: Facts) -> State:
    return pipe(State(facts=dict(facts)))


def to_dict(s: State) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "verdict": s.verdict,
        "facts": dict(s.facts),
        "trace": [vars(t) for t in s.trace],
    }


# ---------- Контрфактуалы: минимальное изменение одного признака ----------
def counterfactual(pipe: Callable[[State], State], facts: Facts, wanted: str, keys: list[str],
                   span: float = 0.5, n: int = 20) -> list[dict]:
    found = []
    for k in keys:
        base = facts[k]
        for i in range(-n, n + 1):
            rel = span * i / n
            v = base * (1 + rel)
            if i and run_pipeline(pipe, {**facts, k: v}).verdict == wanted:
                found.append({"feature": k, "from": base, "to": round(v, 2), "rel_change": rel})
    return sorted(found, key=lambda c: abs(c["rel_change"]))[:5]
