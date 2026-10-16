import dataclasses
import hashlib
import math
from pathlib import Path
from typing import Mapping, Optional

LIMIT_KEYS = {
    "fz": ("FZMIN", "FZMAX"),
    "alpha": ("ALPMIN", "ALPMAX"),
    "kappa": ("KPUMIN", "KPUMAX"),
    "gamma": ("CAMMIN", "CAMMAX"),
}
UNITS = {"FORCE": "newton", "ANGLE": "radians"}
CODES = {"fz": "fz_out_of_fit", "alpha": "alpha_out_of_fit", "kappa": "kappa_out_of_fit", "gamma": "camber_out_of_fit"}
EDGE_TOL = 1e-9


class TireSupportError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class TireSupport:
    fz: tuple
    alpha: tuple
    kappa: tuple
    gamma: tuple
    source: str = ""
    sha256: Optional[str] = None

    def __post_init__(self):
        for name in LIMIT_KEYS:
            lo, hi = getattr(self, name)
            if not (math.isfinite(lo) and math.isfinite(hi) and lo <= hi):
                raise TireSupportError(f"{self.source or 'tire'}: the {name} fit range [{lo}, {hi}] is not a finite range.")

    @classmethod
    def from_coefficients(cls, coefficients: Mapping, source="", sha256=None):
        where = source or "tire"
        for key, unit in UNITS.items():
            value = coefficients.get(key)
            if value is None:
                raise TireSupportError(f"{where} has no {key} unit. The fit ranges need {key} = '{unit}'.")
            if str(value).strip().lower() != unit:
                raise TireSupportError(f"{where} has {key} = '{value}'. The fit ranges need '{unit}'.")
        ranges = {}
        for name, keys in LIMIT_KEYS.items():
            missing = [k for k in keys if k not in coefficients]
            if missing:
                raise TireSupportError(f"{where} has no {', '.join(missing)}. The study cannot bound {name} without it.")
            try:
                ranges[name] = tuple(float(coefficients[k]) for k in keys)
            except (TypeError, ValueError) as error:
                raise TireSupportError(f"{where}: {keys} must be numbers. {error}") from None
        return cls(source=source, sha256=sha256, **ranges)

    @classmethod
    def from_tir(cls, path):
        from common.vehicle_io import parse_tir

        path = Path(path)
        return cls.from_coefficients(parse_tir(path), source=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def violations(self, fz, alpha, kappa, gamma, corner=""):
        found = []
        for name, value in (("fz", fz), ("alpha", alpha), ("kappa", kappa), ("gamma", gamma)):
            lo, hi = getattr(self, name)
            span = EDGE_TOL * max(1.0, abs(lo), abs(hi))
            if value is None or not math.isfinite(value) or not lo - span <= value <= hi + span:
                found.append(f"{CODES[name]}:{corner}" if corner else CODES[name])
        return tuple(found)

    def search_bounds(self, name, lo, hi):
        fit_lo, fit_hi = getattr(self, name)
        bounded = (max(lo, fit_lo), min(hi, fit_hi))
        if bounded[0] > bounded[1]:
            raise TireSupportError(f"The search range [{lo}, {hi}] for {name} lies outside the fit range [{fit_lo}, {fit_hi}].")
        return bounded

    def at_edge(self, name, value, tol=1e-4):
        lo, hi = getattr(self, name)
        return abs(value - lo) <= tol or abs(value - hi) <= tol

    def summary(self):
        # Tire data is restricted by default, so artifacts name the limits and carry no .tir values.
        return {"source": self.source, "sha256": self.sha256, "limit_keys": {name: list(keys) for name, keys in LIMIT_KEYS.items()}}
