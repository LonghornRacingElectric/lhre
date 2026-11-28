"""identity.json for a make car run: what ran, on which car, and whether to trust it."""

import datetime
import hashlib
import json
from pathlib import Path

import yaml
from common.vehicle_io import load_yaml, require_side, tire_template_name, tire_templates_root

# v2: BobSim stages the records itself (BOBSIM_VEHICLE), so the v1 records
# block and its per-executable staleness check are gone.
SCHEMA = "lhre.car-run.v2"
# BOBSIM_OUT is a cache under the run folder. Its copies are listed in outputs.
NOT_OUTPUTS = {"identity.json"}
NOT_OUTPUT_DIRS = {".bobsim"}
COMMITTED_VEHICLE = "vehicle/vehicle.yml"


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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


def _is_output(path, out):
    relative = path.relative_to(out)
    return path.is_file() and relative.as_posix() not in NOT_OUTPUTS and relative.parts[0] not in NOT_OUTPUT_DIRS


def build_identity(out, vehicle, facts, failures):
    """The identity of one run.

    ``facts`` holds what tools/car.py saw: target, target_status (an int or
    None when the target never ran), vehicle_name, lhre_commit, lhre_dirty,
    bobsim_commit, bobsim_clean_after, boblib_commit, image_name, image_id.
    ``failures`` lists what already went wrong; more are added here.
    """
    failures = list(failures)
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

    exit_status = facts.get("target_status")
    clean_after = facts.get("bobsim_clean_after")
    ok = not failures and exit_status == 0 and clean_after is True
    outputs = [
        {"path": path.relative_to(out).as_posix(), "sha256": _sha256(path), "bytes": path.stat().st_size}
        for path in sorted(out.rglob("*"))
        if _is_output(path, out)
    ]
    return {
        "schema": SCHEMA,
        "status": "ok" if ok else "failed",
        "failures": failures,
        "target": facts.get("target"),
        "target_exit_status": exit_status,
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "lhre": {"commit": facts.get("lhre_commit") or None, "dirty": facts.get("lhre_dirty")},
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
        "outputs": outputs,
    }


def json_text(identity):
    return json.dumps(identity, indent=2) + "\n"
