import csv
import hashlib
import json

import pytest

from tools import four_post_acceptance as acceptance

METRICS = "generated_results/four_post_eval_report_metrics.csv"
MANIFEST = "runs/FourPostSim/run_1/manifest.json"
CONFIG = "four_post_eval_config.yml"
VEHICLE = """\
vehicle:
  name: TestCar
front:
  actuation:
    shock:
      spring_table:
        table: [[0, 0], [1, 1000.0]]
    stabar:
      rate_n_m_per_rad: 250.0
rear:
  actuation:
    shock:
      spring_table:
        table: [[0, 0], [1, 2000.0]]
    stabar:
      rate_n_m_per_rad: 535.0
"""
EXECUTED_SETUP = {
    "heaveMagnitude": 0.03,
    "pVehicle.pFrAxleDW.springFreeLength": 0.2,
    "pFrStabar.barRate": 250.0,
    "pVehicle.pFrStabar.barRate": 250.0,
    "pRrStabar.barRate": 535.0,
    "pVehicle.pRrStabar.barRate": 535.0,
    "pVehicle.pFrAxleDW.springTable[1,1]": 0.0,
    "pVehicle.pFrAxleDW.springTable[1,2]": 0.0,
    "pVehicle.pFrAxleDW.springTable[2,1]": 1.0,
    "pVehicle.pFrAxleDW.springTable[2,2]": 1000.0,
    "pVehicle.pRrAxleDW.springTable[1,1]": 0.0,
    "pVehicle.pRrAxleDW.springTable[1,2]": 0.0,
    "pVehicle.pRrAxleDW.springTable[2,1]": 1.0,
    "pVehicle.pRrAxleDW.springTable[2,2]": 2000.0,
}
FE03BAB_BAR_RATES = {
    "pFrStabar.barRate": 390.0,
    "pVehicle.pFrStabar.barRate": 390.0,
    "pRrStabar.barRate": 525.0,
    "pVehicle.pRrStabar.barRate": 525.0,
}
FE03BAB_CONFIG = """\
standard: FourPostEval
model_overrides:
  suspension:
    static_balance_tolerance_n: 5.0
    static_balance_tolerance_pct: 0.5
    front:
      spring_rate_n_per_m: 26269.02525
      stabar_rate_n_m_per_rad: 390
    rear:
      spring_rate_n_per_m: 43781.70875
      stabar_rate_n_m_per_rad: 525
procedure:
  heaveMagnitude: 0.03
"""
CONFIG_WITHOUT_OVERRIDES = """\
procedure:
  heaveMagnitude: 0.03
"""
FE03BAB_KEYS = (
    "arb_roll_stiffness_front_Nm_per_rad", "arb_roll_stiffness_rear_Nm_per_rad", "avg_anti_dive_pct",
    "avg_anti_roll_front_pct", "avg_anti_roll_rear_pct", "avg_anti_squat_pct",
    "avg_antiroll_geometry_lltd_delta_pct", "avg_fbrc_differential_jacking_ratio_front",
    "avg_fbrc_differential_jacking_ratio_rear", "avg_fbrc_equivalent_height_front_m",
    "avg_fbrc_equivalent_height_rear_m", "avg_lateral_jacking_coeff_front", "avg_lateral_jacking_coeff_rear",
    "avg_lltd_front_frac", "avg_lltd_front_pct", "avg_longitudinal_jacking_coeff_front",
    "avg_longitudinal_jacking_coeff_rear", "avg_motion_ratio_front", "avg_motion_ratio_rear",
    "avg_roll_rate_distribution_front_pct", "avg_stabar_motion_ratio_front", "avg_stabar_motion_ratio_rear",
    "camber_gain_heave_rad_per_m", "camber_gain_roll_rad_per_rad", "caster_gain_heave_rad_per_m",
    "caster_gain_roll_rad_per_rad", "elastic_roll_stiffness_front_Nm_per_rad",
    "elastic_roll_stiffness_rear_Nm_per_rad", "kpi_gain_heave_rad_per_m", "kpi_gain_roll_rad_per_rad",
    "minimum_roll_pulse_contact_fz_n", "roll_contact_rejected_points", "roll_contact_valid_points",
    "scrub_gain_heave_m_per_m", "scrub_gain_roll_m_per_rad", "spring_roll_stiffness_front_Nm_per_rad",
    "spring_roll_stiffness_rear_Nm_per_rad", "static_balance_iterations", "static_balance_max_abs_fz_error_n",
    "static_balance_max_abs_fz_error_pct", "static_balance_pass", "static_motion_ratio_front",
    "static_motion_ratio_rear", "toe_gain_heave_rad_per_m", "toe_gain_roll_rad_per_rad",
    "trail_gain_heave_m_per_m", "trail_gain_roll_m_per_rad",
)
DRIFT_REASON = "closure 4.10e-03 of load scale exceeds 1e-04 at t = 118 s"
CORNER_REASON = "corner load differs from target by 12.3 N"
SPRING_REASON = "spring force differs from the executed table by 130 N"


