"""make car: run one BobSim target on our car and keep what it made.

BobSim takes the car and the output folder from outside (BOBSIM_VEHICLE,
BOBSIM_OUT) and stages the car's Modelica records in a copy of BobLib, so
nothing here writes into bobsim/. This tool checks the inputs, runs the
target, copies the outputs that matter out of the BobSim output folder, and
writes identity.json. Runs inside the sim container: make car starts it.
"""

import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import yaml

from tools import car_identity

BOBSIM = Path("bobsim")
COMMITTED_VEHICLE = "../vehicle/vehicle.yml"
TARGET = re.compile(r"(?!-)(?!.*--)[A-Za-z0-9_.-]+")
CANDIDATE = re.compile(r"out/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
# BOBSIM_OUT under the run folder. Its builds outlive a run, so an unchanged
# car does not recompile; its results are cleared before each run.
CACHE = ".bobsim"
KEEP_IN_CACHE = "BuildBobLib"
FOUR_POST_CONFIG = BOBSIM / "simulations/response/mbd/four_post/four_post_eval_config.yml"
RUN_FILES = ("manifest.json", "overrides.txt", "run.log")
RESULT_CSV_LIMIT = 1024 * 1024
# The container runs git as root on files that the host or CI runner owns.
SAFE_DIRECTORY = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": "*"}


class Stop(Exception):
    """A problem found before the run folder exists. Nothing was written."""

    def __init__(self, message, status=1):
        super().__init__(message)
        self.status = status


def _git(*args):
    result = subprocess.run(
        ["git", "--no-optional-locks", *args], capture_output=True, text=True, env={**os.environ, **SAFE_DIRECTORY}, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def bobsim_changes():
    return _git("-C", str(BOBSIM), "status", "--porcelain", "--ignore-submodules=none")


def check_target(target):
    if not TARGET.fullmatch(target):
        raise Stop(f"make car: T='{target}' is not a BobSim target name.", 2)


def check_vehicle(vehicle):
    """Return True for a candidate copy, False for the committed vehicle."""
    if vehicle == COMMITTED_VEHICLE:
        candidate = False
    elif ".." not in vehicle and CANDIDATE.fullmatch(vehicle):
        candidate = True
    else:
        raise Stop(
            f"make car: VEHICLE='{vehicle}' is not a file under out/<name>/.\n"
            "The repo has one vehicle file. Put a candidate copy and its bundle.yml in out/<name>/.",
            2,
        )
    if not Path(vehicle).is_file():
        raise Stop(f"make car: {vehicle} does not exist.", 2)
    return candidate


def check_candidate(vehicle):
    bundle = Path(vehicle).parent / "bundle.yml"
    command = [sys.executable, "tools/bundle.py", "check", "--bundle", str(bundle), "--vehicle", vehicle]
    if subprocess.call(command) != 0:
        raise Stop(f"make car: {vehicle} fails the candidate check. Fix the problems above, then run make car again.")


def clear_run_folder(out):
    """Delete the last run's outputs and results, and keep the build cache."""
    out.mkdir(parents=True, exist_ok=True)
    for path in out.iterdir():
        if path.name != CACHE:
            _remove(path)
    cache = out / CACHE
    if cache.is_dir():
        for path in cache.iterdir():
            if path.name != KEEP_IN_CACHE:
                _remove(path)
        for results in (cache / KEEP_IN_CACHE).glob("*/results"):
            _remove(results)


def _remove(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def run_target(target, vehicle, out):
    """Run make -C bobsim <target> with our car and output folder. Return its exit status."""
    command = [
        "make", "-C", str(BOBSIM), target, "RUN=",
        f"BOBSIM_VEHICLE={vehicle.resolve()}", f"BOBSIM_OUT={(out / CACHE).resolve()}",
    ]
    with (out / "target.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.write(line)
            log.write(line)
        return process.wait()


def copy_outputs(out):
    """Copy the reports, metrics and run records out of the BobSim output folder."""
    cache = out / CACHE
    if FOUR_POST_CONFIG.is_file():
        shutil.copy(FOUR_POST_CONFIG, out / FOUR_POST_CONFIG.name)
    results = cache / "generated_results"
    if results.is_dir():
        files = [p for p in results.iterdir() if p.is_file() and p.name != ".gitkeep" and p.suffix != ".tir"]
        if files:
            (out / "generated_results").mkdir(exist_ok=True)
        for path in files:
            shutil.copy(path, out / "generated_results" / path.name)
    for run in sorted((cache / KEEP_IN_CACHE).glob("*/results/run_*")):
        if not run.is_dir():
            continue
        destination = out / "runs" / run.parent.parent.name / run.name
        destination.mkdir(parents=True, exist_ok=True)
        for name in RUN_FILES:
            if (run / name).is_file():
                shutil.copy(run / name, destination / name)
        for csv in run.glob("*_res.csv"):
            if csv.stat().st_size < RESULT_CSV_LIMIT:
                shutil.copy(csv, destination / csv.name)


def open_run_folder(out):
    """Let the host delete the run folder. The container writes it as root."""
    for path in [out, *out.rglob("*")]:
        if not path.is_symlink():
            path.chmod(path.stat().st_mode | (0o777 if path.is_dir() else 0o666))


def _vehicle_name(vehicle):
    try:
        data = yaml.safe_load(Path(vehicle).read_text(encoding="utf-8"))
        return str(data["vehicle"]["name"])
    except (OSError, KeyError, TypeError, yaml.YAMLError):
        return None


def _interrupted(signum, frame):
    raise KeyboardInterrupt


def main(argv):
    target, vehicle, out = argv[1], argv[2], Path(argv[3])
    try:
        check_target(target)
        candidate = check_vehicle(vehicle)
        changes = bobsim_changes()
        if changes is None:
            raise Stop("make car: git cannot read bobsim/.")
        if changes:
            raise Stop(f"make car: bobsim/ has local changes. Run make car on the pin as committed.\n{changes}")
        if candidate:
            print(f"\n== make car: Check the candidate {vehicle}")
            check_candidate(vehicle)
    except Stop as stop:
        print(stop, file=sys.stderr)
        return stop.status

    signal.signal(signal.SIGTERM, _interrupted)
    signal.signal(signal.SIGHUP, _interrupted)
    lhre_changes = _git("-C", "..", "status", "--porcelain", "--", "vehicle", "simulation")
    facts = {
        "target": target,
        "target_status": None,
        "vehicle_name": _vehicle_name(vehicle),
        "lhre_commit": _git("-C", "..", "rev-parse", "HEAD"),
        "lhre_dirty": None if lhre_changes is None else bool(lhre_changes),
        "bobsim_commit": _git("-C", str(BOBSIM), "rev-parse", "HEAD"),
        "boblib_commit": _git("-C", str(BOBSIM / "engines/boblib"), "rev-parse", "HEAD"),
        "image_name": os.environ.get("LHRE_IMAGE"),
        "image_id": os.environ.get("LHRE_IMAGE_ID"),
    }
    failures = []
    clear_run_folder(out)
    try:
        shutil.copy(vehicle, out / "vehicle.yml")
        print(f"\n== make car: Run make -C bobsim {target} on {vehicle}")
        facts["target_status"] = run_target(target, out / "vehicle.yml", out)
        if facts["target_status"] != 0:
            failures.append(f"make -C bobsim {target} failed with exit status {facts['target_status']}.")
    except KeyboardInterrupt:
        failures.append("make car was interrupted.")
    finally:
        copy_outputs(out)
        changes = bobsim_changes()
        facts["bobsim_clean_after"] = None if changes is None else not changes
        if changes:
            failures.append("bobsim/ has local changes after the run.")
            print(f"make car: bobsim/ has local changes after the run:\n{changes}", file=sys.stderr)
        identity = car_identity.build_identity(out, Path(vehicle), facts, failures)
        (out / "identity.json").write_text(car_identity.json_text(identity), encoding="utf-8")
        open_run_folder(out)
    print(f"\n{out / 'identity.json'}: status {identity['status']}")
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
