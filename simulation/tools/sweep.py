import csv
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

import yaml

from tools.parallel import map_cases


class Sim(NamedTuple):
    model: str
    build_target: str
    module: str
    config: str


SIMS = {
    "four-post": Sim("FourPostSim", "standard-build-four-post", "FourPostEval.four_post_eval_sim", "FourPostEval/four_post_eval_config.yml"),
    "steady-state": Sim("VehicleSim", "standard-build", "SteadyStateEval.steady_state_eval_sim", "SteadyStateEval/steady_state_eval_config.yml"),
    "ramp-steer": Sim("VehicleSim", "standard-build", "RampSteerEval.ramp_steer_eval_sim", "RampSteerEval/ramp_steer_eval_config.yml"),
    "transient": Sim("VehicleSim", "standard-build", "TransientEval.transient_eval_sim", "TransientEval/transient_eval_config.yml"),
}
BOBSIM = Path("bobsim").resolve()
BOBLIB_PACKAGE = "_0_Utils/external/BobLib/BobLib"
CACHE = Path("out/.cache/sweep").resolve()
REFUSED_OVERRIDE = "It is not possible to override"


def digest(*parts):
    sha = hashlib.sha256()
    for part in parts:
        sha.update(part if isinstance(part, bytes) else str(part).encode())
        sha.update(b"\0")
    return sha.hexdigest()[:16]


def tree_digest(root):
    files = sorted(path for path in root.rglob("*") if path.is_file())
    return digest(*(item for path in files for item in (path.relative_to(root).as_posix(), path.read_bytes())))


def build_dir(workspace, sim):
    return workspace / "_3_StandardSim/BuildBobLib" / sim.model


def build_files(directory, sim):
    executable = f"BobLib.Experiments.Standards.{sim.model}"
    return [
        path for path in directory.iterdir()
        if path.is_file() and not path.is_symlink() and (path.name == executable or path.name.endswith(("_init.xml", "_info.json", ".bin")))
    ]


def copy_vehicle(car, workspace):
    vehicle = yaml.safe_load(car.read_text())
    paths = vehicle.setdefault("paths", {})
    tires = paths.get("tire_templates")
    if tires and not Path(tires).is_absolute():
        paths["tire_templates"] = str((BOBSIM / tires).resolve())
    paths["boblib"] = BOBLIB_PACKAGE
    (workspace / "vehicle.yml").write_text(yaml.safe_dump(vehicle, sort_keys=False))


def run_logged(step, workspace, log_path):
    with log_path.open("a") as log:
        log.write(f"$ {' '.join(step)}\n")
        log.flush()
        result = subprocess.run(step, cwd=workspace, env={**os.environ, "PYTHONPATH": str(workspace)}, stdout=log, stderr=subprocess.STDOUT)
    return None if result.returncode == 0 else f"'{step[-1]}' exited with {result.returncode}. See {log_path}."


def prepare(case):
    name, car, template, out_dir = case
    sim = SIMS[name]
    workspace = template.parent / car.stem / "bobsim"
    log_path = out_dir / "logs" / f"{car.stem}.log"
    shutil.rmtree(workspace.parent, ignore_errors=True)
    shutil.copytree(template, workspace)
    copy_vehicle(car, workspace)
    error = run_logged([sys.executable, "-m", "_5_App.modelica_generator", "vehicle.yml", "--write"], workspace, log_path)
    if error:
        return {"car": car.stem, "error": error}
    build_key = digest(os.environ["BOBSIM_SHA"], sim.model, tree_digest(workspace / BOBLIB_PACKAGE))
    result_key = digest(build_key, name, (template / "_3_StandardSim" / sim.config).read_bytes(), (workspace / "vehicle.yml").read_bytes())
    return {"car": car.stem, "workspace": workspace, "log": log_path, "build_key": build_key, "result_key": result_key}


def build(case):
    name, job = case
    sim = SIMS[name]
    error = run_logged(["make", sim.build_target], job["workspace"], job["log"])
    if error:
        return error
    staging = CACHE / "builds" / sim.model / f".{job['build_key']}-{os.getpid()}"
    staging.mkdir(parents=True)
    for path in build_files(build_dir(job["workspace"], sim), sim):
        shutil.copy2(path, staging)
    try:
        staging.rename(staging.parent / job["build_key"])
    except OSError:
        shutil.rmtree(staging)
    return None


