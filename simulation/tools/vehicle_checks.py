import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np

AXLES = ("front", "rear")
ARCHITECTURES = ("direct", "bellcrank", "bellcrank_stabar")
ROD_TARGETS = ("lower", "upper")
BOBSIM_AXES = {"x": "forward", "y": "left", "z": "up"}
COINCIDENT_M = 1e-4
MAX_COORDINATE_M = 3.0
WHEEL_RADIUS_M = (0.1, 0.5)
MAX_STATIC_ANGLE_DEG = 10.0
MIN_MASS_KG = 1e-6
MAX_MASS_KG = 2000.0
RANK_TOLERANCE = 1e-6
FOREIGN_UNITS = {"mm": "m", "cm": "m", "in": "m", "inch": "m", "ft": "m", "rad": "deg", "g": "kg", "lb": "kg"}

SUSPENSION_POINTS = (
    "upper_fore_i_m",
    "upper_aft_i_m",
    "lower_fore_i_m",
    "lower_aft_i_m",
    "upper_o_m",
    "lower_o_m",
    "tie_o_m",
    "wheel_center_m",
)
AXLE_KEYS = ("wheel", "tire", "suspension", "steering", "actuation", "masses")
WHEEL_KEYS = ("radius_m", "rim_radius_ratio", "rim_width_ratio", "toe_deg", "camber_deg")
STEERING_KEYS = ("rack_pickup_m", "rack_travel_per_rev_m")
ACTUATION_KEYS = ("rod_to", "rod_mount_m", "shock", "bellcrank", "stabar")
SHOCK_KEYS = ("mount_m", "spring_table", "free_length_m", "damper_table")
BELLCRANK_KEYS = ("pivot_m", "axis", "pickups_m", "order")
STABAR_KEYS = ("arm_end_m", "bar_end_m", "rate_n_m_per_rad")
MASS_KEYS = ("mass_kg", "cg_m", "inertia_kg_m2")
AXLE_MASSES = ("unsprung", "upper_control_arm", "lower_control_arm", "tie_rod")

UPRIGHT_LINKS = (
    ("upper wishbone front leg", "suspension.upper_fore_i_m", "suspension.upper_o_m"),
    ("upper wishbone rear leg", "suspension.upper_aft_i_m", "suspension.upper_o_m"),
    ("lower wishbone front leg", "suspension.lower_fore_i_m", "suspension.lower_o_m"),
    ("lower wishbone rear leg", "suspension.lower_aft_i_m", "suspension.lower_o_m"),
    ("tie rod", "steering.rack_pickup_m", "suspension.tie_o_m"),
)
HINGES = (
    ("upper", "suspension.upper_fore_i_m", "suspension.upper_aft_i_m", "suspension.upper_o_m"),
    ("lower", "suspension.lower_fore_i_m", "suspension.lower_aft_i_m", "suspension.lower_o_m"),
)
ROCKER_LINKS = {
    "rod": ("pushrod", "actuation.rod_mount_m"),
    "shock": ("shock", "actuation.shock.mount_m"),
    "stabar": ("stabar drop link", "actuation.stabar.arm_end_m"),
}


@dataclass(frozen=True)
class Problem:
    path: str
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message} [{self.code}]"


class VehicleCheckError(ValueError):
    def __init__(self, problems: Iterable[Problem]) -> None:
        self.problems = tuple(problems)
        super().__init__("The vehicle fails its checks:\n" + "\n".join(f"  {problem}" for problem in self.problems))


@dataclass(frozen=True)
class Datum:
    value: Any
    status: Any
    source: Any

    @property
    def usable(self) -> bool:
        return self.status != "unavailable" and _finite(self.value) and abs(self.value) <= MAX_COORDINATE_M


UNAVAILABLE = Datum(None, "unavailable", None)


