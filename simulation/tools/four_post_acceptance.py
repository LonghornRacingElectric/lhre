import csv
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path

import numpy as np
import yaml

SCHEMA = "lhre.four-post-acceptance.v2"
METRICS = "generated_results/four_post_eval_report_metrics.csv"
MANIFESTS = "runs/FourPostSim/*/manifest.json"
FOUR_POST_CONFIG = "four_post_eval_config.yml"
AXLES = {"front": "Fr", "rear": "Rr"}
BAR_RATE_KEY = re.compile(r"(?:pVehicle\.)?p(Fr|Rr)Stabar\.barRate")
SPRING_TABLE_KEY = re.compile(r"pVehicle\.p(Fr|Rr)AxleDW\.springTable\[\d+,\d+\]")
CONFIG_BAR_RATE_KEYS = ("stabar_rate_n_m_per_rad", "bar_rate_n_m_per_rad", "arb_rate")
TARGETS = ("standard-eval-four-post", "standard-eval-all")
ANTI_KEY = "avg_longitudinal_jacking_coeff_{axle}"
ANTI_REL_TOL = 0.05
ANTI_ABS_TOL = 1e-4
REQUIRED_METRICS = (
    "avg_motion_ratio_front",
    "avg_motion_ratio_rear",
    "spring_roll_stiffness_front_Nm_per_rad",
    "spring_roll_stiffness_rear_Nm_per_rad",
    "arb_roll_stiffness_front_Nm_per_rad",
    "arb_roll_stiffness_rear_Nm_per_rad",
    "elastic_roll_stiffness_front_Nm_per_rad",
    "elastic_roll_stiffness_rear_Nm_per_rad",
    "avg_lltd_front_pct",
    "avg_anti_dive_pct",
    "avg_anti_squat_pct",
    "camber_gain_heave_rad_per_m",
    "camber_gain_roll_rad_per_rad",
    "toe_gain_heave_rad_per_m",
    "toe_gain_roll_rad_per_rad",
    "minimum_roll_pulse_contact_fz_n",
    "roll_contact_valid_points",
)
BALANCE_STATUS = "static_balance_status"
LEGACY_BALANCE = "static_balance_pass"
BALANCE_METRICS = (
    BALANCE_STATUS,
    "static_balance_reason",
    LEGACY_BALANCE,
    "static_balance_max_abs_fz_error_n",
    "static_balance_max_abs_fz_error_pct",
    "static_equilibrium_pass",
    "static_equilibrium_max_force_residual_n",
    "static_equilibrium_max_moment_residual_nm",
    "static_equilibrium_max_relative_residual",
    "static_equilibrium_worst_time_s",
    "static_spring_max_residual_n",
)
VEHICLE_STAMP = "vehicle_sha256"
CHECKED_METRICS = (*REQUIRED_METRICS, *BALANCE_METRICS, VEHICLE_STAMP)
BALANCE_CLAUSES = {"closure": "closure", "corner": "corner load", "spring": "spring force"}
BOBSIM_117 = "BobSim #117 (https://github.com/BobDyn/BobSim/pull/117)"
CLOSURE_DRIFT = "BobLib issue 'FourPostSim: axle force closure residual grows with simulated time at a fixed pose'"
PASS, FAIL, UNAVAILABLE, KNOWN_UPSTREAM = "PASS", "FAIL", "UNAVAILABLE", "KNOWN-UPSTREAM"


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return math.nan


def _finite(text):
    return math.isfinite(_number(text))


def _flag(text):
    return {"true": True, "1": True, "false": False, "0": False}.get(str(text).strip().lower())


def _same(a, b):
    return math.isclose(_number(a), _number(b), rel_tol=1e-9, abs_tol=1e-12)


def _check(name, result, detail):
    return {"name": name, "result": result, "detail": detail}


def _mapping(node, *keys):
    for key in keys:
        node = node.get(key) if isinstance(node, dict) else None
    return node if isinstance(node, dict) else {}


def _identity_checks(run, vehicle):
    path = run / "identity.json"
    if not path.is_file():
        return None, [_check("run identity", FAIL, f"{path} does not exist. Run make car first.")]
    identity = json.loads(path.read_text(encoding="utf-8"))
    target = identity.get("target")
    if identity.get("status") != "ok":
        failures = " ".join(identity.get("failures") or []) or "no reason given"
        run_check = _check("run identity", FAIL, f"make car T={target} failed: {failures}")
    elif target not in TARGETS:
        run_check = _check("run identity", FAIL, f"make car ran T={target}. FourPost runs in {', '.join(TARGETS)}.")
    else:
        run_check = _check(
            "run identity",
            PASS,
            f"make car T={target} exited 0, wrote the records and left bobsim/ clean.",
        )
    executed = (identity.get("vehicle") or {}).get("sha256")
    current = _sha256(vehicle)
    if executed == current:
        vehicle_check = _check("vehicle", PASS, f"The run used {vehicle} with sha256 {current}.")
    else:
        vehicle_check = _check("vehicle", FAIL, f"The run used vehicle sha256 {executed}. {vehicle} is now {current}.")
    return identity, [run_check, vehicle_check, _bundle_check(identity)]


