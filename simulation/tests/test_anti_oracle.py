import copy
import math
from pathlib import Path

import numpy as np
import pytest
import yaml

import anti_oracle as oracle

REPO = Path(__file__).resolve().parents[2]
VEHICLE = yaml.safe_load((REPO / "vehicle" / "vehicle.yml").read_text(encoding="utf-8"))
R = 0.2
H = 0.3
L = 1.5
X_CP = -1.5


def _links_through(instant_center):
    links = []
    for z_upright in (0.1, 0.32):
        toward = np.subtract(instant_center, (X_CP, z_upright))
        chassis = np.add((X_CP, z_upright), 0.4 * toward / np.linalg.norm(toward))
        links.append(((float(chassis[0]), float(chassis[1])), (X_CP, z_upright)))
    return links


def _parallel_corner():
    lower_o, upper_o, tie_o = (np.array([X_CP, 0.56, z]) for z in (0.10, 0.32, 0.20))
    tie_o[0] += 0.07
    chassis = [
        lower_o + [0.2, -0.31, 0.0],
        lower_o + [-0.2, -0.31, 0.0],
        upper_o + [0.2, -0.31, 0.0],
        upper_o + [-0.2, -0.31, 0.0],
        tie_o + [0.0, -0.31, 0.0],
    ]
    r_wc = np.array([X_CP, 0.6, R])
    return oracle.Corner(
        chassis=tuple(chassis),
        upright=(lower_o, lower_o, upper_o, upper_o, tie_o),
        r_cp=r_wc - [0.0, 0.0, R],
        r_wc=r_wc,
        spin=np.array([0.0, 1.0, 0.0]),
    )


@pytest.mark.parametrize("reaction", ["upright", "chassis"])
def test_horizontal_links_give_no_jacking(reaction):
    z_force = 0.0 if reaction == "upright" else R
    links = [((X_CP + 0.4, z), (X_CP, z)) for z in (0.1, 0.32)]
    assert oracle.planar_coeff(links, X_CP, z_force) == pytest.approx(0.0, abs=1e-12)
    corner = _parallel_corner()
    assert oracle.statics_coeff(corner, reaction) == pytest.approx(0.0, abs=1e-12)
    assert oracle.virtual_power_coeff(corner, reaction) == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("slope", [-0.15, 0.1])
def test_inclined_parallel_links_give_the_slope_on_both_paths(slope):
    direction = np.array([1.0, slope]) / math.hypot(1.0, slope)
    links = [(tuple(np.add((X_CP, z), 0.4 * direction)), (X_CP, z)) for z in (0.1, 0.32)]
    for z_force in (0.0, R):
        assert oracle.planar_coeff(links, X_CP, z_force) == pytest.approx(slope, abs=1e-12)


KNOWN = [
    ("rear anti-squat", "rear", (X_CP + 1.0, 0.2), 0.2, 0.0, 100.0, 0.0),
    ("rear pro-squat", "rear", (X_CP + 1.0, 0.0), 0.0, -0.2, 0.0, -100.0),
    ("front anti-dive", "front", (X_CP - 1.0, 0.2), -0.2, 0.0, 100.0, 0.0),
]


@pytest.mark.parametrize(
    ("axle", "instant_center", "c_upright", "c_chassis", "pct_upright", "pct_chassis"),
    [case[1:] for case in KNOWN],
    ids=[case[0] for case in KNOWN],
)
def test_known_instant_centers_give_known_anti(axle, instant_center, c_upright, c_chassis, pct_upright, pct_chassis):
    links = _links_through(instant_center)
    for z_force, coeff, pct in ((0.0, c_upright, pct_upright), (R, c_chassis, pct_chassis)):
        assert oracle.instant_center_coeff(X_CP, instant_center, z_force) == pytest.approx(coeff, abs=1e-12)
        assert oracle.planar_coeff(links, X_CP, z_force) == pytest.approx(coeff, abs=1e-12)
        assert oracle.anti_pct(axle, coeff, H, L) == pytest.approx(pct, abs=1e-9)


def test_front_and_rear_signs_follow_the_pitch_load_transfer():
    for fx in (1000.0, -1000.0):
        assert oracle.pitch_transfer("rear", fx, H, L) == pytest.approx(-oracle.pitch_transfer("front", fx, H, L))
    assert oracle.pitch_transfer("rear", 1000.0, H, L) > 0.0
    assert oracle.anti_pct("front", -H / L, H, L) == pytest.approx(100.0)
    assert oracle.anti_pct("rear", H / L, H, L) == pytest.approx(100.0)


@pytest.mark.parametrize("axle", ["front", "rear"])
@pytest.mark.parametrize("reaction", ["upright", "chassis"])
def test_fx_sign_reversal_flips_jacking_but_not_the_coefficient_or_anti(axle, reaction):
    corner = oracle.corner_from_vehicle(VEHICLE, axle)
    drive = oracle.statics(corner, 1000.0, {"source": reaction}, {}, "source")[0]
    brake = oracle.statics(corner, -1000.0, {"source": reaction}, {}, "source")[0]
    assert abs(drive) > 1e-4
    assert drive == pytest.approx(brake, rel=1e-12)
    assert drive * 1000.0 == pytest.approx(-brake * -1000.0, rel=1e-12)
    assert oracle.anti_pct(axle, drive, H, L, fx=1000.0) == pytest.approx(oracle.anti_pct(axle, brake, H, L, fx=-1000.0))


