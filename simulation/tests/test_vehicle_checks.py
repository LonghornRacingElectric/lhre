import copy
import shutil
from pathlib import Path

import numpy as np
import pytest
import yaml

from tools import bundle as vb
from tools import vehicle_checks as vc

REPO = Path(__file__).resolve().parents[2]
VEHICLE = yaml.safe_load((REPO / "vehicle" / "vehicle.yml").read_text(encoding="utf-8"))
BUNDLE = yaml.safe_load((REPO / "vehicle" / "bundle.yml").read_text(encoding="utf-8"))
TIRE = "vehicle/tires/16x7p5_10_12psi.tir"
BOBSIM_VEHICLE = yaml.safe_load((REPO / "simulation" / "bobsim" / "vehicle.yml").read_text(encoding="utf-8"))


def _codes(problems):
    return {(problem.path, problem.code) for problem in problems}


def _check(vehicle, frame=None):
    return vc.check_vehicle(vehicle, frame or vc.Frame.from_bundle(BUNDLE))


def _points(vehicle, axle):
    return vehicle[axle]["suspension"]


def _scale_hardpoints(vehicle):
    for key, point in _points(vehicle, "front").items():
        _points(vehicle, "front")[key] = [1000.0 * value for value in point]


def _store_right(vehicle):
    for key, point in _points(vehicle, "front").items():
        _points(vehicle, "front")[key] = [point[0], -point[1], point[2]]


def _rename(section, old, new, scale=1.0):
    def change(vehicle):
        node = vehicle
        for key in section:
            node = node[key]
        node[new] = node.pop(old) * scale

    return change


def _set(path, value):
    def change(vehicle):
        node = vehicle
        for key in path[:-1]:
            node = node[key]
        if value is None:
            del node[path[-1]]
        else:
            node[path[-1]] = value(vehicle) if callable(value) else value

    return change


# Fixtures come from BobSim's own car so they follow its schema, and they
# set the damper fields outright so they hold whichever form our car uses.
BOBSIM_DAMPER = BOBSIM_VEHICLE["front"]["actuation"]["shock"]["damper"]
LINEAR_TABLE = {"table": [[-1, -850], [0, 0], [1, 850]]}


def _shock(axle, damper=None, table=None, drop=None):
    def change(vehicle):
        shock = vehicle[axle]["actuation"]["shock"]
        shock.pop("damper", None)
        shock.pop("damper_table", None)
        if damper is not None:
            shock["damper"] = copy.deepcopy(damper)
            if drop:
                del shock["damper"][drop]
        if table is not None:
            shock["damper_table"] = copy.deepcopy(table)

    return change


def _midpoint(axle, first, second):
    def value(vehicle):
        points = _points(vehicle, axle)
        return [(a + b) / 2 for a, b in zip(points[first], points[second])]  # noqa: B905 pre-existing when the lint gate went on

    return value


def _on_lower_hinge(vehicle):
    points = _points(vehicle, "rear")
    fore, aft = np.array(points["lower_fore_i_m"]), np.array(points["lower_aft_i_m"])
    return (fore + 1.5 * (aft - fore)).tolist()


def _coplanar_wishbones(vehicle):
    points = _points(vehicle, "rear")
    fore, aft, outer = (np.array(points[key]) for key in ("lower_fore_i_m", "lower_aft_i_m", "lower_o_m"))
    points["upper_fore_i_m"] = (fore + 0.2 * (outer - fore)).tolist()
    points["upper_aft_i_m"] = (aft + 0.2 * (outer - aft)).tolist()
    points["upper_o_m"] = (outer + 0.3 * (aft - fore)).tolist()