class _Coefficients:
    def __init__(self, value, tolerance=0.0):
        self.values = {"contact_patch_path": value}
        self.tolerance = {"contact_patch_path": tolerance}


@pytest.fixture(autouse=True)
def _kinpy_matches_the_rows(monkeypatch):
    monkeypatch.setattr(acceptance, "_kinpy_coefficients", lambda vehicle: {axle: _Coefficients(1.5) for axle in acceptance.AXLES})


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _legacy_rows():
    """Metrics CSV of FourPost at BobSim fe03bab: no status column, target round-trip static balance."""
    rows = {key: "1.5" for key in FE03BAB_KEYS}
    rows.update(static_balance_pass="True", static_balance_max_abs_fz_error_n="0.0")
    return rows


def _measured_rows(*, status="fail", reason=DRIFT_REASON, closure="False", corner_n="0.9", spring_n="1e-12"):
    """Metrics CSV of FourPost after the measured static balance: same keys, measured balance keys."""
    rows = {key: "1.5" for key in FE03BAB_KEYS if key != "static_balance_iterations"}
    rows.update({
        "static_balance_status": status,
        "static_balance_reason": reason,
        "static_balance_pass": {"pass": "True", "fail": "False"}.get(status, ""),
        "static_balance_max_abs_fz_error_n": corner_n,
        "static_balance_max_abs_fz_error_pct": "0.13",
        "static_equilibrium_pass": closure,
        "static_equilibrium_max_force_residual_n": "4.39",
        "static_equilibrium_max_moment_residual_nm": "7.5",
        "static_equilibrium_max_relative_residual": "0.0041",
        "static_equilibrium_worst_time_s": "118.0",
        "static_equilibrium_rows": "60",
        "static_spring_max_residual_n": spring_n,
    })
    return rows


def _labelled_rows(**kwargs):
    """The measured CSV after the metric-label change: new rows, same keys, and a status column."""
    rows = _measured_rows(**kwargs)
    rows.update(heave_cp_tension_points="2", anti_force_path="contact_patch_upright_reacted")
    return rows


