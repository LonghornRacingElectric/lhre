import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

import yaml
from common.vehicle_io import load_yaml, require_side, tire_template_name, tire_templates_root

SCHEMA = "lhre.car-run.v1"
LISTS = ("failure", "executable")
NOT_OUTPUTS = {"identity.json", ".facts"}
COMMITTED_VEHICLE = "vehicle/vehicle.yml"


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_facts(path):
    facts = {}
    lists = {key: [] for key in LISTS}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key in lists:
            lists[key].append(value)
        else:
            facts[key] = value
    return facts, lists


def _flag(value):
    return {"true": True, "false": False}.get(value)


def _tires(vehicle):
    data = load_yaml(vehicle)
    root = tire_templates_root(data)
    axles = {}
    for axle in ("front", "rear"):
        axles.setdefault(tire_template_name(data, require_side(data, axle)), []).append(axle)
    return [
        {"template": name, "axles": sides, "sha256": _sha256(root / f"{name}.tir")}
        for name, sides in axles.items()
    ]


def _bundle(vehicle, vehicle_sha256):
    path = vehicle.parent / "bundle.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{_display(path)} is not a YAML mapping.")
    if not data.get("bundle_id"):
        raise ValueError(f"{_display(path)} has no bundle_id.")
    member = ((data.get("members") or {}).get("vehicle") or {}).get("sha256")
    return {
        "path": _display(path),
        "sha256": _sha256(path),
        "bundle_id": data["bundle_id"],
        "status": data.get("status"),
        "describes_vehicle": None if member is None else member == vehicle_sha256,
    }


def _display(path):
    try:
        return path.resolve().relative_to(Path.cwd().resolve().parent).as_posix()
    except ValueError:
        return str(path)


def build_identity(out, vehicle):
    facts, lists = _read_facts(out / ".facts")
    failures = list(lists["failure"])
    vehicle_sha256 = _sha256(vehicle)
    candidate = vehicle.resolve() != (Path.cwd().parent / COMMITTED_VEHICLE).resolve()
    executed = out / "vehicle.yml"
    executed_sha256 = _sha256(executed) if executed.is_file() else None
    if executed_sha256 not in (None, vehicle_sha256):
        failures.append(f"{_display(vehicle)} changed during the run.")
    try:
        tires = _tires(vehicle)
    except (OSError, KeyError, TypeError, ValueError) as error:
        tires = None
        failures.append(f"Cannot hash the tire files: {error}")
    try:
        bundle = _bundle(vehicle, vehicle_sha256)
    except (OSError, ValueError, yaml.YAMLError) as error:
        bundle = None
        failures.append(f"Cannot read the vehicle bundle: {error}")

    target_status = facts.get("target_status", "not_run")
    exit_status = None if target_status in ("", "not_run") else int(target_status)
    executables = []
    for item in lists["executable"]:
        path, _, state = item.rpartition(" ")
        executables.append({"path": path, "built_after_records": state == "built_after_records"})
    clean_after = _flag(facts.get("bobsim_clean_after"))
    ok = (
        not failures
        and exit_status == 0
        and facts.get("records_state") == "written"
        and clean_after is True
        and all(item["built_after_records"] for item in executables)
    )
    outputs = [
        {"path": path.relative_to(out).as_posix(), "sha256": _sha256(path), "bytes": path.stat().st_size}
        for path in sorted(out.rglob("*"))
        if path.is_file() and path.relative_to(out).as_posix() not in NOT_OUTPUTS
    ]
    return {
        "schema": SCHEMA,
        "status": "ok" if ok else "failed",
        "failures": failures,
        "target": facts.get("target"),
        "target_exit_status": exit_status,
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "lhre": {"commit": facts.get("lhre_commit") or None, "dirty": _flag(facts.get("lhre_dirty"))},
        "bobsim": {"commit": facts.get("bobsim_commit") or None, "clean_after": clean_after},
        "boblib": {"commit": facts.get("boblib_commit") or None},
        "image": {"name": facts.get("image_name") or None, "id": facts.get("image_id") or None},
        "vehicle": {
            "path": _display(vehicle),
            "name": facts.get("vehicle_name"),
            "sha256": vehicle_sha256,
            "executed_sha256": executed_sha256,
            "candidate": candidate,
            "note": "A candidate copy. It is not the vehicle member of vehicle/bundle.yml." if candidate else None,
        },
        "bundle": bundle,
        "tires": tires,
        "records": {"state": facts.get("records_state"), "executables": executables},
        "outputs": outputs,
    }


def main(argv):
    out = Path(argv[1])
    identity = build_identity(out, Path(os.environ["BOBSIM_VEHICLE"]))
    (out / "identity.json").write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
    (out / ".facts").unlink()
    print(f"{out / 'identity.json'}: status {identity['status']}")
    for failure in identity["failures"]:
        print(f"  {failure}")
    if identity["vehicle"]["candidate"]:
        print(f"  Candidate: {identity['vehicle']['path']} is not the vehicle member of vehicle/bundle.yml.")
    bundle = identity["bundle"]
    if bundle and bundle["describes_vehicle"] is not True:
        print(f"  Warning: bundle {bundle['bundle_id']} does not describe {identity['vehicle']['path']}.")
    return 0 if identity["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
