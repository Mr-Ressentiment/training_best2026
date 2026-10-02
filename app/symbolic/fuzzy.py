"""Fuzzy logic engine: membership functions (trap, tri), variables, rules, and Mamdani inference stage."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from app.symbolic.engine import State, TraceStep, log


# ---------- Fuzzy: функции принадлежности и движок Мамдани ----------
def trap(a: float, b: float, c: float, d: float) -> Callable[[float], float]:
    """Трапеция; a==b или c==d дают 'плечо'. Треугольник: trap(a, b, b, c)."""
    def mu(x: float) -> float:
        if x < a or x > d:
            return 0.0
        if b <= x <= c:
            return 1.0
        return (x - a) / (b - a) if x < b else (d - x) / (d - c)
    return mu


def tri(a: float, b: float, c: float) -> Callable[[float], float]:
    return trap(a, b, b, c)


@dataclass(frozen=True)
class FuzzyVar:
    name: str
    lo: float
    hi: float
    sets: Mapping[str, Callable[[float], float]]


@dataclass(frozen=True)
class FuzzyRule:
    id: str
    ifs: Mapping[str, str]      # {"dti": "high", "lti": "large"} - AND через min
    then: tuple[str, str]       # ("risk", "high")


def fuzzy_stage(inputs: list[FuzzyVar], output: FuzzyVar, rules: list[FuzzyRule],
                resolution: int = 200) -> Callable[[State], State]:
    ivars = {v.name: v for v in inputs}
    xs = [output.lo + (output.hi - output.lo) * i / resolution for i in range(resolution + 1)]

    def run(s: State) -> State:
        steps, clipped = [], []
        for r in rules:
            degs = {k: ivars[k].sets[lbl](min(max(s.facts[k], ivars[k].lo), ivars[k].hi))
                    for k, lbl in r.ifs.items()}  # clamp: вне универсума не даём mu=0
            deg = min(degs.values())
            if deg > 0:
                clipped.append((deg, output.sets[r.then[1]]))
                cond = " & ".join(f"{k}={r.ifs[k]}({degs[k]:.2f})" for k in r.ifs)
                steps.append(TraceStep("fuzzy", r.id, f"{cond} -> {r.then[0]}={r.then[1]}", deg))
        agg = [max((min(d, mu(x)) for d, mu in clipped), default=0.0) for x in xs]
        area = sum(agg)
        crisp = sum(x * m for x, m in zip(xs, agg)) / area if area else (output.lo + output.hi) / 2
        member = {lbl: mu(crisp) for lbl, mu in output.sets.items()}
        label = max(member, key=member.get)
        steps.append(TraceStep("defuzz", output.name, f"{output.name}={crisp:.1f}, метка={label}",
                                member[label]))
        return log(s, *steps, facts={**s.facts, output.name: crisp, f"{output.name}_label": label})

    return run
