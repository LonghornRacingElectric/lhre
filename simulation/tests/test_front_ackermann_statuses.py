import dataclasses
import math
import types

import numpy as np
import pytest

from tools.trim_status import BoundKind, LimitResult, TrimRecord, TrimStatus

G = 9.80665
OUTER_MAX = math.radians(40.0)
# Check case from the vehicle-dynamics-first-principles skill (seven-point tire sweep note).
SKILL_CAR = {"mass": 261.07265114, "cg_height": 0.284217678378, "wheelbase": 1.5494,
             "track_front": 1.2122215, "track_rear": 1.2122215, "front_static_frac": 0.46}


@pytest.fixture(scope="module")
def skill_car(make_car):
    return lambda lltd: make_car(lltd=lltd, **SKILL_CAR)


def test_loads_sum_to_weight_with_and_without_lift(skill_car):
    for lltd in (0.2, 0.4, 0.6):
        car = skill_car(lltd)
        weight = car.mass * G
        for ay in np.linspace(0.0, 2.5, 11):
            for decel in (-1.2, -0.6, 0.0, 0.6, 0.9):
                fz, _ = car.loads(ay, decel)
                assert sum(fz) == pytest.approx(weight, rel=1e-9), (lltd, ay, decel)
                assert min(fz) >= 0.0


def test_lift_caps_the_outer_wheel_at_the_axle_load(skill_car):
    car = skill_car(0.6)
    weight = car.mass * G
    front_axle, rear_axle = 0.46 * weight, 0.54 * weight
    fz, lifted = car.loads(2.0, 0.0)
    assert lifted == (True, False, False, False)
    assert fz[0] == pytest.approx(0.0, abs=1e-9)
    assert fz[1] == pytest.approx(front_axle, abs=1e-6)
    assert fz[1] == pytest.approx(1178.0, abs=0.5)
    roll = weight * 2.0 * car.cg_height
    rear_transfer = (roll - front_axle / 2 * car.track_front) / car.track_rear
    assert fz[3] == pytest.approx(rear_axle / 2 + rear_transfer, rel=1e-9)


def test_longitudinal_transfer_has_no_factor_of_two(skill_car):
    fz, lifted = skill_car(0.5).loads(0.0, -1.5)
    assert fz[1] == pytest.approx(236.6, abs=0.05)
    assert fz[3] == pytest.approx(1043.5, abs=0.05)
    assert not any(lifted)


@pytest.mark.parametrize("pct", [-50, 0, 50, 100])
@pytest.mark.parametrize("outer_deg", [2.0, 10.0, 25.0])
def test_constant_ackermann_reproduces_its_percentage(study, pct, outer_deg):
    law = study.ConstantAckermann(pct, 1.25, 1.55, OUTER_MAX)
    outer = math.radians(outer_deg)
    assert study.ackermann_pct(law(outer), outer, 1.25, 1.55) == pytest.approx(pct, abs=1e-9)


def test_table_curve_matches_its_source_law_at_the_nodes(study):
    law = study.ConstantAckermann(50, 1.25, 1.55, OUTER_MAX)
    outer = np.radians([0.0, 5.0, 10.0, 20.0, 30.0])
    table = np.column_stack([np.arange(5.0), [law(o) for o in outer], outer])
    curve = study.TableCurve(table)
    for o in outer[1:]:
        assert study.ackermann_pct(curve(o), o, 1.25, 1.55) == pytest.approx(50.0, abs=1e-9)


def test_table_curve_rejects_an_angle_outside_its_table(study):
    table = np.column_stack([[0.0, 10.0], np.radians([0.0, 12.0]), np.radians([0.0, 10.0])])
    curve = study.TableCurve(table)
    assert curve.domain == pytest.approx((0.0, math.radians(10.0)))
    with pytest.raises(study.SteeringDomainError, match="table_end"):
        curve(math.radians(10.5))
    shifted = study.ToeCurve(curve, 1.0)
    with pytest.raises(study.SteeringDomainError):
        shifted(math.radians(9.9))