@dataclass(frozen=True)
class Frame:
    axes: Any = field(default_factory=lambda: dict(BOBSIM_AXES))
    length_unit: Any = "m"
    stored_side: Any = "left"
    mirror_rule: Any = "negate_y"
    datum_z: Mapping[str, Datum] = field(default_factory=lambda: {axle: UNAVAILABLE for axle in AXLES})

    @classmethod
    def from_bundle(cls, bundle: Mapping[str, Any]) -> "Frame":
        frame = _get(bundle, "frame")
        units = _get(frame, "units", "value")
        datum = {}
        for axle in AXLES:
            leaf = _get(frame, "datum_z", axle)
            datum[axle] = (
                Datum(leaf.get("value"), leaf.get("status"), leaf.get("source"))
                if isinstance(leaf, Mapping) and leaf
                else UNAVAILABLE
            )
        return cls(
            axes=_get(frame, "axes", "value"),
            length_unit=units.get("length") if isinstance(units, Mapping) else units,
            stored_side=_get(frame, "mirror", "stored_side", "value"),
            mirror_rule=_get(frame, "mirror", "rule", "value"),
            datum_z=datum,
        )


def _get(node: Any, *keys: str) -> Any:
    for key in keys:
        node = node.get(key) if isinstance(node, Mapping) else None
    return {} if node is None else node


def _join(prefix: str, key: object) -> str:
    return f"{prefix}.{key}" if prefix else str(key)


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _unit(vector: np.ndarray) -> np.ndarray:
    return vector / np.linalg.norm(vector)


class _Checks:
    def __init__(self) -> None:
        self.problems: list[Problem] = []

    def add(self, path: str, code: str, message: str) -> None:
        self.problems.append(Problem(path, code, message))

    def section(self, parent: Mapping[str, Any], key: str, path: str) -> Mapping[str, Any] | None:
        where = _join(path, key)
        if key not in parent:
            self.add(where, "missing_key", "is missing.")
            return None
        if not isinstance(parent[key], Mapping):
            self.add(where, "wrong_type", "must be a mapping.")
            return None
        return parent[key]

    def keys(self, node: Mapping[str, Any], path: str, allowed: tuple[str, ...]) -> None:
        for key in node:
            if key in allowed:
                continue
            stem, _, suffix = str(key).rpartition("_")
            unit = FOREIGN_UNITS.get(suffix)
            if stem and unit and f"{stem}_{unit}" in allowed:
                self.add(
                    _join(path, key),
                    "unit_unsupported",
                    f"is in {suffix}. BobSim reads {stem}_{unit} in {unit}. Convert the value and rename the key.",
                )
            else:
                self.add(_join(path, key), "unknown_key", f"is not a key here. Allowed keys: {', '.join(allowed)}.")

    def number(
        self, node: Mapping[str, Any], key: str, path: str, low: float, high: float, unit: str, *, required: bool = True
    ) -> float | None:
        where = _join(path, key)
        if key not in node:
            if required:
                self.add(where, "missing_key", "is missing.")
            return None
        value = node[key]
        if not _finite(value):
            self.add(where, "bad_value", f"is {value!r}. It must be a finite number in {unit}.")
            return None
        if not low <= value <= high:
            self.add(where, "unit_implausible", f"is {value:g} {unit}. Expected {low:g} to {high:g} {unit}. Check the unit.")
            return None
        return float(value)

    def point(self, node: Mapping[str, Any], key: str, path: str, *, stored: bool = True) -> np.ndarray | None:
        where = _join(path, key)
        if key not in node:
            self.add(where, "missing_key", "is missing. Every hardpoint is required.")
            return None
        value = node[key]
        if not (isinstance(value, (list, tuple)) and len(value) == 3 and all(_finite(item) for item in value)):
            self.add(where, "bad_value", f"is {value!r}. It must be [x, y, z] in metres.")
            return None
        point = np.array(value, dtype=float)
        largest = float(np.max(np.abs(point)))
        if largest > MAX_COORDINATE_M:
            self.add(
                where,
                "unit_implausible",
                f"has a coordinate of {largest:g} m. Points lie within {MAX_COORDINATE_M:g} m of the origin. "
                "A value in millimetres gives this.",
            )
            return None
        if stored and point[1] <= COINCIDENT_M:
            self.add(
                where,
                "wrong_side",
                f"has y = {point[1]:g} m. The file stores the left corner (y > 0). "
                "BobSim builds the right corner by negating y.",
            )
        return point