def _write_run(
    tmp_path,
    rows,
    *,
    statuses=None,
    target="standard-eval-four-post",
    status="ok",
    case=None,
    config=CONFIG_WITHOUT_OVERRIDES,
    bundle=None,
):
    vehicle = tmp_path / "vehicle.yml"
    if not vehicle.exists():
        vehicle.write_text(VEHICLE, encoding="utf-8")
    run = tmp_path / "out" / "car" / target
    metrics = run / METRICS
    metrics.parent.mkdir(parents=True, exist_ok=True)
    with metrics.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["metric", "value"] + (["status"] if statuses is not None else []))
        for key, value in rows.items():
            writer.writerow([key, value] + ([statuses.get(key, "ok")] if statuses is not None else []))
    manifest = run / MANIFEST
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"status": "succeeded", "case": case or EXECUTED_SETUP}), encoding="utf-8")
    outputs = [METRICS, MANIFEST]
    if config is not None:
        (run / CONFIG).write_text(config, encoding="utf-8")
        outputs.append(CONFIG)
    identity = {
        "schema": "lhre.car-run.v1",
        "status": status,
        "failures": [] if status == "ok" else ["make bobsim failed."],
        "target": target,
        "target_exit_status": 0 if status == "ok" else 2,
        "vehicle": {"path": "vehicle/vehicle.yml", "sha256": _sha256(vehicle)},
        "bundle": bundle or {"bundle_id": "test-bundle-1", "status": "candidate", "describes_vehicle": True},
        "records": {"state": "written", "executables": []},
        "bobsim": {"commit": "fe03bab", "clean_after": True},
        "outputs": [{"path": name, "sha256": _sha256(run / name)} for name in outputs],
    }
    (run / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
    return run, vehicle


def _accept(run, vehicle, monkeypatch):
    monkeypatch.setenv("BOBSIM_VEHICLE", str(vehicle))
    code = acceptance.main(["four_post_acceptance.py", str(run)])
    report = json.loads((run / "four_post_acceptance.json").read_text(encoding="utf-8"))
    return code, {check["name"]: check for check in report["checks"]}, report


def _results(checks):
    return {name: check["result"] for name, check in checks.items()}


def test_a_fe03bab_run_is_accepted_with_visible_known_upstream_and_unavailable_checks(tmp_path, monkeypatch, capsys):
    case = {**EXECUTED_SETUP, **FE03BAB_BAR_RATES}
    run, vehicle = _write_run(tmp_path, _legacy_rows(), case=case, config=FE03BAB_CONFIG)
    code, checks, report = _accept(run, vehicle, monkeypatch)
    assert code == 0
    assert report["result"] == "accepted"
    assert report["schema"] == "lhre.four-post-acceptance.v2"
    assert checks["executed bar rates"]["result"] == "KNOWN-UPSTREAM"
    assert "BobSim #117" in checks["executed bar rates"]["detail"]
    assert "pFrStabar.barRate ran at 390.0" in checks["executed bar rates"]["detail"]
    assert checks["executed spring tables"]["result"] == "PASS"
    assert checks["static balance"]["result"] == "UNAVAILABLE"
    assert checks["static balance"]["detail"].startswith("unavailable: legacy tautology")
    assert checks["metric statuses"]["result"] == "UNAVAILABLE"
    assert checks["vehicle stamp"]["result"] == "UNAVAILABLE"
    assert "PASS" not in {checks[name]["result"] for name in ("static balance", "executed bar rates")}
    printed = capsys.readouterr().out
    assert "KNOWN-UPSTREAM" in printed and "BobSim #117" in printed
    assert "unavailable: legacy tautology" in printed
    assert "Known upstream defects: 1 of" in printed
    assert "not a pass" in printed


def test_a_legacy_static_balance_failure_fails(tmp_path, monkeypatch):
    rows = _legacy_rows()
    rows["static_balance_pass"] = "False"
    run, vehicle = _write_run(tmp_path, rows)
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["static balance"]["result"] == "FAIL"


def test_a_csv_without_static_balance_is_unavailable(tmp_path, monkeypatch):
    rows = _legacy_rows()
    del rows["static_balance_pass"]
    run, vehicle = _write_run(tmp_path, rows)
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 0
    assert checks["static balance"]["result"] == "UNAVAILABLE"


def test_bar_rates_that_differ_fail_when_the_config_has_no_overrides(tmp_path, monkeypatch):
    case = {**EXECUTED_SETUP, **FE03BAB_BAR_RATES}
    run, vehicle = _write_run(tmp_path, _legacy_rows(), case=case, config=CONFIG_WITHOUT_OVERRIDES)
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["executed bar rates"]["result"] == "FAIL"
    assert "pRrStabar.barRate ran at 525.0" in checks["executed bar rates"]["detail"]


def test_bar_rates_that_match_neither_the_vehicle_nor_the_config_fail(tmp_path, monkeypatch):
    case = {**EXECUTED_SETUP, **FE03BAB_BAR_RATES, "pFrStabar.barRate": 400.0}
    run, vehicle = _write_run(tmp_path, _legacy_rows(), case=case, config=FE03BAB_CONFIG)
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["executed bar rates"]["result"] == "FAIL"


def test_bar_rates_that_differ_fail_when_the_run_did_not_record_the_config(tmp_path, monkeypatch):
    case = {**EXECUTED_SETUP, **FE03BAB_BAR_RATES}
    run, vehicle = _write_run(tmp_path, _legacy_rows(), case=case, config=None)
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["executed bar rates"]["result"] == "FAIL"
    assert "did not record" in checks["executed bar rates"]["detail"]


def test_a_config_that_changed_after_the_run_is_not_trusted(tmp_path, monkeypatch):
    case = {**EXECUTED_SETUP, **FE03BAB_BAR_RATES}
    run, vehicle = _write_run(tmp_path, _legacy_rows(), case=case, config=CONFIG_WITHOUT_OVERRIDES)
    (run / CONFIG).write_text(FE03BAB_CONFIG, encoding="utf-8")
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["executed bar rates"]["result"] == "FAIL"


def test_vehicle_bar_rates_pass_even_when_the_config_has_overrides(tmp_path, monkeypatch):
    run, vehicle = _write_run(tmp_path, _legacy_rows(), config=FE03BAB_CONFIG)
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 0
    assert checks["executed bar rates"]["result"] == "PASS"


def test_a_run_that_does_not_show_a_bar_rate_is_unavailable(tmp_path, monkeypatch):
    case = {key: value for key, value in EXECUTED_SETUP.items() if "RrStabar" not in key}
    run, vehicle = _write_run(tmp_path, _legacy_rows(), case=case)
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 0
    assert checks["executed bar rates"]["result"] == "UNAVAILABLE"


def test_a_run_that_does_not_show_the_spring_tables_is_unavailable(tmp_path, monkeypatch):
    case = {key: value for key, value in EXECUTED_SETUP.items() if "springTable" not in key}
    run, vehicle = _write_run(tmp_path, _legacy_rows(), case=case)
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 0
    assert checks["executed spring tables"]["result"] == "UNAVAILABLE"


@pytest.mark.parametrize("statuses", [None, {}], ids=["measured", "measured-with-status"])
def test_a_measured_static_balance_pass_passes(tmp_path, monkeypatch, statuses):
    rows = (_labelled_rows if statuses is not None else _measured_rows)(status="pass", reason="", closure="True")
    run, vehicle = _write_run(tmp_path, rows, statuses=statuses)
    code, checks, report = _accept(run, vehicle, monkeypatch)
    assert code == 0, report
    results = _results(checks)
    assert results["static balance: corner loads"] == "PASS"
    assert results["static balance: springs"] == "PASS"
    assert results["static balance: closure"] == "PASS"
    assert "static balance" not in results


@pytest.mark.parametrize("statuses", [None, {}], ids=["measured", "measured-with-status"])
def test_a_closure_failure_from_the_time_drift_alone_is_known_upstream(tmp_path, monkeypatch, capsys, statuses):
    rows = _labelled_rows() if statuses is not None else _measured_rows()
    run, vehicle = _write_run(tmp_path, rows, statuses=statuses)
    code, checks, report = _accept(run, vehicle, monkeypatch)
    assert code == 0, report
    assert report["result"] == "accepted"
    drift = checks["static balance: closure drift"]
    assert drift["result"] == "KNOWN-UPSTREAM"
    assert "closure residual grows with simulated time" in drift["detail"]
    assert "t = 118 s" in drift["detail"]
    assert checks["static balance: design-pose closure"]["result"] == "UNAVAILABLE"
    assert checks["static balance: corner loads"]["result"] == "PASS"
    assert checks["static balance: springs"]["result"] == "PASS"
    assert "closure residual grows with simulated time" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("reason", "kwargs", "failed"),
    [
        (f"{DRIFT_REASON}; {CORNER_REASON}", {"corner_n": "12.3"}, "static balance: corner loads"),
        (CORNER_REASON, {"closure": "True", "corner_n": "12.3"}, "static balance: corner loads"),
        (f"{DRIFT_REASON}; {SPRING_REASON}", {"spring_n": "130"}, "static balance: springs"),
        (f"{DRIFT_REASON}; non-finite sample: frKnC.heave", {}, "static balance"),
        (f"{DRIFT_REASON}; a check that BobSim adds later", {}, "static balance"),
        ("", {}, "static balance"),
    ],
    ids=["corner-and-drift", "corner-only", "spring", "non-finite", "unknown-clause", "no-reason"],
)
def test_a_measured_static_balance_failure_beyond_the_drift_fails(tmp_path, monkeypatch, reason, kwargs, failed):
    rows = _labelled_rows(reason=reason, **kwargs)
    run, vehicle = _write_run(tmp_path, rows, statuses={})
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks[failed]["result"] == "FAIL"