def test_engine_sees_the_requested_steering_law(study, make_car, synthetic_tire):
    _, support = synthetic_tire
    car = make_car(support_override=dataclasses.replace(support, alpha=(-0.35, 0.35)))
    radius, b = 3.5, car.wheelbase * car.front_frac
    rear_axle_radius = math.sqrt(radius ** 2 - b ** 2)
    path_gap = (math.atan(car.wheelbase / (rear_axle_radius - car.track_front / 2))
                - math.atan(car.wheelbase / (rear_axle_radius + car.track_front / 2)))
    trims, limits = {}, {}
    for pct in (0, 100):
        law = study.ConstantAckermann(pct, car.track_front, car.wheelbase, OUTER_MAX)
        record = study.trim(car, law, radius, 1.0, 0.2, OUTER_MAX)
        assert record.accepted, record
        beta, outer = record.unknowns
        alphas = [w["alpha_rad"] for w in record.wheels]
        trims[pct] = (alphas[0] - alphas[1], law(outer) - outer)
        limits[pct] = study.limit_ay(car, law, radius, 1.0, OUTER_MAX)
    assert trims[0][1] == pytest.approx(0.0, abs=1e-12)
    assert math.degrees(trims[100][1]) == pytest.approx(math.degrees(path_gap), abs=0.1)
    assert math.degrees(trims[100][0] - trims[0][0]) == pytest.approx(math.degrees(path_gap), abs=0.25)
    assert all(limit.is_grip_limit for limit in limits.values())
    assert abs(limits[100].ay_g - limits[0].ay_g) > 0.05


def test_out_of_fit_slip_is_rejected_not_ranked(study, make_car):
    car = make_car()
    law = study.ConstantAckermann(-50, car.track_front, car.wheelbase, OUTER_MAX)
    record = study.trim(car, law, 3.5, 1.0, 0.2, OUTER_MAX)
    assert record.status is TrimStatus.DOMAIN_REJECTED
    assert "alpha_out_of_fit:FR" in record.reasons
    limit = study.limit_ay(car, law, 3.5, 1.0, OUTER_MAX)
    assert limit.bound_kind is BoundKind.DOMAIN and limit.grip_g() is None


def test_a_wheel_below_the_load_fit_is_rejected(study, make_car, synthetic_tire):
    _, support = synthetic_tire
    car = make_car(support_override=dataclasses.replace(support, fz=(500.0, 2000.0)))
    law = study.ConstantAckermann(100, car.track_front, car.wheelbase, OUTER_MAX)
    record = study.trim(car, law, 3.5, 1.0, 0.8, OUTER_MAX)
    assert record.status is TrimStatus.DOMAIN_REJECTED
    assert "fz_out_of_fit:RL" in record.reasons


@pytest.mark.parametrize("side, below_fit", [(1.0, {"FL", "RL"}), (-1.0, {"FR", "RR"})])
def test_negative_camber_leaves_a_positive_only_camber_fit_on_one_side(study, make_car, synthetic_tire, side, below_fit):
    _, support = synthetic_tire
    car = make_car(camber_deg=-1.0, support_override=dataclasses.replace(support, gamma=(0.0, 0.0698)))
    law = study.ConstantAckermann(0, car.track_front, car.wheelbase, OUTER_MAX)
    record = study.trim(car, law, 8.0, side, 0.3, OUTER_MAX)
    assert record.status is TrimStatus.DOMAIN_REJECTED
    assert {r.split(":")[1] for r in record.reasons if r.startswith("camber_out_of_fit")} == below_fit


def test_peak_tables_stay_inside_the_load_fit(make_car, synthetic_tire):
    _, support = synthetic_tire
    car = make_car()
    assert car.fz_table[0] == support.fz[0] and car.fz_table[-1] == support.fz[1]
    assert all(k <= support.kappa[1] + 1e-12 for table in car.kappa_peak[1] for k in table)