def _check_frame(c: _Checks, frame: Frame) -> None:
    if frame.axes != BOBSIM_AXES:
        c.add("frame.axes", "frame_unsupported", f"is {frame.axes!r}. BobSim reads x forward, y left, z up.")
    if frame.length_unit != "m":
        c.add("frame.units", "frame_unsupported", f"gives length {frame.length_unit!r}. BobSim reads metres.")
    if frame.stored_side != "left":
        c.add("frame.mirror.stored_side", "frame_unsupported", f"is {frame.stored_side!r}. BobSim stores the left corner.")
    if frame.mirror_rule != "negate_y":
        c.add("frame.mirror.rule", "frame_unsupported", f"is {frame.mirror_rule!r}. BobSim mirrors by negating y.")
    for axle in AXLES:
        datum = frame.datum_z.get(axle, UNAVAILABLE)
        if datum.value is not None and not datum.usable:
            c.add(
                f"frame.datum_z.{axle}",
                "bad_datum",
                f"value {datum.value!r} must be the ground-plane z in metres, within {MAX_COORDINATE_M:g} m of 0.",
            )


def _check_corner(c: _Checks, side: Mapping[str, Any], axle: str) -> dict[str, np.ndarray]:
    points: dict[str, np.ndarray] = {}
    radius = camber = None
    wheel = c.section(side, "wheel", axle)
    if wheel is not None:
        path = f"{axle}.wheel"
        c.keys(wheel, path, WHEEL_KEYS)
        radius = c.number(wheel, "radius_m", path, *WHEEL_RADIUS_M, "m")
        camber = c.number(wheel, "camber_deg", path, -MAX_STATIC_ANGLE_DEG, MAX_STATIC_ANGLE_DEG, "deg")
        c.number(wheel, "toe_deg", path, -MAX_STATIC_ANGLE_DEG, MAX_STATIC_ANGLE_DEG, "deg")
    suspension = c.section(side, "suspension", axle)
    if suspension is not None:
        c.keys(suspension, f"{axle}.suspension", SUSPENSION_POINTS)
        for key in SUSPENSION_POINTS:
            _keep(points, f"suspension.{key}", c.point(suspension, key, f"{axle}.suspension"))
    steering = c.section(side, "steering", axle)
    if steering is not None:
        c.keys(steering, f"{axle}.steering", STEERING_KEYS)
        _keep(points, "steering.rack_pickup_m", c.point(steering, "rack_pickup_m", f"{axle}.steering"))
    center = points.get("suspension.wheel_center_m")
    if radius is not None and camber is not None and center is not None:
        tilt = math.radians(camber)
        points["contact_patch"] = center - radius * np.array([0.0, math.sin(tilt), math.cos(tilt)])
    _check_linkage(c, points, axle)
    return points


def _keep(points: dict[str, np.ndarray], key: str, value: np.ndarray | None) -> None:
    if value is not None:
        points[key] = value


def _coincident(c: _Checks, points: dict[str, np.ndarray], axle: str, label: str, first: str, second: str) -> bool:
    if first not in points or second not in points:
        return False
    gap = float(np.linalg.norm(points[second] - points[first]))
    if gap >= COINCIDENT_M:
        return False
    c.add(f"{axle}.{second}", "coincident_points", f"is {gap * 1000:.3f} mm from {axle}.{first}. The {label} has zero length.")
    return True


