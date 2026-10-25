import argparse
import itertools
import json
import os
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent


def load(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def bundle_id(vehicle_path):
    path = vehicle_path.with_name("bundle.yml")
    return load(path).get("bundle_id") if path.is_file() else None


def cases(study):
    names = list(study["sweep"])
    for values in itertools.product(*study["sweep"].values()):
        yield {**study["parameters"], **dict(zip(names, values))}


def solve(vehicle, case):
    mass_kg = vehicle["sprung_mass"]["mass_kg"] + vehicle["driver_mass"]["mass_kg"]
    return {**case, "mass_kg": case["scale"] * mass_kg}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=HERE / "study.yml", help="the study inputs, for a variant")
    config = parser.parse_args().config
    study = load(config)
    out = Path(os.environ["OUT_DIR"])
    vehicle_path = Path(os.environ["BOBSIM_VEHICLE"])
    if bundle_id(vehicle_path) != study["bundle"]:
        raise SystemExit(f"{config} names bundle {study['bundle']}, but the vehicle has {bundle_id(vehicle_path)}.")
    vehicle = load(vehicle_path)

    rows = [solve(vehicle, case) for case in cases(study)]

    table = ["| Scale | Mass |", "| ----- | ---- |"] + [f"| {r['scale']:.2f} | {r['mass_kg']:.1f} kg |" for r in rows]
    (out / "table.md").write_text("\n".join(table) + "\n", encoding="utf-8")
    summary = {"study": study, "rows": rows}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
    readme = {"question": study["question"], "max_mass_kg": max(r["mass_kg"] for r in rows)}
    (out / "readme.json").write_text(json.dumps(readme, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