@pytest.mark.parametrize("corner_n", ["", "nan"])
def test_a_measured_corner_error_without_a_finite_value_fails(tmp_path, monkeypatch, corner_n):
    run, vehicle = _write_run(tmp_path, _measured_rows(corner_n=corner_n))
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["static balance: corner loads"]["result"] == "FAIL"


@pytest.mark.parametrize(
    ("closure", "reason"),
    [("True", DRIFT_REASON), ("False", CORNER_REASON), ("", DRIFT_REASON)],
    ids=["pass-with-closure-reason", "fail-without-closure-reason", "no-closure-value"],
)
def test_static_balance_keys_that_disagree_fail(tmp_path, monkeypatch, closure, reason):
    run, vehicle = _write_run(tmp_path, _measured_rows(closure=closure, reason=reason, corner_n="12.3"))
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["static balance: closure"]["result"] == "FAIL"


def test_an_unavailable_measured_static_balance_fails(tmp_path, monkeypatch):
    rows = _labelled_rows(status="unavailable", reason="missing signal: frChassisActuator.sprungLoads.frame_a.f[1]")
    for key in acceptance.BALANCE_METRICS:
        if key not in ("static_balance_status", "static_balance_reason"):
            rows[key] = ""
    statuses = {key: "unavailable" for key in rows if key.startswith(("static_balance_", "static_equilibrium_"))}
    statuses.update(static_balance_status="ok", static_balance_reason="ok")
    run, vehicle = _write_run(tmp_path, rows, statuses=statuses)
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["static balance"]["result"] == "FAIL"
    assert "missing signal" in checks["static balance"]["detail"]
    assert checks["metric statuses"]["result"] == "FAIL"


