import csv
import hashlib
import json

import pytest

from tools import four_post_acceptance as acceptance

METRICS = "generated_results/four_post_eval_report_metrics.csv"
MANIFEST = "runs/FourPostSim/run_1/manifest.json"
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
"""
EXECUTED_SETUP = {
    "heaveMagnitude": 0.03,
    "pVehicle.pFrAxleDW.springFreeLength": 0.2,
    "pFrStabar.barRate": 250.0,
    "pVehicle.pFrStabar.barRate": 250.0,
    "pVehicle.pFrAxleDW.springTable[1,1]": 0.0,
    "pVehicle.pFrAxleDW.springTable[1,2]": 0.0,
    "pVehicle.pFrAxleDW.springTable[2,1]": 1.0,
    "pVehicle.pFrAxleDW.springTable[2,2]": 1000.0,
    "pVehicle.pRrAxleDW.springTable[1,1]": 0.0,
    "pVehicle.pRrAxleDW.springTable[1,2]": 0.0,
    "pVehicle.pRrAxleDW.springTable[2,1]": 1.0,
    "pVehicle.pRrAxleDW.springTable[2,2]": 2000.0,
}
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


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _legacy_rows():
    return {key: ("True" if key == "static_balance_pass" else "1.5") for key in FE03BAB_KEYS}


def _write_run(tmp_path, rows, *, statuses=None, target="standard-eval-four-post", status="ok", case=None):
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
    identity = {
        "schema": "lhre.car-run.v1",
        "status": status,
        "failures": [] if status == "ok" else ["make bobsim failed."],
        "target": target,
        "target_exit_status": 0 if status == "ok" else 2,
        "vehicle": {"path": "vehicle/vehicle.yml", "sha256": _sha256(vehicle)},
        "records": {"state": "written", "executables": []},
        "bobsim": {"commit": "fe03bab", "clean_after": True},
        "outputs": [{"path": name, "sha256": _sha256(run / name)} for name in (METRICS, MANIFEST)],
    }
    (run / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
    return run, vehicle


def _accept(run, vehicle, monkeypatch):
    monkeypatch.setenv("BOBSIM_VEHICLE", str(vehicle))
    code = acceptance.main(["four_post_acceptance.py", str(run)])
    report = json.loads((run / "four_post_acceptance.json").read_text(encoding="utf-8"))
    return code, {check["name"]: check for check in report["checks"]}, report


def test_a_legacy_fe03bab_csv_passes_with_explicit_unavailable_checks(tmp_path, monkeypatch, capsys):
    run, vehicle = _write_run(tmp_path, _legacy_rows())
    code, checks, report = _accept(run, vehicle, monkeypatch)
    assert code == 0
    assert report["result"] == "accepted"
    assert checks["required metrics"]["result"] == "PASS"
    assert checks["metric statuses"]["result"] == "UNAVAILABLE"
    assert checks["static equilibrium"]["result"] == "UNAVAILABLE"
    assert "target" in checks["static equilibrium"]["detail"]
    assert checks["vehicle stamp"]["result"] == "UNAVAILABLE"
    printed = capsys.readouterr().out
    assert "UNAVAILABLE" in printed
    assert "not a pass" in printed


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


def test_a_non_ok_status_fails_when_the_csv_has_a_status_column(tmp_path, monkeypatch):
    rows = _legacy_rows()
    run, vehicle = _write_run(tmp_path, rows, statuses={"avg_fbrc_equivalent_height_front_m": "partial"})
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["metric statuses"]["result"] == "FAIL"
    assert "avg_fbrc_equivalent_height_front_m=partial" in checks["metric statuses"]["detail"]


def test_a_full_status_csv_with_measured_equilibrium_and_vehicle_stamp_passes(tmp_path, monkeypatch):
    rows = _legacy_rows()
    rows["static_equilibrium_pass"] = "True"
    (tmp_path / "vehicle.yml").write_text(VEHICLE, encoding="utf-8")
    rows["vehicle_sha256"] = _sha256(tmp_path / "vehicle.yml")
    run, vehicle = _write_run(tmp_path, rows, statuses={})
    code, checks, report = _accept(run, vehicle, monkeypatch)
    assert code == 0
    assert {check["result"] for check in checks.values()} == {"PASS"}
    assert report["result"] == "accepted"


@pytest.mark.parametrize("key", ["static_equilibrium_pass", "static_balance_pass"])
def test_a_reported_static_balance_failure_fails(tmp_path, monkeypatch, key):
    rows = _legacy_rows()
    rows[key] = "False"
    run, vehicle = _write_run(tmp_path, rows)
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["static equilibrium"]["result"] == "FAIL"


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


def test_a_vehicle_stamp_for_another_vehicle_fails(tmp_path, monkeypatch):
    rows = _legacy_rows()
    rows["vehicle_sha256"] = "0" * 64
    run, vehicle = _write_run(tmp_path, rows)
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["vehicle stamp"]["result"] == "FAIL"


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
    assert checks["executed setup"]["result"] == "PASS"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("pFrStabar.barRate", 390.0),
        ("pVehicle.pFrStabar.barRate", 390.0),
        ("pRrStabar.barRate", 525.0),
        ("pVehicle.pRrAxleDW.springTable[2,2]", 2500.0),
    ],
)
def test_a_fourpost_override_that_differs_from_the_vehicle_fails(tmp_path, monkeypatch, key, value):
    run, vehicle = _write_run(tmp_path, _legacy_rows(), case={**EXECUTED_SETUP, key: value})
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["executed setup"]["result"] == "FAIL"
    assert key in checks["executed setup"]["detail"]


def test_a_run_without_a_fourpost_manifest_fails(tmp_path, monkeypatch):
    run, vehicle = _write_run(tmp_path, _legacy_rows())
    (run / MANIFEST).unlink()
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["executed setup"]["result"] == "FAIL"


def test_a_fourpost_manifest_that_changed_after_the_run_fails(tmp_path, monkeypatch):
    run, vehicle = _write_run(tmp_path, _legacy_rows())
    (run / MANIFEST).write_text(json.dumps({"case": {}}), encoding="utf-8")
    code, checks, _ = _accept(run, vehicle, monkeypatch)
    assert code == 1
    assert checks["executed setup"]["result"] == "FAIL"