def _check_linkage(c: _Checks, points: dict[str, np.ndarray], axle: str) -> None:
    before = len(c.problems)
    for arm, fore, aft, outer in HINGES:
        if _coincident(c, points, axle, f"{arm} wishbone hinge", fore, aft) or outer not in points:
            continue
        if fore in points and aft in points:
            axis = _unit(points[aft] - points[fore])
            gap = float(np.linalg.norm(np.cross(points[outer] - points[fore], axis)))
            if gap < COINCIDENT_M:
                c.add(
                    f"{axle}.{outer}",
                    "singular_geometry",
                    f"is {gap * 1000:.3f} mm from the {arm} wishbone hinge axis. The wishbone cannot locate the upright.",
                )
    for label, inboard, outboard in UPRIGHT_LINKS:
        _coincident(c, points, axle, label, inboard, outboard)
    _coincident(c, points, axle, "kingpin", "suspension.lower_o_m", "suspension.upper_o_m")
    needed = {key for _, *keys in UPRIGHT_LINKS for key in keys} | {"contact_patch"}
    if len(c.problems) > before or not needed <= set(points):
        return
    lower, upper, tie = points["suspension.lower_o_m"], points["suspension.upper_o_m"], points["suspension.tie_o_m"]
    tie_axis = _unit(tie - points["steering.rack_pickup_m"])
    steer_arm = abs(float(np.dot(_unit(upper - lower), np.cross(tie - lower, tie_axis))))
    if steer_arm < COINCIDENT_M:
        c.add(
            f"{axle}.suspension.tie_o_m",
            "singular_geometry",
            f"gives the tie rod a {steer_arm * 1000:.3f} mm moment arm about the kingpin axis. Toe is not constrained.",
        )
        return
    contact = points["contact_patch"]
    rows = []
    for _, inboard, outboard in UPRIGHT_LINKS:
        direction = _unit(points[outboard] - points[inboard])
        rows.append(np.concatenate((direction, np.cross(points[outboard] - contact, direction))))
    singular = np.linalg.svd(np.array(rows), compute_uv=False)
    if singular[-1] < RANK_TOLERANCE * singular[0]:
        c.add(
            f"{axle}.suspension",
            "singular_geometry",
            "The two wishbones and the tie rod do not locate the upright. Their five link lines are dependent.",
        )


def _check_actuation(c: _Checks, side: Mapping[str, Any], axle: str, architecture: str | None) -> None:
    actuation = c.section(side, "actuation", axle)
    if actuation is None:
        return
    path = f"{axle}.actuation"
    points: dict[str, np.ndarray] = {}
    c.keys(actuation, path, ACTUATION_KEYS)
    if actuation.get("rod_to") not in ROD_TARGETS:
        c.add(f"{path}.rod_to", "bad_value", f"is {actuation.get('rod_to')!r}. It must be lower or upper.")
    _keep(points, "actuation.rod_mount_m", c.point(actuation, "rod_mount_m", path))
    shock = c.section(actuation, "shock", path)
    if shock is not None:
        c.keys(shock, f"{path}.shock", SHOCK_KEYS)
        _keep(points, "actuation.shock.mount_m", c.point(shock, "mount_m", f"{path}.shock"))
    if architecture == "direct":
        _coincident(c, points, axle, "shock", "actuation.rod_mount_m", "actuation.shock.mount_m")
        return
    if architecture not in ARCHITECTURES:
        return
    pickups = ("rod", "shock", "stabar") if architecture == "bellcrank_stabar" else ("rod", "shock")
    bellcrank = c.section(actuation, "bellcrank", path)
    if bellcrank is not None:
        crank = f"{path}.bellcrank"
        c.keys(bellcrank, crank, BELLCRANK_KEYS)
        _keep(points, "pivot", c.point(bellcrank, "pivot_m", crank))
        axis = c.point(bellcrank, "axis", crank, stored=False)
        if axis is not None and np.linalg.norm(axis) < 1e-9:
            c.add(f"{crank}.axis", "singular_geometry", "has zero length. The bellcrank pivot axis is undefined.")
        elif axis is not None:
            points["axis"] = _unit(axis)
        named = c.section(bellcrank, "pickups_m", crank)
        if named is not None:
            c.keys(named, f"{crank}.pickups_m", pickups)
            for name in pickups:
                _keep(points, f"pickup.{name}", c.point(named, name, f"{crank}.pickups_m"))
    if architecture == "bellcrank_stabar":
        stabar = c.section(actuation, "stabar", path)
        if stabar is not None:
            c.keys(stabar, f"{path}.stabar", STABAR_KEYS)
            _keep(points, "actuation.stabar.arm_end_m", c.point(stabar, "arm_end_m", f"{path}.stabar"))
            _keep(points, "actuation.stabar.bar_end_m", c.point(stabar, "bar_end_m", f"{path}.stabar"))
            _coincident(c, points, axle, "stabar arm", "actuation.stabar.arm_end_m", "actuation.stabar.bar_end_m")
    if "pivot" not in points or "axis" not in points:
        return
    pivot, axis = points["pivot"], points["axis"]
    for name in pickups:
        pickup, (label, far_key) = points.get(f"pickup.{name}"), ROCKER_LINKS[name]
        where = f"{path}.bellcrank.pickups_m.{name}"
        if pickup is None:
            continue
        lever = float(np.linalg.norm(np.cross(pickup - pivot, axis)))
        far = points.get(far_key)
        if lever < COINCIDENT_M:
            c.add(where, "singular_geometry", f"is {lever * 1000:.3f} mm from the bellcrank pivot axis. It has no lever arm.")
        elif far is not None and np.linalg.norm(far - pickup) < COINCIDENT_M:
            c.add(where, "coincident_points", f"is on {axle}.{far_key}. The {label} has zero length.")
        elif far is not None:
            arm = abs(float(np.dot(axis, np.cross(pickup - pivot, _unit(far - pickup)))))
            if arm < COINCIDENT_M:
                c.add(where, "singular_geometry", f"gives the {label} a {arm * 1000:.3f} mm moment arm. It cannot turn the bellcrank.")


