import csv
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path

import yaml

SCHEMA = "lhre.four-post-acceptance.v1"
METRICS = "generated_results/four_post_eval_report_metrics.csv"
MANIFESTS = "runs/FourPostSim/*/manifest.json"
AXLES = {"front": "Fr", "rear": "Rr"}
SETUP_KEY = re.compile(r"(pVehicle\.)?p(Fr|Rr)Stabar\.barRate|pVehicle\.p(Fr|Rr)AxleDW\.springTable\[\d+,\d+\]")
TARGETS = ("standard-eval-four-post", "standard-eval-all")
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
MEASURED_BALANCE = "static_equilibrium_pass"
LEGACY_BALANCE = "static_balance_pass"
PASS, FAIL, UNAVAILABLE = "PASS", "FAIL", "UNAVAILABLE"


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _finite(text):
    try:
        return math.isfinite(float(text))
    except (TypeError, ValueError):
        return False


def _flag(text):
    return {"true": True, "1": True, "false": False, "0": False}.get(str(text).strip().lower())


def _check(name, result, detail):
    return {"name": name, "result": result, "detail": detail}


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
    return identity, [run_check, vehicle_check]


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
        actuation = (data.get(axle) or {}).get("actuation") or {}
        stabar = actuation.get("stabar")
        rate = stabar.get("rate_n_m_per_rad") if isinstance(stabar, dict) else None
        setup[f"p{tag}Stabar.barRate"] = setup[f"pVehicle.p{tag}Stabar.barRate"] = rate
        table = ((actuation.get("shock") or {}).get("spring_table") or {}).get("table") or []
        for i, row in enumerate(table, 1):
            for j, value in enumerate(row, 1):
                setup[f"pVehicle.p{tag}AxleDW.springTable[{i},{j}]"] = value
    return setup


def _setup_check(run, identity, vehicle):
    manifests = sorted(run.glob(MANIFESTS))
    if not manifests:
        return _check("executed setup", FAIL, f"{run} has no FourPost run manifest. The setup that ran is unknown.")
    recorded = _recorded(identity)
    expected = _vehicle_setup(vehicle)
    problems, compared = {}, set()
    for manifest in manifests:
        name = manifest.relative_to(run).as_posix()
        if recorded.get(name) != _sha256(manifest):
            problems[f"{name} is not the manifest this run wrote"] = None
            continue
        case = json.loads(manifest.read_text(encoding="utf-8")).get("case") or {}
        for key, value in sorted(case.items()):
            if not SETUP_KEY.fullmatch(key):
                continue
            compared.add(key)
            want = expected.get(key)
            if want is None or not math.isclose(float(value), float(want), rel_tol=1e-9, abs_tol=1e-12):
                problems[f"{key} ran at {value}, {vehicle.name} has {'none' if want is None else want}"] = None
    if problems:
        return _check("executed setup", FAIL, f"The FourPost runs did not use the vehicle file: {'. '.join(problems)}.")
    return _check(
        "executed setup",
        PASS,
        f"{len(manifests)} FourPost runs use the {len(compared)} spring table and bar rate values of {vehicle.name}.",
    )


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

    if "status" in fields:
        bad = [f"{name}={row.get('status')}" for name, row in rows.items() if row.get("status") != "ok"]
        if bad:
            checks.append(_check("metric statuses", FAIL, f"Rows without status ok: {', '.join(bad)}."))
        else:
            checks.append(_check("metric statuses", PASS, f"All {len(rows)} rows have status ok."))
    else:
        checks.append(_check("metric statuses", UNAVAILABLE, "The CSV has no status column at this BobSim pin."))

    reported = {name: _flag(rows[name].get("value")) for name in (MEASURED_BALANCE, LEGACY_BALANCE) if name in rows}
    not_true = [f"{name}={rows[name].get('value')}" for name, value in reported.items() if value is not True]
    if not_true:
        checks.append(_check("static equilibrium", FAIL, f"The pin reports {', '.join(not_true)}."))
    elif MEASURED_BALANCE in reported:
        checks.append(_check("static equilibrium", PASS, f"The pin reports {MEASURED_BALANCE}=True."))
    elif LEGACY_BALANCE in reported:
        checks.append(_check(
            "static equilibrium",
            UNAVAILABLE,
            f"The pin reports only {LEGACY_BALANCE}. It compares target loads with themselves. "
            "It is not a measurement.",
        ))
    else:
        checks.append(_check("static equilibrium", UNAVAILABLE, "The pin reports no static balance."))

    if "vehicle_sha256" in rows:
        stamped = rows["vehicle_sha256"].get("value")
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


def evaluate(run, vehicle):
    identity, checks = _identity_checks(run, vehicle)
    file_check = _file_check(run, identity)
    checks.append(file_check)
    if (run / METRICS).is_file():
        checks.extend(_metric_checks(*_read_metrics(run / METRICS), vehicle))
    checks.append(_setup_check(run, identity, vehicle))
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
        print(f"  {check['result']:<12} {check['name']}: {check['detail']}")
    total = len(report["checks"])
    counts = {result: sum(check["result"] == result for check in report["checks"]) for result in (FAIL, UNAVAILABLE)}
    print(f"Failed checks: {counts[FAIL]} of {total}. Unavailable checks: {counts[UNAVAILABLE]} of {total}.")
    print("An unavailable check is not a pass. This is a software check. It is not a physical validation.")
    if counts[FAIL]:
        print("Result: rejected.")
        return 1
    print("Result: accepted.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