def test_a_steering_bound_is_a_domain_limit_not_grip(study, make_car):
    car = make_car()
    lock = math.radians(5.0)
    law = study.ConstantAckermann(100, car.track_front, car.wheelbase, lock)
    limit = study.limit_ay(car, law, 3.5, 1.0, lock)
    assert limit.bound_kind is BoundKind.DOMAIN
    assert "steer_bound" in limit.upper.reasons
    assert not limit.is_grip_limit


def law_table(law, outer_max_deg=40.0):
    outer = np.radians(np.arange(0.0, outer_max_deg + 0.25, 0.5))
    return np.column_stack([np.degrees(outer), [law(o) for o in outer], outer])


def test_the_design_curve_ends_at_the_rack_stop(study):
    rack = np.arange(0.0, 60.5, 0.5)
    table = np.column_stack([rack, np.radians(0.7 * rack), np.radians(0.6 * rack)])
    curve = study.TableCurve(*study.to_rack_stop(table, 31.75))
    assert curve.domain[1] == pytest.approx(math.radians(0.6 * 31.75))
    assert curve(curve.domain[1]) == pytest.approx(math.radians(0.7 * 31.75))
    with pytest.raises(study.SteeringDomainError, match="rack_stop"):
        curve(math.radians(0.6 * 32.0))
    assert study.to_rack_stop(table, 70.0)[1] == "table_end"


def test_a_design_trim_past_the_rack_stop_is_a_domain_limit(study, make_car):
    car = make_car()
    table = law_table(study.ConstantAckermann(100, car.track_front, car.wheelbase, OUTER_MAX))
    assert study.limit_ay(car, study.TableCurve(table), 3.5, 1.0, OUTER_MAX).is_grip_limit
    stopped = study.limit_ay(car, study.TableCurve(*study.to_rack_stop(table, 10.0)), 3.5, 1.0, OUTER_MAX)
    assert stopped.bound_kind is BoundKind.DOMAIN and not stopped.is_grip_limit
    assert "steer_bound" in stopped.upper.reasons


def test_lock_margin_is_measured_to_the_bound_of_the_law(study, make_car):
    car = make_car()
    table = law_table(study.ConstantAckermann(50, car.track_front, car.wheelbase, OUTER_MAX))
    curve = study.TableCurve(*study.to_rack_stop(table, 25.0))
    case = {"car": car, "curves": {study.BASELINE: curve}, "radius": 8.0, "lon": None, "outer_max": OUTER_MAX, "tol": 1e-3,
            "probe": False, "deriv": False, "labels": {"mu": 1.0, "rear": "open", "radius_m": 8.0, "ax_g": 0.0}}
    row = study.solve_map_case(case)[0]
    assert row["ay_max_g"] is not None
    assert row["lock_margin_deg"] == pytest.approx(25.0 - row["outer_deg"])


def test_an_iteration_cap_is_a_numerical_failure(study, make_car):
    car = make_car()
    law = study.ConstantAckermann(100, car.track_front, car.wheelbase, OUTER_MAX)
    record = study.trim(car, law, 3.5, 1.0, 0.2, OUTER_MAX, max_nfev=1)
    assert record.status is TrimStatus.NUMERICAL_FAILURE
    assert record.reasons == ("max_evaluations",)


def stationary(beta, outer):
    return types.SimpleNamespace(x=np.array([beta, outer]), fun=np.array([0.05, 0.0]), active_mask=np.zeros(2, dtype=int),
                                 status=1, optimality=1e-12)


@pytest.mark.parametrize("status, optimality, reason", [(0, 1.0, "max_evaluations"), (-1, 1.0, "solver_error"),
                                                        (2, 1.0, "not_converged")])
