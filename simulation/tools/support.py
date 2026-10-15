import dataclasses
import math
from typing import Iterable, Optional

PHYSICAL = "physical"


@dataclasses.dataclass(frozen=True)
class SupportPolicy:
    min_coverage: float
    ay_floor_g: float
    tolerances_pct: tuple

    def __post_init__(self):
        if not 0.0 < self.min_coverage <= 1.0:
            raise ValueError(f"min_coverage must be in (0, 1]. Got {self.min_coverage}.")
        if not (math.isfinite(self.ay_floor_g) and self.ay_floor_g >= 0.0):
            raise ValueError(f"ay_floor_g must be a finite value of 0 or more. Got {self.ay_floor_g}.")
        if not self.tolerances_pct or any(not (math.isfinite(t) and t >= 0.0) for t in self.tolerances_pct):
            raise ValueError(f"tolerances_pct must hold one or more finite values of 0 or more. Got {self.tolerances_pct}.")


@dataclasses.dataclass(frozen=True)
class Observation:
    condition: str
    law: str
    ay_g: Optional[float]
    kind: str
    reason: str = ""

    @property
    def valid(self):
        return self.kind == PHYSICAL and self.ay_g is not None and math.isfinite(self.ay_g) and self.ay_g > 0.0


@dataclasses.dataclass(frozen=True)
class Exclusion:
    condition: str
    law: str
    kind: str
    reason: str


@dataclasses.dataclass(frozen=True)
class LawResult:
    worst_shortfall_pct: Optional[float]
    binding: Optional[str]
    in_region: dict


@dataclasses.dataclass(frozen=True)
class Comparison:
    status: str
    reason: str
    laws: dict
    region: dict
    n_supported: int
    n_total: int
    excluded: tuple
    floor_pct: Optional[float]

    @property
    def coverage(self):
        return self.n_supported / self.n_total if self.n_total else 0.0

    @property
    def recommendation(self):
        return None if self.status != "ok" else self.region

    def excluded_by_kind(self):
        counts = {}
        for item in self.excluded:
            counts[item.kind] = counts.get(item.kind, 0) + 1
        return dict(sorted(counts.items()))


def _cells(observations, laws):
    cells = {}
    for obs in observations:
        if obs.law not in laws:
            continue
        cell = cells.setdefault(obs.condition, {})
        if obs.law in cell:
            raise ValueError(f"{obs.law} appears twice in {obs.condition}.")
        cell[obs.law] = obs
    return cells


def _exclusions(condition, cell, laws, baseline, policy):
    out = []
    for law in laws:
        obs = cell.get(law)
        if obs is None:
            out.append(Exclusion(condition, law, "missing", "no row"))
        elif not obs.valid:
            out.append(Exclusion(condition, law, obs.kind, obs.reason))
    if not out and cell[baseline].ay_g < policy.ay_floor_g:
        out.append(Exclusion(condition, baseline, "ill_conditioned", f"baseline {cell[baseline].ay_g:.3f} g < floor {policy.ay_floor_g:g} g"))
    return out


def compare(observations: Iterable[Observation], laws, baseline, policy: SupportPolicy) -> Comparison:
    laws = tuple(dict.fromkeys(laws))
    if baseline not in laws:
        raise ValueError(f"The baseline {baseline} must be one of the compared laws {laws}.")
    cells = _cells(observations, laws)
    excluded, supported = [], []
    for condition, cell in cells.items():
        out = _exclusions(condition, cell, laws, baseline, policy)
        excluded += out
        if not out:
            supported.append(condition)
    worst = {law: (None, None) for law in laws}
    for condition in supported:
        cell = cells[condition]
        best = max(cell[law].ay_g for law in laws)
        for law in laws:
            short = 100.0 * (1.0 - cell[law].ay_g / best)
            if worst[law][0] is None or short > worst[law][0]:
                worst[law] = (short, condition)
    floor = min((w for w, _ in worst.values() if w is not None), default=None)
    status, reason = "ok", ""
    if not supported:
        status, reason = "withheld", "no_common_support"
    elif len(supported) / len(cells) < policy.min_coverage:
        status, reason = "withheld", "low_coverage"
    results, region = {}, {}
    for tol in policy.tolerances_pct:
        key = f"{tol:g}"
        region[key] = () if status != "ok" else tuple(law for law in laws if worst[law][0] <= floor + tol)
    for law in laws:
        short, binding = worst[law]
        results[law] = LawResult(
            None if short is None else round(short, 2), binding,
            {f"{tol:g}": law in region[f"{tol:g}"] for tol in policy.tolerances_pct},
        )
    return Comparison(status, reason, results, region, len(supported), len(cells), tuple(excluded),
                      None if floor is None else round(floor, 2))


@dataclasses.dataclass(frozen=True)
class CellBest:
    law: Optional[str]
    gain_pct: Optional[float]
    status: str
    reason: str = ""


def cell_best(cell: dict, laws, baseline, policy: SupportPolicy, min_gain_pct) -> CellBest:
    out = _exclusions("", cell, tuple(dict.fromkeys((baseline, *laws))), baseline, policy)
    if out:
        kinds = sorted({o.kind for o in out})
        return CellBest(None, None, "withheld", f"{len(out)} excluded: {', '.join(kinds)}")
    ref = cell[baseline].ay_g
    gain, law = max((100.0 * (cell[law].ay_g / ref - 1.0), law) for law in laws)
    if gain <= min_gain_pct:
        return CellBest(baseline, 0.0, "ok")
    return CellBest(law, gain, "ok")


def gain_pct(ay_g, baseline_ay_g, policy: SupportPolicy):
    if ay_g is None or baseline_ay_g is None or baseline_ay_g < policy.ay_floor_g:
        return None
    return 100.0 * (ay_g / baseline_ay_g - 1.0)