def evaluate(case):
    name, job, out_dir = case
    sim = SIMS[name]
    workspace = job["workspace"]
    started = time.perf_counter()
    try:
        target = build_dir(workspace, sim)
        target.mkdir(parents=True, exist_ok=True)
        for path in (CACHE / "builds" / sim.model / job["build_key"]).iterdir():
            if not (target / path.name).exists():
                shutil.copy2(path, target)
        config = yaml.safe_load((workspace / "_3_StandardSim" / sim.config).read_text())
        report = config.setdefault("report", {})
        report.update(enabled=False, output_path=str(workspace / "sweep.pdf"), metrics_csv_path=str(workspace / "sweep_metrics.csv"))
        config_path = workspace / "sweep_config.yml"
        config_path.write_text(yaml.safe_dump(config, sort_keys=False))
        error = run_logged([sys.executable, "-m", f"_3_StandardSim.{sim.module}", str(config_path)], workspace, job["log"])
        if error:
            return {"car": job["car"], "error": error}
        for run_log in target.glob("results/run_*/run.log"):
            if REFUSED_OVERRIDE in run_log.read_text(errors="replace"):
                return {"car": job["car"], "error": f"The model refused a parameter override. See {job['log']}."}
        result = CACHE / "results" / name / f"{job['result_key']}.csv"
        result.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workspace / "sweep_metrics.csv", result)
        return {"car": job["car"], "wall_s": round(time.perf_counter() - started)}
    finally:
        shutil.rmtree(workspace.parent, ignore_errors=True)


def read_metrics(path):
    metrics = {}
    with path.open() as f:
        for row in csv.DictReader(f):
            key = ".".join(filter(None, (row.get("group"), row["metric"])))
            count = sum(1 for existing in metrics if existing == key or existing.startswith(f"{key}#"))
            metrics[f"{key}#{count + 1}" if count else key] = row["value"]
    return metrics


def write_summary(rows, path):
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main():
    name, template, cars_dir = sys.argv[1], Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
    if name not in SIMS:
        raise SystemExit(f"SIM must be one of: {', '.join(SIMS)}.")
    sim = SIMS[name]
    cars = sorted(cars_dir.glob("*.yml"))
    if not cars:
        raise SystemExit(f"{cars_dir} has no .yml files.")
    out_dir = cars_dir.parent / name
    shutil.rmtree(out_dir, ignore_errors=True)
    (out_dir / "logs").mkdir(parents=True)
    started = time.perf_counter()

    jobs = map_cases(prepare, [(name, car, template, out_dir) for car in cars])
    rows = {job["car"]: {"car": job["car"]} for job in jobs}
    pending = []
    for job in jobs:
        if "error" in job:
            rows[job["car"]]["error"] = job["error"]
        elif (CACHE / "results" / name / f"{job['result_key']}.csv").exists():
            rows[job["car"]]["source"] = "result cache"
            shutil.rmtree(job["workspace"].parent, ignore_errors=True)
        else:
            pending.append(job)

    missing = {}
    for job in pending:
        if not (CACHE / "builds" / sim.model / job["build_key"]).is_dir():
            missing.setdefault(job["build_key"], job)
    build_errors = dict(zip(missing, map_cases(build, [(name, job) for job in missing.values()])))

    runnable = []
    for job in pending:
        error = build_errors.get(job["build_key"])
        if error:
            rows[job["car"]]["error"] = error
            shutil.rmtree(job["workspace"].parent, ignore_errors=True)
        else:
            rows[job["car"]]["source"] = "new build" if job["build_key"] in missing else "build cache"
            runnable.append(job)
    for result in map_cases(evaluate, [(name, job, out_dir) for job in runnable]):
        rows[result["car"]].update(result)

    for job in jobs:
        result = CACHE / "results" / name / f"{job.get('result_key')}.csv"
        if "error" not in rows[job["car"]] and result.exists():
            shutil.copy2(result, out_dir / f"{job['car']}.csv")
            rows[job["car"]].update(read_metrics(result))
    write_summary(list(rows.values()), out_dir / "summary.csv")

    failed = [row for row in rows.values() if "error" in row]
    print(
        f"{len(cars) - len(failed)} of {len(cars)} cars done in {time.perf_counter() - started:.0f} s: "
        f"{len(missing)} builds, {len(runnable)} evals. Results: {out_dir}"
    )
    for row in failed:
        print(f"FAIL {row['car']}: {row['error']}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