def test_a_solver_stop_on_a_bound_is_a_numerical_failure(study, make_car, status, optimality, reason):
    car = make_car()
    law = study.ConstantAckermann(0, car.track_front, car.wheelbase, OUTER_MAX)
    res = types.SimpleNamespace(x=np.array([0.1, 0.2]), fun=np.array([0.3, 0.0]), active_mask=np.array([1, 0]),
                                status=status, optimality=optimality)
    record = study.classify(car, law, 3.5, 1.0, 0.5, None, res)
    assert record.status is TrimStatus.NUMERICAL_FAILURE
    assert record.reasons == (reason,)


def test_a_converged_stop_on_a_bound_is_a_domain_rejection(study, make_car):
    car = make_car()
    law = study.ConstantAckermann(0, car.track_front, car.wheelbase, OUTER_MAX)
    res = types.SimpleNamespace(x=np.array([0.1, 0.2]), fun=np.array([0.3, 0.0]), active_mask=np.array([0, 1]),
                                status=3, optimality=1e-12)
    record = study.classify(car, law, 3.5, 1.0, 0.5, None, res)
    assert record.status is TrimStatus.DOMAIN_REJECTED
    assert record.reasons == ("steer_bound",)


def test_no_equilibrium_counts_as_physical_only_inside_the_fit(study, make_car):
    car = make_car()
    radius, ay = 3.5, 1.2
    law = study.ConstantAckermann(0, car.track_front, car.wheelbase, math.radians(60.0))
    beta, probe = car.wheelbase * car.front_frac / radius, 0.5
    path = [probe - a for a in study.state(car, law, radius, 1.0, beta, probe, ay)["alphas"][:2]]
    inside, outside = sum(path) / 2, max(path) + 0.25

    def flags(outer):
        return [f for w in car.wheel_states(study.state(car, law, radius, 1.0, beta, outer, ay), 1.0) for f in w["flags"]]

    assert flags(inside) == [] and "alpha_out_of_fit:FL" in flags(outside)
    record = study.classify(car, law, radius, 1.0, ay, None, stationary(beta, inside))
    assert record.status is TrimStatus.PHYSICALLY_INFEASIBLE and record.reasons == ("no_equilibrium",)
    record = study.classify(car, law, radius, 1.0, ay, None, stationary(beta, outside))
    assert record.status is TrimStatus.DOMAIN_REJECTED
    assert {"alpha_out_of_fit:FL", "alpha_out_of_fit:FR"} <= set(record.reasons)


def test_a_grip_limit_ends_at_physical_saturation(study, make_car):
    car = make_car()
    law = study.ConstantAckermann(100, car.track_front, car.wheelbase, OUTER_MAX)
    limit = study.limit_ay(car, law, 3.5, 1.0, OUTER_MAX)
    assert limit.is_grip_limit
    assert limit.upper_status is TrimStatus.PHYSICALLY_INFEASIBLE
    assert limit.record.accepted and limit.record.wheels


def test_locked_lsd_slip_is_checked_against_the_fit(study, make_car):
    car = make_car()
    law = study.ConstantAckermann(100, car.track_front, car.wheelbase, OUTER_MAX)
    lon = study.Longitudinal(-0.3, diff=(500.0, 0.0, 0.85), wheel_radius=0.2)
    s = study.state(car, law, 3.5, 1.0, 0.0, math.radians(20.0), 0.5, lon, 800.0)
    assert s["kappas"][2] > car.support.kappa[1]
    flags = car.wheel_states(s, 1.0)[2]["flags"]
    assert "kappa_out_of_fit:RL" in flags


def slip_norm_car(make_car):
    car = make_car()
    car.combined = "slip_norm"
    return car


