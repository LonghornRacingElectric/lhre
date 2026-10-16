import copy
from pathlib import Path

import numpy as np
import pytest
import yaml

import anti_oracle as oracle
from tools import anti_paths as ap
from tools import vehicle_checks as vc

REPO = Path(__file__).resolve().parents[2]
VEHICLE = yaml.safe_load((REPO / "vehicle" / "vehicle.yml").read_text(encoding="utf-8"))
BUNDLE = yaml.safe_load((REPO / "vehicle" / "bundle.yml").read_text(encoding="utf-8"))
R = 0.2
H = 0.3
L = 1.5
X_WC = {"front": 0.0, "rear": -L}
C5_KEYS = (
    "axle",
    "source",
    "reaction",
    "coeff_n_per_n",
    "coeff_method",
    "axle_force_share",
    "normalization",
    "h_m",
    "h_basis",
    "wheelbase_m",
    "pose",
    "sign_convention",
    "pct",
    "status",
    "reason",
)
ASYMMETRIC = {
    "front": {
        "lower_fore_i_m": [0.22, 0.24, 0.09],
        "lower_aft_i_m": [-0.18, 0.25, 0.115],
        "upper_fore_i_m": [0.19, 0.29, 0.30],
        "upper_aft_i_m": [-0.21, 0.27, 0.285],
        "lower_o_m": [0.012, 0.565, 0.105],
        "upper_o_m": [-0.015, 0.535, 0.315],
        "tie_o_m": [0.075, 0.555, 0.16],
        "wheel_center_m": [0.0, 0.605, R],
        "rack_pickup_m": [0.085, 0.245, 0.145],
    },
    "rear": {
        "lower_fore_i_m": [-1.26, 0.255, 0.13],
        "lower_aft_i_m": [-1.66, 0.245, 0.085],
        "upper_fore_i_m": [-1.27, 0.285, 0.28],
        "upper_aft_i_m": [-1.64, 0.275, 0.31],
        "lower_o_m": [-1.488, 0.565, 0.098],
        "upper_o_m": [-1.522, 0.528, 0.318],
        "tie_o_m": [-1.405, 0.572, 0.178],
        "wheel_center_m": [-L, 0.605, R],
        "rack_pickup_m": [-1.385, 0.262, 0.168],
    },
}


def _planar(axle, instant_center):
    x = X_WC[axle]
    points = {"wheel_center_m": [x, 0.6, R], "tie_o_m": [x + 0.07, 0.56, 0.15], "rack_pickup_m": [x + 0.07, 0.25, 0.15]}
    for arm, joint in (("lower", (x, 0.10)), ("upper", (x, 0.32))):
        toward = np.subtract(instant_center, joint)
        hinge = np.add(joint, 0.4 * toward / np.linalg.norm(toward))
        points[f"{arm}_fore_i_m"] = [float(hinge[0]), 0.2, float(hinge[1])]
        points[f"{arm}_aft_i_m"] = [float(hinge[0]), 0.3, float(hinge[1])]
        points[f"{arm}_o_m"] = [x, 0.56 if arm == "lower" else 0.53, joint[1]]
    return points


def _vehicle(front, rear, **wheel):
    vehicle = copy.deepcopy(VEHICLE)
    del vehicle["driver_mass"]
    vehicle["sprung_mass"]["cg_m"] = [-0.75, 0.0, H]
    for axle, points in (("front", front), ("rear", rear)):
        points = dict(points)
        vehicle[axle]["steering"]["rack_pickup_m"] = points.pop("rack_pickup_m")
        vehicle[axle]["suspension"] = points
        vehicle[axle]["wheel"].update({"radius_m": R, "toe_deg": 0.0, "camber_deg": 0.0, **wheel})
    return vehicle


def _leaf(value, status="declared"):
    return {"value": value, "status": status if value is not None else "unavailable", "source": "test"}