def _variants():
    cambered, toed = copy.deepcopy(VEHICLE), copy.deepcopy(VEHICLE)
    for axle in ("front", "rear"):
        cambered[axle]["wheel"]["camber_deg"] = -1.5
        toed[axle]["wheel"].update(camber_deg=-1.5, toe_deg=0.5)
    return [("design", VEHICLE), ("camber", cambered), ("camber_toe", toed)]


@pytest.mark.parametrize(("name", "vehicle"), _variants(), ids=[name for name, _ in _variants()])
@pytest.mark.parametrize("axle", ["front", "rear"])
@pytest.mark.parametrize("reaction", ["upright", "chassis"])
def test_statics_and_virtual_power_agree(name, vehicle, axle, reaction):
    corner = oracle.corner_from_vehicle(vehicle, axle)
    coeff, tensions, _ = oracle.statics(corner, 1.0, {"source": reaction}, {}, "source")
    assert np.all(np.isfinite(tensions))
    assert coeff == pytest.approx(oracle.virtual_power_coeff(corner, reaction), abs=1e-10)


def _cambered_toed_corner():
    camber, toe = math.radians(-2.0), math.radians(0.7)
    heading = np.array([math.cos(toe), math.sin(toe), 0.0])
    up = np.array([0.0, math.sin(camber), math.cos(camber)])
    r_wc = np.array([X_CP, 0.6, R])
    corner = oracle.corner_from_vehicle(VEHICLE, "rear")
    return oracle.Corner(corner.chassis, corner.upright, r_wc - R * up, r_wc, np.cross(heading, up) / np.linalg.norm(np.cross(heading, up)))


FORCE = np.array([1500.0, -900.0, 1200.0])


@pytest.mark.parametrize(
    "reactions",
    [
        {"drive": "chassis", "hydraulic_brake": "upright", "regen": "chassis"},
        {"drive": "upright", "hydraulic_brake": "upright", "regen": "upright"},
        {"drive": "chassis", "hydraulic_brake": "chassis", "regen": "chassis"},
    ],
    ids=["inboard-drive-outboard-brake", "all-upright", "all-chassis"],
)
def test_the_wrench_split_closes_in_3d(reactions):
    corner = _cambered_toed_corner()
    total = oracle.spin_torque(corner, FORCE)
    torques = {"drive": 0.5 * total, "hydraulic_brake": 0.3 * total, "regen": 0.2 * total}
    split = oracle.wrench_split(corner, FORCE, torques, reactions)
    scale = np.linalg.norm(FORCE) * 3.0
    assert split.spin_residual == pytest.approx(0.0, abs=1e-12 * scale)
    for about in (np.zeros(3), np.array([0.37, -1.2, 0.81])):
        assert np.max(np.abs(oracle.closure_residual(corner, split, FORCE, about))) < 1e-12 * scale
    held = sum(torques[source] for source, place in reactions.items() if place == "upright")
    assert np.dot(split.upright_moment, corner.spin) == pytest.approx(-held, abs=1e-12 * scale)


def test_a_missing_spin_torque_breaks_the_closure():
    corner = _cambered_toed_corner()
    total = oracle.spin_torque(corner, FORCE)
    split = oracle.wrench_split(corner, FORCE, {"drive": 0.6 * total}, {"drive": "chassis"})
    residual = oracle.closure_residual(corner, split, FORCE, np.zeros(3))
    assert np.linalg.norm(residual[3:]) == pytest.approx(abs(0.4 * total), rel=1e-12)


@pytest.mark.parametrize("axle", ["front", "rear"])
def test_a_zero_brake_torque_leaves_inboard_drive_jacking_unchanged(axle):
    corner = oracle.corner_from_vehicle(VEHICLE, axle)
    jacking = {}
    for brake in ("upright", "chassis"):
        reactions = {"drive": "chassis", "hydraulic_brake": brake}
        coeff, _, drive_torque = oracle.statics(corner, 1000.0, reactions, {"hydraulic_brake": 0.0}, "drive")
        jacking[brake] = coeff
        assert abs(drive_torque) == pytest.approx(VEHICLE[axle]["wheel"]["radius_m"] * 1000.0, rel=1e-9)
    assert jacking["upright"] == pytest.approx(jacking["chassis"], abs=1e-12)
    assert jacking["upright"] == pytest.approx(oracle.statics_coeff(corner, "chassis"), abs=1e-12)
    held_by_brake = oracle.statics(corner, 1000.0, {"drive": "chassis", "hydraulic_brake": "upright"}, {"drive": 0.0}, "hydraulic_brake")[0]
    assert abs(held_by_brake - jacking["upright"]) > 1e-3


@pytest.mark.parametrize("regen_share", [0.0, 0.35, 1.0])
def test_a_shared_spin_torque_gives_the_share_weighted_coefficient(regen_share):
    corner = oracle.corner_from_vehicle(VEHICLE, "rear")
    regen = regen_share * oracle.spin_torque(corner, 1000.0 * oracle.X)
    reactions = {"regen": "chassis", "hydraulic_brake": "upright"}
    blended = oracle.statics(corner, 1000.0, reactions, {"regen": regen}, "hydraulic_brake")[0]
    c_chassis, c_upright = oracle.statics_coeff(corner, "chassis"), oracle.statics_coeff(corner, "upright")
    assert blended == pytest.approx(regen_share * c_chassis + (1.0 - regen_share) * c_upright, abs=1e-12)