def test_slip_norm_reports_the_model_slip_and_checks_the_evaluated_slips(make_car):
    car = slip_norm_car(make_car)
    fz, alpha = 800.0, math.radians(11.0)
    fx = -0.9 / car.slip_norm_point(0, fz, alpha, -1.0, 1.0).use
    point = car.slip_norm_point(0, fz, alpha, fx, 1.0)
    s = {"loads": (fz,) * 4, "alphas": (alpha, 0.0, 0.0, 0.0), "wheel_fx": (fx, 0.0, 0.0, 0.0), "kappas": (None,) * 4,
         "lifted": (False,) * 4, "brake_use": (point.use, 0.0, 0.0, 0.0)}
    wheel = car.wheel_states(s, 1.0)[0]
    assert point.kappa == pytest.approx(-0.154, abs=0.002)
    assert wheel["kappa"] == pytest.approx(point.kappa)
    assert {"kappa_eval_out_of_fit:FL", "alpha_eval_out_of_fit:FL"} <= set(wheel["flags"])


def test_a_slip_norm_braking_trim_evaluated_outside_the_fit_is_rejected(study, make_car):
    car = slip_norm_car(make_car)
    law = study.ConstantAckermann(0, car.track_front, car.wheelbase, OUTER_MAX)
    # Above this car's in-fit braking limit, below the limit found without the evaluated-slip check.
    record = study.trim(car, law, 3.5, 1.0, 1.275, OUTER_MAX, lon=study.Longitudinal(0.5, bias=0.5))
    assert record.status is TrimStatus.DOMAIN_REJECTED
    assert {"kappa_eval_out_of_fit:RL", "alpha_eval_out_of_fit:RL"} <= set(record.reasons)


def test_braking_is_open_axle_and_lsd_braking_is_not_modeled(study, make_car):
    car = make_car()
    law = study.ConstantAckermann(100, car.track_front, car.wheelbase, OUTER_MAX)
    brakes = study.Longitudinal(0.3, bias=0.65)
    assert brakes.coupling == "open_axle" and not brakes.withheld
    assert "open axle" in study.brake_model(0.65) and "lsd" not in study.brake_model(0.65).lower()
    lsd = study.Longitudinal(0.3, diff=(5.0, 0.6, 0.85), wheel_radius=0.2)
    assert lsd.withheld
    assert study.trim(car, law, 3.5, 1.0, 0.3, OUTER_MAX, lon=lsd).status is TrimStatus.NOT_MODELED
    limit = study.limit_ay(car, law, 3.5, 1.0, OUTER_MAX, lon=lsd)
    assert limit.bound_kind is BoundKind.NOT_MODELED and limit.ay_g is None
    assert not study.Longitudinal(0.3, diff=(0.0, 0.0, 0.85), wheel_radius=0.2).withheld


def test_map_rows_record_status_and_wheel_states(study, make_car):
    car = make_car()
    curves = {name: study.ConstantAckermann(p, car.track_front, car.wheelbase, OUTER_MAX)
              for name, p in ((study.BASELINE, 0), ("+100%", 100))}
    case = {"car": car, "curves": curves, "radius": 4.5, "lon": study.Longitudinal(0.3, bias=0.65), "outer_max": OUTER_MAX,
            "tol": 1e-3, "probe": False, "deriv": False,
            "labels": {"mu": 1.0, "rear": study.brake_model(0.65), "radius_m": 4.5, "ax_g": -0.3}}
    rows = study.solve_map_case(case)
    for row in rows:
        assert row["driveline_coupling"] == "open_axle"
        assert row["trim_status"] in {s.value for s in TrimStatus}
        assert row["bound_kind"] in {k.value for k in BoundKind}
        if row["ay_accepted_g"] is not None:
            for corner in study.CORNERS:
                for key in (f"fz_{corner}_n", f"alpha_{corner}_deg", f"kappa_{corner}", f"gamma_{corner}_deg",
                            f"use_{corner}", f"support_{corner}"):
                    assert key in row
        if row["bound_kind"] != "physical":
            assert row["ay_max_g"] is None and row["ay_change_pct"] is None