def _bundle(drive="chassis", front_brake="upright", rear_brake="upright", regen="chassis", datum=0.0):
    bundle = copy.deepcopy(BUNDLE)
    topology = bundle["torque_topology"]
    topology["drive"]["reaction"] = _leaf(drive)
    topology["regen"]["reaction"] = _leaf(regen)
    topology["hydraulic_brake"]["front"]["reaction"] = _leaf(front_brake)
    topology["hydraulic_brake"]["rear"]["reaction"] = _leaf(rear_brake)
    for axle in ("front", "rear"):
        bundle["frame"]["datum_z"][axle] = _leaf(datum)
    return bundle


def _record(records, axle, source):
    found = [record for record in records if (record["axle"], record["source"]) == (axle, source)]
    assert len(found) == 1
    return found[0]


KNOWN = [
    ("front anti-dive", "front", (-1.0, 0.2), -0.2, 0.0),
    ("rear anti-squat", "rear", (-L + 1.0, 0.2), 0.2, 0.0),
    ("rear pro-squat", "rear", (-L + 1.0, 0.0), 0.0, -0.2),
]


def _known_vehicle(axle, instant_center):
    other = "rear" if axle == "front" else "front"
    other_ic = (X_WC[other] + (1.0 if other == "rear" else -1.0), 0.1)
    corners = {axle: _planar(axle, instant_center), other: _planar(other, other_ic)}
    return _vehicle(corners["front"], corners["rear"])


def _known_mismatches(records, axle, c_upright, c_chassis):
    expected = {"upright": c_upright, "chassis": c_chassis}
    wrong = []
    for record in records:
        if record["axle"] != axle:
            continue
        coeff = expected[record["reaction"]]
        pct = oracle.anti_pct(axle, coeff, H, L)
        if abs(record["coeff_n_per_n"] - coeff) > 1e-6 or abs(record["pct"] - pct) > 1e-4:
            wrong.append(f"{axle} {record['source']}: {record['coeff_n_per_n']} N/N, {record['pct']} % (want {coeff}, {pct})")
    return wrong


@pytest.mark.parametrize(
    ("axle", "instant_center", "c_upright", "c_chassis"), [case[1:] for case in KNOWN], ids=[case[0] for case in KNOWN]
)
@pytest.mark.parametrize("brakes", ["upright", "chassis"])
def test_known_instant_centers_give_known_anti(axle, instant_center, c_upright, c_chassis, brakes):
    vehicle = _known_vehicle(axle, instant_center)
    records = ap.anti_records(vehicle, _bundle(front_brake=brakes, rear_brake=brakes))
    assert {record["status"] for record in records} == {"ok"}
    assert _known_mismatches(records, axle, c_upright, c_chassis) == []


def test_a_negated_axle_sign_fails_the_known_anti_check(monkeypatch):
    vehicle = _known_vehicle("rear", (-L + 1.0, 0.2))
    monkeypatch.setattr(ap, "AXLE_SIGN", {"front": 1.0, "rear": -1.0})
    records = ap.anti_records(vehicle, _bundle(drive="upright"))
    assert len(_known_mismatches(records, "rear", 0.2, 0.0)) == 2


def test_a_negated_coefficient_fails_the_known_anti_check(monkeypatch):
    vehicle = _known_vehicle("front", (-1.0, 0.2))
    original = ap._path_coefficients
    monkeypatch.setattr(ap, "_path_coefficients", lambda pose, tangent: {k: -v for k, v in original(pose, tangent).items()})
    records = ap.anti_records(vehicle, _bundle())
    assert len(_known_mismatches(records, "front", -0.2, 0.0)) == 1


def _asymmetric(**wheel):
    return _vehicle(ASYMMETRIC["front"], ASYMMETRIC["rear"], **wheel)


VARIANTS = [
    ("asymmetric", lambda: _asymmetric()),
    ("asymmetric camber", lambda: _asymmetric(camber_deg=-1.5)),
    ("asymmetric camber toe", lambda: _asymmetric(camber_deg=-1.5, toe_deg=0.5)),
    ("lhre", lambda: VEHICLE),
]