def _bundle_check(identity):
    if "bundle" not in identity:
        return _check("bundle", UNAVAILABLE, "identity.json names no vehicle bundle. make car wrote it before bundles.")
    bundle = identity["bundle"] or {}
    name = bundle.get("bundle_id")
    if not name:
        return _check("bundle", FAIL, "identity.json names no bundle_id. The run cannot be traced to a bundle.")
    if bundle.get("describes_vehicle") is not True:
        return _check(
            "bundle",
            FAIL,
            f"Bundle {name} does not describe the vehicle that ran. Its members.vehicle.sha256 is not that file.",
        )
    return _check("bundle", PASS, f"The run used bundle {name} (status {bundle.get('status')}).")


def _read_metrics(path):
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows, duplicates = {}, []
        for row in reader:
            name = row.get("metric")
            if name in rows:
                duplicates.append(name)
            rows[name] = row
    return fields, rows, duplicates


def _recorded(identity):
    return {item.get("path"): item.get("sha256") for item in (identity or {}).get("outputs", [])}


def _vehicle_setup(vehicle):
    data = yaml.safe_load(Path(vehicle).read_text(encoding="utf-8")) or {}
    setup = {}
    for axle, tag in AXLES.items():
        actuation = _mapping(data, axle, "actuation")
        stabar = actuation.get("stabar")
        rate = stabar.get("rate_n_m_per_rad") if isinstance(stabar, dict) else None
        setup[f"p{tag}Stabar.barRate"] = setup[f"pVehicle.p{tag}Stabar.barRate"] = rate
        table = _mapping(actuation, "shock", "spring_table").get("table") or []
        for i, row in enumerate(table, 1):
            for j, value in enumerate(row, 1):
                setup[f"pVehicle.p{tag}AxleDW.springTable[{i},{j}]"] = value
    return setup


def _config_bar_rates(run, identity):
    """Bar rates that the FourPost config of the run sets in model_overrides, by axle tag."""
    path = run / FOUR_POST_CONFIG
    if not path.is_file():
        return {}, f"The run did not record the BobSim {FOUR_POST_CONFIG}."
    if _recorded(identity).get(FOUR_POST_CONFIG) != _sha256(path):
        return {}, f"{FOUR_POST_CONFIG} is not the file this run recorded."
    suspension = _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), "model_overrides", "suspension")
    rates = {}
    for axle, tag in AXLES.items():
        settings = _mapping(suspension, axle)
        rate = next((settings[key] for key in CONFIG_BAR_RATE_KEYS if settings.get(key) is not None), None)
        if rate is not None:
            rates[tag] = float(rate)
    return rates, None


def _manifest_cases(run, identity):
    recorded = _recorded(identity)
    cases, changed = [], []
    for manifest in sorted(run.glob(MANIFESTS)):
        name = manifest.relative_to(run).as_posix()
        if recorded.get(name) != _sha256(manifest):
            changed.append(name)
        else:
            cases.append(json.loads(manifest.read_text(encoding="utf-8")).get("case") or {})
    return cases, changed


def _manifest_problem(run, cases, changed):
    if changed:
        return f"{', '.join(changed)} is not the manifest this run wrote."
    if not cases:
        return f"{run} has no FourPost run manifest. The setup that ran is unknown."
    return None


def _compare(cases, pattern, expected):
    compared, mismatches = set(), {}
    for case in cases:
        for key, value in sorted(case.items()):
            match = pattern.fullmatch(key)
            if not match:
                continue
            compared.add(match.group(1))
            want = expected.get(key)
            if want is None or not _same(value, want):
                mismatches[key] = (_number(value), match.group(1), want)
    return compared, mismatches


def _describe(mismatches, vehicle):
    return ". ".join(
        f"{key} ran at {ran}, {vehicle.name} has {'none' if want is None else want}"
        for key, (ran, _, want) in mismatches.items()
    )