def test_a_non_ok_status_of_a_checked_metric_fails(tmp_path, monkeypatch):
    run, vehicle = _write_run(tmp_path, _labelled_rows(), statuses={"avg_lltd_front_pct": "partial"})
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["metric statuses"]["result"] == "FAIL"
    assert "avg_lltd_front_pct=partial" in checks["metric statuses"]["detail"]


def test_a_non_ok_status_of_an_unchecked_metric_is_shown_but_does_not_fail(tmp_path, monkeypatch):
    statuses = {"avg_fbrc_equivalent_height_front_m": "partial"}
    run, vehicle = _write_run(tmp_path, _labelled_rows(), statuses=statuses)
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 0
    assert checks["metric statuses"]["result"] == "PASS"
    assert "avg_fbrc_equivalent_height_front_m=partial" in checks["metric statuses"]["detail"]


def test_a_missing_metric_fails(tmp_path, monkeypatch):
    rows = _legacy_rows()
    del rows["avg_anti_squat_pct"]
    run, vehicle = _write_run(tmp_path, rows)
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["required metrics"]["result"] == "FAIL"
    assert "avg_anti_squat_pct" in checks["required metrics"]["detail"]


@pytest.mark.parametrize("value", ["nan", "", "inf", "n/a"])
def test_a_metric_without_a_finite_value_fails(tmp_path, monkeypatch, value):
    rows = _legacy_rows()
    rows["avg_lltd_front_pct"] = value
    run, vehicle = _write_run(tmp_path, rows)
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert "avg_lltd_front_pct" in checks["required metrics"]["detail"]