@pytest.mark.parametrize("make", [make for _, make in VARIANTS], ids=[name for name, _ in VARIANTS])
@pytest.mark.parametrize("axle", ["front", "rear"])
def test_kinpy_coefficients_match_the_oracle(make, axle):
    vehicle = make()
    corner = oracle.corner_from_vehicle(vehicle, axle)
    found = ap.corner_coefficients(vehicle, axle)
    for reaction, method in ap.COEFF_METHODS.items():
        tolerance = found.tolerance[method]
        assert 0.0 < tolerance < 1e-5
        assert found.values[method] == pytest.approx(oracle.virtual_power_coeff(corner, reaction), abs=tolerance + 1e-9)
        assert found.values[method] == pytest.approx(oracle.statics_coeff(corner, reaction), abs=tolerance + 1e-9)
    assert found.bobsim_instant_link == pytest.approx(found.values["contact_patch_path"], abs=1e-6)
    assert abs(found.values["contact_patch_path"] - found.values["virtual_power_3d"]) > 1e-3


def test_the_wheel_center_formula_is_only_a_cross_check_with_toe():
    vehicle = _asymmetric(camber_deg=-1.5, toe_deg=0.5)
    found = ap.corner_coefficients(vehicle, "rear")
    exact = oracle.virtual_power_coeff(oracle.corner_from_vehicle(vehicle, "rear"), "chassis")
    assert abs(found.values["wheel_center_path"] - exact) > 10 * found.tolerance["virtual_power_3d"]
    level = ap.corner_coefficients(_asymmetric(camber_deg=-1.5), "rear")
    assert level.values["wheel_center_path"] == pytest.approx(level.values["virtual_power_3d"], abs=1e-6)


@pytest.mark.parametrize("axle", ["front", "rear"])
def test_brake_reaction_alone_leaves_inboard_drive_anti_unchanged(axle):
    outboard = ap.anti_records(VEHICLE, _bundle(drive="chassis", front_brake="upright", rear_brake="upright"))
    inboard = ap.anti_records(VEHICLE, _bundle(drive="chassis", front_brake="chassis", rear_brake="chassis"))
    corner = oracle.corner_from_vehicle(VEHICLE, axle)
    drive = oracle.statics(corner, 1000.0, {"drive": "chassis", "hydraulic_brake": "upright"}, {"hydraulic_brake": 0.0}, "drive")[0]
    for records in (outboard, inboard):
        assert _record(records, axle, "drive")["coeff_n_per_n"] == pytest.approx(drive, abs=1e-7)
    assert _record(outboard, axle, "drive") == _record(inboard, axle, "drive")
    assert _record(outboard, axle, "regen") == _record(inboard, axle, "regen")
    lift = {name: _record(records, axle, "hydraulic_brake") for name, records in (("out", outboard), ("in", inboard))}
    assert (lift["out"]["coeff_method"], lift["in"]["coeff_method"]) == ("contact_patch_path", "virtual_power_3d")
    assert lift["out"]["coeff_n_per_n"] == pytest.approx(oracle.statics_coeff(corner, "upright"), abs=1e-7)
    assert lift["in"]["coeff_n_per_n"] == pytest.approx(oracle.statics_coeff(corner, "chassis"), abs=1e-7)
    assert abs(lift["out"]["pct"] - lift["in"]["pct"]) > 1.0


def test_a_hub_motor_uses_the_contact_patch_path():
    records = ap.anti_records(VEHICLE, _bundle(drive="upright"))
    rear = ap.corner_coefficients(VEHICLE, "rear")
    drive = _record(records, "rear", "drive")
    assert (drive["coeff_method"], drive["coeff_n_per_n"]) == ("contact_patch_path", rear.values["contact_patch_path"])


def test_the_committed_bundle_gives_unavailable_records():
    records = ap.anti_records(VEHICLE, BUNDLE)
    assert [(record["axle"], record["source"]) for record in records] == [
        (axle, source) for axle in ("front", "rear") for source in ("drive", "hydraulic_brake", "regen")
    ]
    parts = [VEHICLE["sprung_mass"], VEHICLE["driver_mass"]]
    h_m = sum(p["mass_kg"] * p["cg_m"][2] for p in parts) / sum(p["mass_kg"] for p in parts)
    for record in records:
        assert tuple(record) == C5_KEYS
        assert (record["status"], record["reaction"]) == ("unavailable", "unknown")
        assert record["coeff_n_per_n"] is None and record["pct"] is None and record["coeff_method"] is None
        assert "is unavailable in the bundle" in record["reason"]
        assert record["h_basis"] == "sprung_and_driver_cg_z"
        assert record["h_m"] == pytest.approx(h_m)
        assert record["wheelbase_m"] == pytest.approx(1.5494)
        assert record["normalization"] == "per_axle_force"
    assert _record(records, "front", "hydraulic_brake")["axle_force_share"] == pytest.approx(0.84)
    assert _record(records, "rear", "drive")["axle_force_share"] == 1.0