def _spring_table_check(run, cases, changed, expected, vehicle):
    name = "executed spring tables"
    problem = _manifest_problem(run, cases, changed)
    if problem:
        return _check(name, FAIL, problem)
    compared, mismatches = _compare(cases, SPRING_TABLE_KEY, expected)
    if mismatches:
        detail = _describe(mismatches, vehicle)
        return _check(name, FAIL, f"FourPost did not run the spring tables of the vehicle: {detail}.")
    if compared != set(AXLES.values()):
        return _check(name, UNAVAILABLE, "The FourPost manifests do not set both spring tables. What ran is unknown.")
    return _check(name, PASS, f"{len(cases)} FourPost runs use the spring tables of {vehicle.name}.")


def _bar_rate_check(run, identity, cases, changed, expected, vehicle):
    name = "executed bar rates"
    problem = _manifest_problem(run, cases, changed)
    if problem:
        return _check(name, FAIL, problem)
    compared, mismatches = _compare(cases, BAR_RATE_KEY, expected)
    config_rates, config_problem = _config_bar_rates(run, identity)
    upstream = [ran for ran, tag, _ in mismatches.values() if tag in config_rates and _same(ran, config_rates[tag])]
    if mismatches and len(upstream) == len(mismatches):
        return _check(
            name,
            KNOWN_UPSTREAM,
            f"FourPost ran the bar rates of model_overrides in its own {FOUR_POST_CONFIG}, not of the vehicle: "
            f"{_describe(mismatches, vehicle)}. {BOBSIM_117} removes these overrides. Until the pin has it, the "
            "FourPost bar roll stiffness and load transfer values are not for our bars.",
        )
    if mismatches:
        detail = _describe(mismatches, vehicle) + (f". {config_problem}" if config_problem else ".")
        return _check(name, FAIL, f"FourPost did not run the bar rates of the vehicle: {detail}")
    with_bar = {tag for tag in AXLES.values() if expected.get(f"p{tag}Stabar.barRate") is not None}
    if not with_bar <= compared:
        return _check(name, UNAVAILABLE, "The FourPost manifests do not set the rate of each bar. What ran is unknown.")
    return _check(name, PASS, f"{len(cases)} FourPost runs use the bar rates of {vehicle.name}.")


def _file_check(run, identity):
    path = run / METRICS
    if not path.is_file():
        return _check("metrics file", FAIL, f"{path} does not exist. The FourPost evaluation wrote no metrics.")
    recorded = _recorded(identity).get(METRICS)
    if recorded is None:
        return _check("metrics file", FAIL, f"identity.json does not list {METRICS}. This run did not write it.")
    if recorded != _sha256(path):
        return _check("metrics file", FAIL, f"{METRICS} changed after the run. Its sha256 differs from identity.json.")
    return _check("metrics file", PASS, f"{METRICS} is the file this run wrote.")


def _status_check(fields, rows):
    if "status" not in fields:
        return _check("metric statuses", UNAVAILABLE, "The CSV has no status column at this BobSim pin.")

    def not_ok(names):
        return [f"{name}={rows[name].get('status')}" for name in names if rows[name].get("status") != "ok"]

    checked = [name for name in CHECKED_METRICS if name in rows]
    bad = not_ok(checked)
    others = not_ok(name for name in rows if name not in CHECKED_METRICS)
    note = f" Rows the acceptance does not check, without status ok: {', '.join(others)}." if others else ""
    if bad:
        return _check("metric statuses", FAIL, f"Checked metrics without status ok: {', '.join(bad)}.{note}")
    return _check("metric statuses", PASS, f"The {len(checked)} checked metrics have status ok.{note}")


def _legacy_balance_check(rows):
    if LEGACY_BALANCE not in rows:
        return _check("static balance", UNAVAILABLE, "The pin reports no static balance.")
    value = rows[LEGACY_BALANCE].get("value")
    if _flag(value) is True:
        return _check(
            "static balance",
            UNAVAILABLE,
            f"unavailable: legacy tautology. The pin reports {LEGACY_BALANCE}={value}, but it compares the target "
            "loads with themselves. It measures nothing.",
        )
    return _check(
        "static balance",
        FAIL,
        f"The pin reports {LEGACY_BALANCE}={value}. The legacy check compares the target loads with themselves, "
        "so a failure means that its inputs had no finite value.",
    )


def _measured_check(name, failed, number, detail):
    if not math.isfinite(number):
        return _check(name, FAIL, "BobSim gives no finite value.")
    if failed:
        return _check(name, FAIL, f"{detail} BobSim reports it outside its tolerance.")
    return _check(name, PASS, f"{detail} BobSim reports it within its tolerance.")