BAD = [
    ("millimetre hardpoints", _scale_hardpoints, "front.suspension.upper_fore_i_m", "unit_implausible"),
    ("radius in mm key", _rename(("front", "wheel"), "radius_m", "radius_mm", 1000.0), "front.wheel.radius_mm", "unit_unsupported"),
    ("toe in rad key", _rename(("rear", "wheel"), "toe_deg", "toe_rad"), "rear.wheel.toe_rad", "unit_unsupported"),
    ("radius in mm value", _set(("front", "wheel", "radius_m"), 204.5), "front.wheel.radius_m", "unit_implausible"),
    ("camber in rad value", _set(("rear", "wheel", "camber_deg"), 45.0), "rear.wheel.camber_deg", "unit_implausible"),
    ("missing tie rod point", _set(("rear", "suspension", "tie_o_m"), None), "rear.suspension.tie_o_m", "missing_key"),
    ("missing rack pickup", _set(("front", "steering", "rack_pickup_m"), None), "front.steering.rack_pickup_m", "missing_key"),
    ("two-item point", _set(("front", "suspension", "upper_o_m"), [0.0, 0.5]), "front.suspension.upper_o_m", "bad_value"),
    ("right side stored", _store_right, "front.suspension.lower_o_m", "wrong_side"),
    (
        "zero-length lower hinge",
        _set(("rear", "suspension", "lower_aft_i_m"), lambda v: list(_points(v, "rear")["lower_fore_i_m"])),
        "rear.suspension.lower_aft_i_m",
        "coincident_points",
    ),
    (
        "ball joint on the hinge axis",
        _set(("rear", "suspension", "lower_o_m"), _on_lower_hinge),
        "rear.suspension.lower_o_m",
        "singular_geometry",
    ),
    (
        "tie rod on the kingpin axis",
        _set(("front", "suspension", "tie_o_m"), _midpoint("front", "lower_o_m", "upper_o_m")),
        "front.suspension.tie_o_m",
        "singular_geometry",
    ),
    ("coplanar wishbones", _coplanar_wishbones, "rear.suspension", "singular_geometry"),
    (
        "bellcrank pickup on the pivot",
        _set(
            ("rear", "actuation", "bellcrank", "pickups_m", "rod"),
            lambda v: list(v["rear"]["actuation"]["bellcrank"]["pivot_m"]),
        ),
        "rear.actuation.bellcrank.pickups_m.rod",
        "singular_geometry",
    ),
    ("unknown architecture", _set(("architecture", "front"), "pushrod"), "architecture.front", "bad_value"),
    ("rod target typo", _set(("front", "actuation", "rod_to"), "lowr"), "front.actuation.rod_to", "bad_value"),
    ("no damper", _shock("front"), "front.actuation.shock", "bobsim_rejects"),
    ("damper and damper table", _shock("rear", BOBSIM_DAMPER, LINEAR_TABLE), "rear.actuation.shock", "bobsim_rejects"),
    ("damper with no rebound", _shock("front", BOBSIM_DAMPER, drop="rebound"), "front.actuation.shock", "bobsim_rejects"),
    ("one-row damper table", _shock("rear", table={"table": [[0, 0]]}), "rear.actuation.shock", "bobsim_rejects"),
    (
        "unmeasured damper clicks",
        _shock("front", {**BOBSIM_DAMPER, "compression": {**BOBSIM_DAMPER["compression"], "high_speed_turns_from_open": 2.0, "low_speed_clicks_from_closed": 14}}),
        "front.actuation.shock",
        "bobsim_rejects",
    ),
    ("driver mass in grams", _set(("driver_mass", "mass_kg"), 65770.912), "driver_mass.mass_kg", "unit_implausible"),
    (
        "rear axle ahead of front",
        _set(("rear", "suspension", "wheel_center_m"), [0.5, 0.606, 0.2]),
        "rear.suspension.wheel_center_m",
        "incompatible_geometry",
    ),
]


@pytest.mark.parametrize(
    "change",
    [_shock("front", BOBSIM_DAMPER), _shock("rear", table=LINEAR_TABLE)],
    ids=["named damper", "damper table"],
)
def test_a_damper_that_bobsim_reads_passes(change):
    vehicle = copy.deepcopy(VEHICLE)
    change(vehicle)
    assert _check(vehicle) == []


def test_keys_that_lhre_does_not_read_pass():
    vehicle = copy.deepcopy(VEHICLE)
    vehicle["front"]["suspension"]["new_bobsim_point"] = [0.0, 0.5, 0.3]
    vehicle["rear"]["actuation"]["shock"]["new_bobsim_field"] = {"anything": 1}
    vehicle["rear"]["new_bobsim_section"] = {}
    assert _check(vehicle) == []


@pytest.mark.parametrize(
    ("section", "key", "typo"),
    [
        (("rear", "actuation", "shock"), "spring_table", "spring_tabel"),
        (("front", "actuation", "shock"), "free_length_m", "free_lenght_m"),
    ],
)
def test_a_near_miss_of_a_known_key_fails(section, key, typo):
    vehicle = copy.deepcopy(VEHICLE)
    node = vehicle
    for part in section:
        node = node[part]
    node[typo] = node.pop(key)
    path = ".".join((*section, typo))
    assert (path, "unknown_key") in [(p.path, p.code) for p in _check(vehicle)]


@pytest.mark.parametrize(
    ("section", "new_key"),
    [
        (("rear", "actuation", "shock"), "spring_tables"),
        (("front", "suspension"), "upper_fore_x_m"),
    ],
)
def test_a_new_key_beside_a_similar_known_key_passes(section, new_key):
    vehicle = copy.deepcopy(VEHICLE)
    node = vehicle
    for part in section:
        node = node[part]
    node[new_key] = 1.0
    assert _check(vehicle) == []


@pytest.mark.parametrize(
    "shock",
    [
        {"damper": 5},
        {"damper_table": {"table": [["a", "b"], [0, 0], [1, 2]]}},
        {"damper": {**BOBSIM_DAMPER, 1: "x", "extra": "y"}},
    ],
)
def test_a_malformed_damper_is_reported_not_raised(shock):
    vehicle = copy.deepcopy(VEHICLE)
    node = vehicle["front"]["actuation"]["shock"]
    node.pop("damper", None)
    node.pop("damper_table", None)
    node.update(shock)
    assert ("front.actuation.shock", "bobsim_rejects") in [(p.path, p.code) for p in _check(vehicle)]


def test_the_committed_vehicle_passes():
    assert _check(VEHICLE) == []
    for axle in vc.AXLES:
        assert vc.check_corner(VEHICLE, axle) == []