def test_lltd_balance_refuses_a_failed_limit(study, make_ggv, synthetic_tire, monkeypatch):
    tire, support = synthetic_tire
    failed = TrimRecord(TrimStatus.NUMERICAL_FAILURE, ("not_converged",))
    monkeypatch.setattr(study, "limit_ay", lambda *a, **k: LimitResult(None, BoundKind.NUMERICAL, TrimStatus.NUMERICAL_FAILURE, upper=failed))
    law = study.ConstantAckermann(0, 1.25, 1.55, OUTER_MAX)
    with pytest.raises(RuntimeError, match="not a limit"):
        study.balanced_lltd(make_ggv(), tire, law, OUTER_MAX, support)


def map_row(study, curve, ay, kind="physical", status="accepted", radius=3.5, ax=0.0, rear="open"):
    accepted = None if status != "accepted" else ay
    return {"mu": 0.623, "rear": rear, "radius_m": radius, "ax_g": ax, "curve": curve,
            "ay_max_g": ay if kind == "physical" and status == "accepted" else None,
            "ay_accepted_g": accepted, "bound_kind": kind, "trim_status": status, "upper_reason": ""}


def test_robust_region_without_common_support_names_no_law(study):
    rows = [map_row(study, study.BASELINE, 1.0), map_row(study, "+50%", 1.05), map_row(study, "-50%", 0.9, kind="domain"),
            map_row(study, study.BASELINE, 1.0, radius=4.5), map_row(study, "+50%", 1.02, radius=4.5),
            map_row(study, "-50%", None, kind="numerical", status="numerical_failure", radius=4.5)]
    robust = study.robust_region(rows)
    assert robust["status"] == "withheld" and robust["n_supported"] == 0
    text = study.region_text(robust)
    assert text.startswith("No recommendation")
    assert "+50%" not in text and "-50%" not in text
    assert all(v["worst_shortfall_pct"] != 100.0 for v in robust["laws"].values())


def test_best_law_map_withholds_a_cell_with_a_failure(study):
    inputs = type("Inputs", (), {"mu": 0.623, "map_radii_m": (3.5,), "map_ax_g": (0.0,)})()
    rows = [map_row(study, study.BASELINE, 1.0)] + [map_row(study, f"{p:+d}%", 1.0 + p / 1000) for p in study.MAP_PCTS]
    assert study.best_laws(rows, inputs)[(3.5, 0.0)][0] == 100
    rows[1] = map_row(study, "-50%", None, kind="domain", status="domain_rejected")
    assert study.best_laws(rows, inputs)[(3.5, 0.0)] == (None, None)
    assert study.best_text(None, None) == "withheld"


def test_toe_summary_never_picks_a_failed_law(study):
    rows = [{"toe_out_deg": 0.0, "radius_m": 3.5, "curve": c, "ay_max_g": ay}
            for c, ay in ((study.BASELINE, 1.0), ("+10%", None), ("+20%", 1.01))]
    entry = study.toe_summary(rows)["toe-out 0 deg, 3.5 m"]
    assert entry["best_curve"] is None and entry["excluded"] == ["+10%"]


def test_envelope_caps_only_physical_failures(study):
    inputs = type("Inputs", (), {"map_radii_m": (3.5,), "map_ax_g": (-0.3, 0.0, 0.3, 0.6)})()
    base = [map_row(study, "+0%", 1.0, ax=0.0), map_row(study, "+0%", 0.9, ax=-0.3), map_row(study, "+0%", 0.8, ax=0.3)]
    infeasible = map_row(study, "+0%", None, kind="physical", status="physically_infeasible", ax=0.6)
    caps, excluded = study.check_map(base + [infeasible], "+0%", 1.0, 1.0, inputs)
    assert [c["cap_g"] for c in caps] == [0.3] and not excluded
    domain = map_row(study, "+0%", None, kind="domain", status="domain_rejected", ax=0.6)
    caps, excluded = study.check_map(base + [domain], "+0%", 1.0, 1.0, inputs)
    assert not caps and excluded[0]["bound_kind"] == "domain"


def test_limiting_axle_is_unknown_without_both_probes(study):
    assert study.limiting_axle(None, 0.01) is None
    assert study.limiting_axle(0.02, 0.01) == "front"