def test_a_matching_vehicle_stamp_passes(tmp_path, monkeypatch):
    (tmp_path / "vehicle.yml").write_text(VEHICLE, encoding="utf-8")
    rows = {**_labelled_rows(), "vehicle_sha256": _sha256(tmp_path / "vehicle.yml")}
    run, vehicle = _write_run(tmp_path, rows, statuses={})
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 0
    assert checks["vehicle stamp"]["result"] == "PASS"


def test_a_vehicle_stamp_for_another_vehicle_fails(tmp_path, monkeypatch):
    rows = _legacy_rows()
    rows["vehicle_sha256"] = "0" * 64
    run, vehicle = _write_run(tmp_path, rows)
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["vehicle stamp"]["result"] == "FAIL"


def test_a_failed_car_run_fails(tmp_path, monkeypatch):
    run, vehicle = _write_run(tmp_path, _legacy_rows(), status="failed")
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["run identity"]["result"] == "FAIL"


def test_a_run_without_identity_fails(tmp_path, monkeypatch):
    run, vehicle = _write_run(tmp_path, _legacy_rows())
    (run / "identity.json").unlink()
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["run identity"]["result"] == "FAIL"


def test_a_run_for_another_vehicle_fails(tmp_path, monkeypatch):
    run, vehicle = _write_run(tmp_path, _legacy_rows())
    vehicle.write_text(VEHICLE + "changed: true\n", encoding="utf-8")
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["vehicle"]["result"] == "FAIL"


def test_the_bundle_of_the_run_is_named(tmp_path, monkeypatch):
    run, vehicle = _write_run(tmp_path, _legacy_rows())
    _, checks, _ = _accept(run, vehicle, monkeypatch)
    assert checks["bundle"]["result"] == "PASS"
    assert "test-bundle-1" in checks["bundle"]["detail"]


def test_a_bundle_that_does_not_describe_the_vehicle_fails(tmp_path, monkeypatch):
    bundle = {"bundle_id": "test-bundle-1", "status": "candidate", "describes_vehicle": False}
    run, vehicle = _write_run(tmp_path, _legacy_rows(), bundle=bundle)
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["bundle"]["result"] == "FAIL"