def _measured_balance_checks(rows):
    def value(key):
        return (rows.get(key) or {}).get("value")

    status = (value(BALANCE_STATUS) or "").strip().lower()
    reason = (value("static_balance_reason") or "").strip()
    if status not in ("pass", "fail"):
        return [_check(
            "static balance",
            FAIL,
            f"The pin measures static balance, but this run reports {BALANCE_STATUS}={status or 'empty'}: "
            f"{reason or 'no reason given'}.",
        )]

    clauses = [clause.strip() for clause in reason.split(";") if clause.strip()] if status == "fail" else []
    failed = {kind for kind, prefix in BALANCE_CLAUSES.items() if any(c.startswith(prefix) for c in clauses)}
    unknown = [c for c in clauses if not c.startswith(tuple(BALANCE_CLAUSES.values()))]
    checks = []
    if unknown or (status == "fail" and not clauses):
        listed = "; ".join(unknown) or "no reason given"
        checks.append(_check("static balance", FAIL, f"BobSim reports a static balance failure: {listed}."))

    error_n = _number(value("static_balance_max_abs_fz_error_n"))
    error_pct = _number(value("static_balance_max_abs_fz_error_pct"))
    checks.append(_measured_check(
        "static balance: corner loads",
        "corner" in failed,
        error_n,
        f"The largest corner contact Fz differs from its target load by {error_n:.3g} N ({error_pct:.3g} %).",
    ))
    spring = _number(value("static_spring_max_residual_n"))
    checks.append(_measured_check(
        "static balance: springs",
        "spring" in failed,
        spring,
        f"The largest spring force differs from the executed spring table by {spring:.3g} N.",
    ))

    closure_pass = _flag(value("static_equilibrium_pass"))
    closure = (
        f"largest relative residual {_number(value('static_equilibrium_max_relative_residual')):.3g} "
        f"(force {_number(value('static_equilibrium_max_force_residual_n')):.3g} N, "
        f"moment {_number(value('static_equilibrium_max_moment_residual_nm')):.3g} N m) "
        f"at t = {_number(value('static_equilibrium_worst_time_s')):g} s"
    )
    if closure_pass is None:
        checks.append(_check("static balance: closure", FAIL, "BobSim gives no static_equilibrium_pass value."))
    elif closure_pass == ("closure" in failed):
        checks.append(_check(
            "static balance: closure",
            FAIL,
            f"BobSim reports static_equilibrium_pass={closure_pass} and the reason '{reason}'. They disagree.",
        ))
    elif closure_pass:
        checks.append(_check(
            "static balance: closure",
            PASS,
            f"Every zero-command row closes within the BobSim tolerance, the design pose too: {closure}.",
        ))
    else:
        checks.append(_check(
            "static balance: design-pose closure",
            UNAVAILABLE,
            "The metrics CSV gives only the largest closure residual over all zero-command rows. It does not give "
            "the closure at the design pose apart from the time drift.",
        ))
        checks.append(_check(
            "static balance: closure drift",
            KNOWN_UPSTREAM,
            f"BobSim reports a closure failure: {closure}. Known defect: {CLOSURE_DRIFT}. It makes this residual "
            "grow with simulated time. The CSV cannot show that the drift is the only cause.",
        ))
    return checks


def _metric_checks(fields, rows, duplicates, vehicle):
    checks = []
    if duplicates:
        names = ", ".join(sorted(set(duplicates)))
        checks.append(_check("metric rows", FAIL, f"The CSV has more than one row for {names}."))
    else:
        checks.append(_check("metric rows", PASS, f"The CSV has one row for each of its {len(rows)} metrics."))

    missing = [name for name in REQUIRED_METRICS if name not in rows]
    not_finite = [name for name in REQUIRED_METRICS if name in rows and not _finite(rows[name].get("value"))]
    if missing or not_finite:
        detail = []
        if missing:
            detail.append(f"Missing: {', '.join(missing)}.")
        if not_finite:
            detail.append(f"No finite value: {', '.join(not_finite)}.")
        checks.append(_check("required metrics", FAIL, " ".join(detail)))
    else:
        checks.append(_check("required metrics", PASS, f"{len(REQUIRED_METRICS)} metrics have finite values."))

    checks.append(_status_check(fields, rows))
    if BALANCE_STATUS in rows:
        checks.extend(_measured_balance_checks(rows))
    else:
        checks.append(_legacy_balance_check(rows))

    if VEHICLE_STAMP in rows:
        stamped = rows[VEHICLE_STAMP].get("value")
        current = _sha256(vehicle)
        result = PASS if stamped == current else FAIL
        detail = f"The CSV names vehicle sha256 {stamped}. {vehicle} is {current}."
        checks.append(_check("vehicle stamp", result, detail))
    else:
        checks.append(_check(
            "vehicle stamp",
            UNAVAILABLE,
            "The CSV does not name its vehicle at this pin. Only identity.json links it to the vehicle.",
        ))
    return checks


