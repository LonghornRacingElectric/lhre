import csv
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

from tools.parallel import map_cases

BOBSIM = Path("bobsim").resolve()
BOBLIB_PACKAGE = "_0_Utils/external/BobLib/BobLib"
EVAL_CONFIG = Path("_3_StandardSim/FourPostEval/four_post_eval_config.yml")
REFUSED_OVERRIDE = "It is not possible to override"


def copy_vehicle(car, workspace):
    vehicle = yaml.safe_load(car.read_text())
    paths = vehicle.setdefault("paths", {})
    tires = paths.get("tire_templates")
    if tires and not Path(tires).is_absolute():
        paths["tire_templates"] = str((BOBSIM / tires).resolve())
    paths["boblib"] = BOBLIB_PACKAGE
    (workspace / "vehicle.yml").write_text(yaml.safe_dump(vehicle, sort_keys=False))


def eval_config(workspace, metrics_path):
    config = yaml.safe_load((workspace / EVAL_CONFIG).read_text())
    config["report"]["enabled"] = False
    config["report"]["metrics_csv_path"] = str(metrics_path)
    path = workspace / "four_post_sweep_config.yml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def run_car(case):
    car, template, out_dir = case
    workspace = template.parent / car.stem / "bobsim"
    metrics_path = out_dir / f"{car.stem}.csv"
    log_path = out_dir / "logs" / f"{car.stem}.log"
    env = {**os.environ, "PYTHONPATH": str(workspace)}
    started = time.perf_counter()
    shutil.rmtree(workspace.parent, ignore_errors=True)
    shutil.copytree(template, workspace)
    try:
        copy_vehicle(car, workspace)
        config = eval_config(workspace, metrics_path)
        steps = (
            [sys.executable, "-m", "_5_App.modelica_generator", "vehicle.yml", "--write"],
            ["make", "standard-build-four-post"],
            [sys.executable, "-m", "_3_StandardSim.FourPostEval.four_post_eval_sim", str(config)],
        )
        with log_path.open("w") as log:
            for step in steps:
                log.write(f"$ {' '.join(step)}\n")
                log.flush()
                result = subprocess.run(step, cwd=workspace, env=env, stdout=log, stderr=subprocess.STDOUT)
                if result.returncode != 0:
                    return {"car": car.stem, "error": f"'{step[-1]}' exited with {result.returncode}. See {log_path}."}
        for run_log in workspace.glob("_3_StandardSim/BuildBobLib/FourPostSim/results/run_*/run.log"):
            if REFUSED_OVERRIDE in run_log.read_text(errors="replace"):
                return {"car": car.stem, "error": f"The four-post refused a parameter override. See {run_log}."}
        with metrics_path.open() as f:
            metrics = {row["metric"]: row["value"] for row in csv.DictReader(f)}
        return {"car": car.stem, "wall_s": round(time.perf_counter() - started), **metrics}
    finally:
        shutil.rmtree(workspace.parent, ignore_errors=True)


def write_summary(rows, path):
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main():
    template, cars_dir = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    cars = sorted(cars_dir.glob("*.yml"))
    if not cars:
        raise SystemExit(f"{cars_dir} has no .yml files.")
    out_dir = cars_dir.parent / "four-post"
    shutil.rmtree(out_dir, ignore_errors=True)
    (out_dir / "logs").mkdir(parents=True)
    started = time.perf_counter()
    rows = map_cases(run_car, [(car, template, out_dir) for car in cars])
    write_summary(rows, out_dir / "summary.csv")
    failed = [row for row in rows if "error" in row]
    print(f"{len(cars) - len(failed)} of {len(cars)} cars done in {time.perf_counter() - started:.0f} s. Results: {out_dir}")
    for row in failed:
        print(f"FAIL {row['car']}: {row['error']}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
