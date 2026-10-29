"""Write OUT_DIR/provenance.json before a study runs.

python tools/provenance.py studies/<name>/run.py [study arguments]
"""

import datetime
import hashlib
import json
import os
import platform
import sys
from collections.abc import Mapping
from pathlib import Path

import yaml

from tools.invalidation import (
    METHOD,
    SCHEMA,
    GitUnavailable,
    Layout,
    bundle_path,
    dependency_keys,
    describes_vehicle,
    git,
    git_root,
    plain,
    read_bundle,
    sha256_file,
    study_sources,
    tire_files,
)

TOOLCHAIN_FILE = Path("/etc/bobsim/toolchain.json")
GIT_REPOS = ("lhre", "bobsim", "boblib")


def _location(path: Path, layout: Layout) -> tuple[str, str]:
    absolute = Path(os.path.abspath(path))
    if absolute.is_relative_to(layout.repo):
        return absolute.relative_to(layout.repo).as_posix(), "checkout"
    return absolute.as_posix(), "external"


def _repo(repo: Path | None, env: Mapping[str, str], fallback: str | None) -> dict:
    try:
        if repo is None:
            raise GitUnavailable("BobSim .gitmodules names no BobLib submodule")
        git_root(repo)
        return {
            "sha": git(repo, "rev-parse", "HEAD").strip(),
            "dirty": bool(git(repo, "status", "--porcelain", "--ignore-submodules=dirty").strip()),
            "source": "git",
        }
    except GitUnavailable as error:
        sha = env.get(fallback) if fallback else None
        return {"sha": sha or None, "dirty": None, "source": f"env:{fallback}" if sha else None, "error": str(error)}


def _boblib(layout: Layout, env: Mapping[str, str]) -> dict:
    record = _repo(layout.boblib, env, None)
    try:
        name = layout.boblib.relative_to(layout.bobsim).as_posix()
        record["gitlink"] = git(git_root(layout.bobsim), "rev-parse", f"HEAD:{name}").strip()
    except (AttributeError, GitUnavailable):
        record["gitlink"] = None
    return record


def _vehicle(path: Path, layout: Layout) -> dict:
    location, stored = _location(path, layout)
    return {"path": location, "sha256": sha256_file(path), "stored": stored}


def _tires(vehicle: Mapping, layout: Layout) -> list[dict]:
    tires = []
    for name, path in tire_files(vehicle, layout).items():
        location, _ = _location(path, layout)
        tires.append({"id": name, "path": location, "sha256": sha256_file(path) if path.is_file() else None})
    return tires


def _bundle(vehicle_path: Path, vehicle_sha256: str, layout: Layout) -> dict | None:
    data = read_bundle(vehicle_path)
    if data is None:
        return None
    path = bundle_path(vehicle_path)
    return {
        "path": _location(path, layout)[0],
        "sha256": sha256_file(path),
        "bundle_id": plain(data.get("bundle_id")),
        "status": plain(data.get("status")),
        "describes_vehicle": describes_vehicle(data, vehicle_sha256),
    }


def _bundle_warnings(bundle: Mapping | None) -> list[str]:
    if bundle is None:
        return []
    warnings = []
    if bundle["status"] == "superseded":
        warnings.append(f"bundle {bundle['bundle_id']} is superseded. The stale check reports this result STALE.")
    if bundle["describes_vehicle"] is not True:
        problem = "is missing" if bundle["describes_vehicle"] is None else "is not the SHA-256 of the file"
        warnings.append(
            f"bundle {bundle['bundle_id']} does not describe the vehicle file. Its members.vehicle.sha256 {problem}. "
            "Until you update bundle.yml, the stale check does not report this result CURRENT."
        )
    return warnings


def _study_code(layout: Layout, study: str) -> dict:
    files = {p.relative_to(layout.repo).as_posix(): sha256_file(p) for p in study_sources(layout, study).lhre}
    lines = "".join(f"{name}\0{digest}\n" for name, digest in sorted(files.items()))
    return {"sha256": hashlib.sha256(lines.encode()).hexdigest(), "files": files}


def _toolchain() -> dict:
    try:
        image = json.loads(TOOLCHAIN_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        image = None
    except (OSError, ValueError) as error:
        image = {"error": f"cannot read {TOOLCHAIN_FILE}: {error}"}
    return {"python": platform.python_version(), "image_toolchain": image}


def build(argv: list[str], env: Mapping[str, str], layout: Layout) -> dict:
    study = env["STUDY"]
    vehicle_path = Path(env.get("BOBSIM_VEHICLE") or layout.repo / "vehicle" / "vehicle.yml")
    vehicle = _vehicle(vehicle_path, layout)
    vehicle_data = yaml.safe_load(vehicle_path.read_text(encoding="utf-8"))
    keys = dependency_keys(study, vehicle_path, layout)
    return {
        "schema": SCHEMA,
        "study": study,
        "variant": env.get("STUDY_VARIANT") or None,
        "argv": list(argv),
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "lhre": _repo(layout.repo, env, "LHRE_SHA"),
        "bobsim": _repo(layout.bobsim, env, "BOBSIM_SHA"),
        "boblib": _boblib(layout, env),
        "image": {"id": env.get("LHRE_IMAGE_ID") or None},
        "toolchain": _toolchain(),
        "vehicle": vehicle,
        "bundle": _bundle(vehicle_path, vehicle["sha256"], layout),
        "tires": _tires(vehicle_data, layout),
        "study_code": _study_code(layout, study),
        "invalidation": {"method": METHOD, "keys": keys.values, "unavailable": keys.unavailable},
    }


def main(argv: list[str] | None = None, layout: Layout | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("Usage: python tools/provenance.py studies/<name>/run.py [study arguments]", file=sys.stderr)
        return 2
    record = build(argv, os.environ, layout or Layout.default())
    failures = [f"{name}: {record[name]['error']}" for name in GIT_REPOS if record[name].get("error")]
    if failures and os.environ.get("LHRE_REQUIRE_GIT") == "1":
        print("provenance.py: LHRE_REQUIRE_GIT=1, but git cannot read these repositories:", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 1
    for warning in _bundle_warnings(record["bundle"]):
        print(f"provenance.py: warning: {warning}", file=sys.stderr)
    unavailable = record["invalidation"]["unavailable"]
    if unavailable:
        print(
            f"provenance.py: {len(unavailable)} invalidation keys are unavailable. "
            f"The stale check reports UNKNOWN for them: {', '.join(unavailable)}",
            file=sys.stderr,
        )
    out_dir = Path(os.environ["OUT_DIR"])
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "provenance.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