def _velocity_row(point, direction):
    return np.concatenate([direction, np.cross(point, direction)])


def contact_patch_coefficient(car, axle):
    """Jacking coefficient c = -(dx_cp/ds)/(dz_cp/ds) of the upright-fixed contact patch at the design pose."""
    points = {name: np.asarray(value, dtype=float) for name, value in car[axle]["suspension"].items()}
    rack = np.asarray(car[axle]["steering"]["rack_pickup_m"], dtype=float)
    rows = []
    for arm in ("lower", "upper"):
        aft, joint = points[f"{arm}_aft_i_m"], points[f"{arm}_o_m"]
        rows += [_velocity_row(joint, points[f"{arm}_fore_i_m"] - aft), _velocity_row(joint, joint - aft)]
    rows.append(_velocity_row(points["tie_o_m"], points["tie_o_m"] - rack))
    _, sing, vt = np.linalg.svd(np.array(rows))
    if sing[4] < 1e-9 * sing[0]:
        raise ValueError(f"{axle} suspension constraints are rank deficient")
    twist = vt[-1]
    contact = points["wheel_center_m"] - np.array([0.0, 0.0, float(car[axle]["wheel"]["radius_m"])])
    velocity = twist[:3] + np.cross(twist[3:], contact)
    if abs(velocity[2]) < 1e-12:
        raise ValueError(f"{axle} contact patch has no vertical velocity")
    return float(-velocity[0] / velocity[2])


def _anti_check(rows, vehicle):
    keys = {axle: ANTI_KEY.format(axle=axle) for axle in AXLES}
    if any(key not in rows for key in keys.values()):
        return _check("anti geometry", UNAVAILABLE, "The CSV has no longitudinal jacking coefficients at this pin.")
    car = yaml.safe_load(Path(vehicle).read_text(encoding="utf-8"))
    details, failed = [], False
    for axle, key in keys.items():
        four_post = _number(rows[key].get("value"))
        try:
            expected = contact_patch_coefficient(car, axle)
        except (KeyError, TypeError, ValueError) as error:
            return _check("anti geometry", FAIL, f"{axle} hardpoints cannot give a reference: {error!r}.")
        tolerance = max(ANTI_REL_TOL * abs(expected), ANTI_ABS_TOL)
        ok = math.isfinite(four_post) and abs(four_post - expected) <= tolerance
        failed = failed or not ok
        details.append(f"{axle} FourPost {four_post:.6g} N/N, hardpoints {expected:.6g} N/N, tolerance {tolerance:.2g}.")
    return _check("anti geometry", FAIL if failed else PASS, " ".join(details))


def evaluate(run, vehicle):
    identity, checks = _identity_checks(run, vehicle)
    checks.append(_file_check(run, identity))
    if (run / METRICS).is_file():
        fields, rows, duplicates = _read_metrics(run / METRICS)
        checks.extend(_metric_checks(fields, rows, duplicates, vehicle))
        checks.append(_anti_check(rows, vehicle))
    cases, changed = _manifest_cases(run, identity)
    expected = _vehicle_setup(vehicle)
    checks.append(_spring_table_check(run, cases, changed, expected, vehicle))
    checks.append(_bar_rate_check(run, identity, cases, changed, expected, vehicle))
    failed = [check for check in checks if check["result"] == FAIL]
    return {
        "schema": SCHEMA,
        "run": str(run),
        "result": "rejected" if failed else "accepted",
        "checks": checks,
    }


def main(argv):
    run = Path(argv[1])
    report = evaluate(run, Path(os.environ["BOBSIM_VEHICLE"]))
    (run / "four_post_acceptance.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"FourPost acceptance for {run}")
    for check in report["checks"]:
        print(f"  {check['result']:<14} {check['name']}: {check['detail']}")
    total = len(report["checks"])
    counts = {
        result: sum(check["result"] == result for check in report["checks"])
        for result in (FAIL, KNOWN_UPSTREAM, UNAVAILABLE)
    }
    print(
        f"Failed checks: {counts[FAIL]} of {total}. Known upstream defects: {counts[KNOWN_UPSTREAM]} of {total}. "
        f"Unavailable checks: {counts[UNAVAILABLE]} of {total}."
    )
    print("An unavailable or known-upstream check is not a pass. A known-upstream check names a BobSim or BobLib")
    print("defect that the pin still has. This is a software check. It is not a physical validation.")
    if counts[FAIL]:
        print("Result: rejected.")
        return 1
    print("Result: accepted.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