def test_an_identity_from_before_bundles_has_an_unavailable_bundle(tmp_path, monkeypatch):
    run, vehicle = _write_run(tmp_path, _legacy_rows())
    identity = json.loads((run / "identity.json").read_text(encoding="utf-8"))
    del identity["bundle"]
    (run / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 0
    assert checks["bundle"]["result"] == "UNAVAILABLE"


def test_a_metrics_file_that_changed_after_the_run_fails(tmp_path, monkeypatch):
    run, vehicle = _write_run(tmp_path, _legacy_rows())
    with (run / METRICS).open("a", encoding="utf-8") as handle:
        handle.write("extra_metric,2.0\n")
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["metrics file"]["result"] == "FAIL"


def test_a_missing_metrics_file_fails(tmp_path, monkeypatch):
    run, vehicle = _write_run(tmp_path, _legacy_rows())
    (run / METRICS).unlink()
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["metrics file"]["result"] == "FAIL"


def test_a_duplicated_metric_row_fails(tmp_path, monkeypatch):
    run, vehicle = _write_run(tmp_path, _legacy_rows())
    metrics = run / METRICS
    metrics.write_text(metrics.read_text(encoding="utf-8") + "avg_lltd_front_pct,9.9\n", encoding="utf-8")
    identity = json.loads((run / "identity.json").read_text(encoding="utf-8"))
    identity["outputs"][0] = {"path": METRICS, "sha256": _sha256(metrics)}
    (run / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["metric rows"]["result"] == "FAIL"
    assert "avg_lltd_front_pct" in checks["metric rows"]["detail"]


def test_a_run_of_another_target_fails(tmp_path, monkeypatch):
    run, vehicle = _write_run(tmp_path, _legacy_rows(), target="standard-eval-transient")
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["run identity"]["result"] == "FAIL"


def test_the_executed_setup_matches_the_vehicle(tmp_path, monkeypatch):
    run, vehicle = _write_run(tmp_path, _legacy_rows())
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 0
    assert checks["executed spring tables"]["result"] == "PASS"
    assert checks["executed bar rates"]["result"] == "PASS"


@pytest.mark.parametrize(
    ("key", "value", "check"),
    [
        ("pFrStabar.barRate", 390.0, "executed bar rates"),
        ("pVehicle.pFrStabar.barRate", 390.0, "executed bar rates"),
        ("pRrStabar.barRate", 525.0, "executed bar rates"),
        ("pVehicle.pRrAxleDW.springTable[2,2]", 2500.0, "executed spring tables"),
    ],
)
def test_a_fourpost_override_that_differs_from_the_vehicle_fails(tmp_path, monkeypatch, key, value, check):
    run, vehicle = _write_run(tmp_path, _legacy_rows(), case={**EXECUTED_SETUP, key: value})
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks[check]["result"] == "FAIL"
    assert key in checks[check]["detail"]


def test_a_spring_table_that_differs_fails_even_with_config_overrides(tmp_path, monkeypatch):
    case = {**EXECUTED_SETUP, **FE03BAB_BAR_RATES, "pVehicle.pFrAxleDW.springTable[2,2]": 1100.0}
    run, vehicle = _write_run(tmp_path, _legacy_rows(), case=case, config=FE03BAB_CONFIG)
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["executed spring tables"]["result"] == "FAIL"


def test_a_run_without_a_fourpost_manifest_fails(tmp_path, monkeypatch):
    run, vehicle = _write_run(tmp_path, _legacy_rows())
    (run / MANIFEST).unlink()
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["executed spring tables"]["result"] == "FAIL"
    assert checks["executed bar rates"]["result"] == "FAIL"


def test_a_fourpost_manifest_that_changed_after_the_run_fails(tmp_path, monkeypatch):
    run, vehicle = _write_run(tmp_path, _legacy_rows())
    (run / MANIFEST).write_text(json.dumps({"case": {}}), encoding="utf-8")
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["executed spring tables"]["result"] == "FAIL"


def test_anti_coefficients_that_match_kinpy_pass(tmp_path, monkeypatch):
    run, vehicle = _write_run(tmp_path, _labelled_rows())
    _, checks, _ = _accept(run, vehicle, monkeypatch)
    assert checks["anti geometry"]["result"] == "PASS"


@pytest.mark.parametrize("kinpy", [-1.5, 1.6, 1.4])
def test_anti_coefficients_outside_the_tolerance_fail(tmp_path, monkeypatch, kinpy):
    monkeypatch.setattr(acceptance, "_kinpy_coefficients", lambda vehicle: {axle: _Coefficients(kinpy) for axle in acceptance.AXLES})
    run, vehicle = _write_run(tmp_path, _labelled_rows())
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["anti geometry"]["result"] == "FAIL"


def test_an_anti_coefficient_without_a_finite_value_fails(tmp_path, monkeypatch):
    rows = {**_labelled_rows(), "avg_longitudinal_jacking_coeff_rear": "nan"}
    run, vehicle = _write_run(tmp_path, rows)
    _, checks, _ = _accept(run, vehicle, monkeypatch)
    assert checks["anti geometry"]["result"] == "FAIL"


def test_anti_geometry_is_unavailable_when_kinpy_cannot_load(tmp_path, monkeypatch):
    def missing(vehicle):
        raise ImportError("no engines.kinpy")

    monkeypatch.setattr(acceptance, "_kinpy_coefficients", missing)
    run, vehicle = _write_run(tmp_path, _labelled_rows())
    _, checks, _ = _accept(run, vehicle, monkeypatch)
    assert checks["anti geometry"]["result"] == "UNAVAILABLE"
