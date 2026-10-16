import dataclasses
import math
from enum import Enum
from typing import Callable, Iterable, Optional


class TrimStatus(str, Enum):
    ACCEPTED = "accepted"
    NUMERICAL_FAILURE = "numerical_failure"
    DOMAIN_REJECTED = "domain_rejected"
    PHYSICALLY_INFEASIBLE = "physically_infeasible"
    NOT_MODELED = "not_modeled"


class BoundKind(str, Enum):
    PHYSICAL = "physical"
    DOMAIN = "domain"
    NUMERICAL = "numerical"
    SEARCH_CEILING = "search_ceiling"
    NOT_MODELED = "not_modeled"


DOMAIN_REASONS = frozenset({
    "alpha_out_of_fit", "kappa_out_of_fit", "fz_out_of_fit", "camber_out_of_fit",
    "alpha_eval_out_of_fit", "kappa_eval_out_of_fit",
    "steer_bound", "steer_domain", "beta_bound", "lon_force_bound", "wheel_lift",
})
PHYSICAL_REASONS = frozenset({"tire_use", "past_peak_axle", "no_equilibrium", "capacity_bound"})
NUMERICAL_REASONS = frozenset({"max_evaluations", "solver_error", "non_finite", "not_converged"})
NOT_MODELED_REASONS = frozenset({"braking_diff_coupling_withheld"})
REASONS = {
    TrimStatus.DOMAIN_REJECTED: DOMAIN_REASONS,
    TrimStatus.PHYSICALLY_INFEASIBLE: PHYSICAL_REASONS,
    TrimStatus.NUMERICAL_FAILURE: NUMERICAL_REASONS,
    TrimStatus.NOT_MODELED: NOT_MODELED_REASONS,
    TrimStatus.ACCEPTED: frozenset(),
}
BOUND_KIND = {
    TrimStatus.PHYSICALLY_INFEASIBLE: BoundKind.PHYSICAL,
    TrimStatus.DOMAIN_REJECTED: BoundKind.DOMAIN,
    TrimStatus.NUMERICAL_FAILURE: BoundKind.NUMERICAL,
    TrimStatus.NOT_MODELED: BoundKind.NOT_MODELED,
}
# A physical claim needs every converged candidate inside the model domain, so domain outranks physical.
PRECEDENCE = (
    TrimStatus.NOT_MODELED, TrimStatus.DOMAIN_REJECTED, TrimStatus.PHYSICALLY_INFEASIBLE, TrimStatus.NUMERICAL_FAILURE,
)


@dataclasses.dataclass(frozen=True)
class TrimRecord:
    status: TrimStatus
    reasons: tuple = ()
    residual_norm: Optional[float] = None
    starts_tried: int = 0
    unknowns: Optional[tuple] = None
    active_bounds: tuple = ()
    wheels: tuple = ()

    def __post_init__(self):
        status = TrimStatus(self.status)
        object.__setattr__(self, "status", status)
        if status is TrimStatus.ACCEPTED and self.reasons:
            raise ValueError(f"An accepted trim has no failure reason. Got {self.reasons}.")
        if status is not TrimStatus.ACCEPTED and not self.reasons:
            raise ValueError(f"A {status.value} trim needs a reason.")
        unknown = [r for r in self.reasons if r.split(":")[0] not in REASONS[status]]
        if unknown:
            raise ValueError(f"{unknown} are not {status.value} reasons. Use one of {sorted(REASONS[status])}.")

    @property
    def accepted(self):
        return self.status is TrimStatus.ACCEPTED

    @property
    def reason(self):
        return ";".join(self.reasons)


def combine(records: Iterable[TrimRecord]) -> TrimRecord:
    records = list(records)
    if not records:
        raise ValueError("combine needs at least one trim record.")
    starts = sum(r.starts_tried for r in records)
    for record in records:
        if record.accepted:
            return dataclasses.replace(record, starts_tried=starts)
    for status in PRECEDENCE:
        picked = [r for r in records if r.status is status]
        if picked:
            reasons = tuple(dict.fromkeys(reason for r in picked for reason in r.reasons))
            best = min(picked, key=lambda r: math.inf if r.residual_norm is None else r.residual_norm)
            return dataclasses.replace(best, reasons=reasons, starts_tried=starts)
    raise AssertionError("unreachable")


@dataclasses.dataclass(frozen=True)
class LimitResult:
    ay_g: Optional[float]
    bound_kind: BoundKind
    lower_status: TrimStatus
    upper: Optional[TrimRecord] = None
    record: Optional[TrimRecord] = None
    bracket: tuple = (None, None)

    def __post_init__(self):
        object.__setattr__(self, "bound_kind", BoundKind(self.bound_kind))
        object.__setattr__(self, "lower_status", TrimStatus(self.lower_status))
        if (self.ay_g is None) != (self.lower_status is not TrimStatus.ACCEPTED):
            raise ValueError("ay_g is set exactly when the lower bracket is an accepted trim.")
        if self.ay_g is not None and not math.isfinite(self.ay_g):
            raise ValueError("ay_g must be finite. Use None and a status for a failure.")

    @property
    def is_grip_limit(self):
        return self.ay_g is not None and self.bound_kind is BoundKind.PHYSICAL

    @property
    def upper_status(self):
        return None if self.upper is None else self.upper.status

    @property
    def upper_reason(self):
        return "" if self.upper is None else self.upper.reason

    def grip_g(self):
        return self.ay_g if self.is_grip_limit else None

    def columns(self):
        return {
            "trim_status": self.lower_status.value,
            "bound_kind": self.bound_kind.value,
            "upper_status": "" if self.upper is None else self.upper.status.value,
            "upper_reason": self.upper_reason,
            "ay_accepted_g": self.ay_g,
            "ay_fail_g": self.bracket[1],
        }

    @classmethod
    def not_modeled(cls, reason):
        record = TrimRecord(TrimStatus.NOT_MODELED, (reason,))
        return cls(None, BoundKind.NOT_MODELED, TrimStatus.NOT_MODELED, upper=record)


def bound_kind(status: TrimStatus) -> BoundKind:
    return BOUND_KIND[TrimStatus(status)]


def bisect_limit(trial: Callable[[float, Optional[TrimRecord]], TrimRecord], starts, high, tol) -> LimitResult:
    failures = []
    best = None
    for start in starts:
        record = trial(start, None)
        if record.accepted:
            low, best = start, record
            break
        failures.append(record)
    if best is None:
        worst = combine(failures)
        return LimitResult(None, bound_kind(worst.status), worst.status, upper=worst, bracket=(None, starts[0]))
    upper = None
    while high - low > tol:
        mid = 0.5 * (low + high)
        record = trial(mid, best)
        if record.accepted:
            low, best = mid, record
        else:
            high, upper = mid, record
    kind = BoundKind.SEARCH_CEILING if upper is None else bound_kind(upper.status)
    return LimitResult(low, kind, TrimStatus.ACCEPTED, upper=upper, record=best, bracket=(low, high))