def _check_mass(c: _Checks, node: Mapping[str, Any], key: str, path: str, *, stored: bool) -> np.ndarray | None:
    mass = c.section(node, key, path)
    if mass is None:
        return None
    where = _join(path, key)
    c.keys(mass, where, MASS_KEYS)
    c.number(mass, "mass_kg", where, MIN_MASS_KG, MAX_MASS_KG, "kg")
    return c.point(mass, "cg_m", where, stored=stored)


def check_corner(vehicle: Mapping[str, Any], axle: str, frame: Frame | None = None) -> list[Problem]:
    c = _Checks()
    _check_frame(c, frame or Frame())
    side = vehicle.get(axle) if isinstance(vehicle, Mapping) else None
    if not isinstance(side, Mapping):
        c.add(axle, "missing_key", "is missing or is not a mapping.")
    else:
        _check_corner(c, side, axle)
    return c.problems


def check_vehicle(vehicle: Mapping[str, Any], frame: Frame | None = None) -> list[Problem]:
    c = _Checks()
    _check_frame(c, frame or Frame())
    if not isinstance(vehicle, Mapping):
        c.add("", "wrong_type", "The vehicle file must be a mapping.")
        return c.problems
    architecture = c.section(vehicle, "architecture", "") or {}
    centers: dict[str, np.ndarray] = {}
    for axle in AXLES:
        kind = architecture.get(axle)
        if architecture and kind not in ARCHITECTURES:
            c.add(f"architecture.{axle}", "bad_value", f"is {kind!r}. It must be one of: {', '.join(ARCHITECTURES)}.")
        side = c.section(vehicle, axle, "")
        if side is None:
            continue
        c.keys(side, axle, AXLE_KEYS)
        points = _check_corner(c, side, axle)
        _keep(centers, axle, points.get("suspension.wheel_center_m"))
        _check_actuation(c, side, axle, kind)
        masses = c.section(side, "masses", axle)
        for key in AXLE_MASSES if masses is not None else ():
            _check_mass(c, masses, key, f"{axle}.masses", stored=True)
    sprung = _check_mass(c, vehicle, "sprung_mass", "", stored=False)
    if "driver_mass" in vehicle:
        _check_mass(c, vehicle, "driver_mass", "", stored=False)
    if len(centers) == 2 and centers["rear"][0] >= centers["front"][0] - COINCIDENT_M:
        c.add(
            "rear.suspension.wheel_center_m",
            "incompatible_geometry",
            f"has x = {centers['rear'][0]:g} m. It must be behind the front wheel center at x = {centers['front'][0]:g} m.",
        )
    elif len(centers) == 2 and sprung is not None and not centers["rear"][0] < sprung[0] < centers["front"][0]:
        c.add("sprung_mass.cg_m", "incompatible_geometry", f"has x = {sprung[0]:g} m. It must lie between the axles.")
    return c.problems


def require(problems: Iterable[Problem]) -> None:
    problems = list(problems)
    if problems:
        raise VehicleCheckError(problems)