def test_an_unavailable_datum_withholds_pct_but_not_the_coefficient():
    records = ap.anti_records(VEHICLE, _bundle(datum=None))
    for record in records:
        assert record["status"] == "partial"
        assert record["pct"] is None and record["coeff_n_per_n"] is not None
        assert "frame.datum_z.front is unavailable" in record["reason"]
        assert record["pose"]["h_reference"] == "z = 0 of the vehicle frame"


def test_a_declared_datum_sets_the_cg_height():
    vehicle = _known_vehicle("rear", (-L + 1.0, 0.2))
    records = ap.anti_records(vehicle, _bundle(drive="upright", datum=0.05))
    drive = _record(records, "rear", "drive")
    assert drive["h_m"] == pytest.approx(H - 0.05)
    assert drive["pct"] == pytest.approx(100.0 * 0.2 * L / (H - 0.05), abs=1e-4)


def test_a_datum_above_the_cg_fails():
    with pytest.raises(ValueError, match="not above the ground plane"):
        ap.anti_records(VEHICLE, _bundle(datum=0.5))


def test_records_carry_the_c5_contract():
    records = ap.anti_records(_asymmetric(), _bundle())
    for record in records:
        assert tuple(record) == C5_KEYS
        assert record["status"] == "ok"
        assert record["h_basis"] == "sprung_cg_z"
        assert (record["h_m"], record["wheelbase_m"]) == (pytest.approx(H), pytest.approx(L))
        assert record["pose"]["jounce_m"] == 0.0 and record["pose"]["coeff_tolerance_n_per_n"] < 1e-5
        assert set(record["pose"]["cross_checks_n_per_n"]) == {"wheel_center_path", "bobsim_instant_link"}
        assert "Jacking +Fz lifts the body" in record["sign_convention"]
        assert record["pct"] == pytest.approx(ap.anti_percent(record["axle"], record["coeff_n_per_n"], H, L))
    assert _record(records, "front", "drive")["reason"] == (
        "torque_topology.drive.front_share is assumed. The axle force share is 0. The value is geometric only."
    )
    assert _record(records, "rear", "hydraulic_brake")["axle_force_share"] == pytest.approx(0.16)


def test_unequal_tire_radii_flag_the_brake_share():
    vehicle = _asymmetric()
    vehicle["rear"]["wheel"]["radius_m"] = 0.21
    records = ap.anti_records(vehicle, _bundle())
    for record in records:
        flagged = "brake.front_bias is a torque share" in (record["reason"] or "")
        assert flagged is (record["source"] == "hydraulic_brake")


def test_singular_geometry_fails_with_its_key_path():
    vehicle = copy.deepcopy(VEHICLE)
    vehicle["rear"]["suspension"]["lower_aft_i_m"] = list(vehicle["rear"]["suspension"]["lower_fore_i_m"])
    with pytest.raises(vc.VehicleCheckError, match=r"rear\.suspension\.lower_aft_i_m"):
        ap.anti_records(vehicle, _bundle())


def test_a_bad_reaction_value_fails():
    bundle = _bundle()
    bundle["torque_topology"]["drive"]["reaction"]["value"] = "inboard"
    with pytest.raises(ValueError, match=r"torque_topology\.drive\.reaction"):
        ap.anti_records(VEHICLE, bundle)


def test_a_vehicle_file_cannot_declare_its_own_force_paths():
    vehicle = dict(VEHICLE, force_paths={})
    with pytest.raises(ValueError, match="bundle"):
        ap.anti_records(vehicle, BUNDLE)