@pytest.mark.parametrize(("change", "where", "code"), [case[1:] for case in BAD], ids=[case[0] for case in BAD])
def test_a_bad_vehicle_fails_with_its_key_path(change, where, code):
    vehicle = copy.deepcopy(VEHICLE)
    change(vehicle)
    assert (where, code) in _codes(_check(vehicle))


def test_check_corner_fails_on_singular_geometry():
    vehicle = copy.deepcopy(VEHICLE)
    _points(vehicle, "rear")["lower_aft_i_m"] = list(_points(vehicle, "rear")["lower_fore_i_m"])
    assert vc.check_corner(vehicle, "front") == []
    with pytest.raises(vc.VehicleCheckError, match=r"rear\.suspension\.lower_aft_i_m"):
        vc.require(vc.check_corner(vehicle, "rear"))


def _frame(change):
    bundle = copy.deepcopy(BUNDLE)
    change(bundle["frame"])
    return vc.Frame.from_bundle(bundle)


FRAMES = [
    (lambda f: f["mirror"]["stored_side"].update(value="right"), "frame.mirror.stored_side", "frame_unsupported"),
    (lambda f: f["mirror"]["rule"].update(value="negate_x"), "frame.mirror.rule", "frame_unsupported"),
    (lambda f: f["units"].update(value={"length": "mm"}), "frame.units", "frame_unsupported"),
    (lambda f: f["axes"].update(value={"x": "rearward", "y": "left", "z": "up"}), "frame.axes", "frame_unsupported"),
    (lambda f: f["datum_z"]["rear"].update(value=4.6, status="declared"), "frame.datum_z.rear", "bad_datum"),
]


@pytest.mark.parametrize(("change", "where", "code"), FRAMES, ids=[where for _, where, _ in FRAMES])
def test_the_frame_declaration_is_checked(change, where, code):
    assert (where, code) in _codes(_check(VEHICLE, _frame(change)))


def test_the_datum_comes_from_the_bundle_frame():
    frame = vc.Frame.from_bundle(BUNDLE)
    assert {axle: (datum.usable, datum.status) for axle, datum in frame.datum_z.items()} == {
        "front": (False, "unavailable"),
        "rear": (False, "unavailable"),
    }
    declared = _frame(lambda f: f["datum_z"]["front"].update(value=0.0, status="declared", source="test"))
    assert declared.datum_z["front"].usable and not declared.datum_z["rear"].usable


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    for rel in ("vehicle/vehicle.yml", "vehicle/bundle.yml", TIRE):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, root / rel)
    monkeypatch.setattr(vb, "REPO", root)

    def write(change):
        vehicle = copy.deepcopy(VEHICLE)
        change(vehicle)
        out = root / "simulation" / "out" / "candidate"
        out.mkdir(parents=True, exist_ok=True)
        path = out / "vehicle.yml"
        path.write_text(yaml.safe_dump(vehicle, sort_keys=False), encoding="utf-8", newline="\n")
        bundle = copy.deepcopy(BUNDLE)
        bundle["bundle_id"] = "orion-candidate-test"
        bundle["members"]["vehicle"].update(path="simulation/out/candidate/vehicle.yml", sha256=vb.file_sha256(path))
        bundle_path = out / "bundle.yml"
        bundle_path.write_text(yaml.safe_dump(bundle, sort_keys=False), encoding="utf-8", newline="\n")
        return bundle_path, path

    return root, write


def test_the_bundle_check_rejects_millimetre_hardpoints(candidate):
    root, write = candidate
    bundle, vehicle = write(_scale_hardpoints)
    problems = vb.check_bundle(vb.load_bundle(bundle), vehicle, root=root)
    assert ("vehicle:front.suspension.upper_fore_i_m", "unit_implausible") in _codes(problems)


def test_the_bundle_check_rejects_a_zero_length_hinge(candidate):
    root, write = candidate
    bundle, vehicle = write(
        _set(("rear", "suspension", "lower_aft_i_m"), lambda v: list(_points(v, "rear")["lower_fore_i_m"]))
    )
    problems = vb.check_bundle(vb.load_bundle(bundle), vehicle, root=root)
    assert ("vehicle:rear.suspension.lower_aft_i_m", "coincident_points") in _codes(problems)


def test_make_check_vehicle_fails_with_the_key_path(candidate, capsys):
    _, write = candidate
    bundle, vehicle = write(_store_right)
    assert vb.main(["check", "--bundle", str(bundle), "--vehicle", str(vehicle)]) == 1
    out = capsys.readouterr().out
    assert "vehicle:front.suspension.lower_o_m" in out
    assert "[wrong_side]" in out


def test_a_bad_frame_is_reported_once(candidate):
    root, write = candidate
    bundle, vehicle = write(lambda vehicle: None)
    data = yaml.safe_load(bundle.read_text(encoding="utf-8"))
    data["frame"]["mirror"]["stored_side"]["value"] = "right"
    bundle.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8", newline="\n")
    problems = vb.check_bundle(vb.load_bundle(bundle), vehicle, root=root)
    assert [problem.path for problem in problems].count("frame.mirror.stored_side") == 1
