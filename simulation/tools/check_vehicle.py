"""Fail if BobSim cannot load //vehicle:vehicle.yml."""

import os
import sys
from pathlib import Path

from _0_Utils.vehicle_io import load_yaml, tire_templates_root
from _2_EnvelopeSim.vehicle_loader import load_active_envelope_inputs

SCHEMA = "boblib.vehicle.v1"


def main() -> int:
    path = Path(os.environ["BOBSIM_VEHICLE"])
    data = load_yaml(path)
    if data.get("schema") != SCHEMA:
        print(f"{path}: schema is {data.get('schema')!r}, expected {SCHEMA!r}")
        return 1

    tires = tire_templates_root(data)
    for side in ("front", "rear"):
        template = data[side]["tire"]["template"]
        if not (tires / f"{template}.tir").is_file():
            print(f"{path}: {side} tire {template!r} is not in {tires}")
            return 1

    inputs = load_active_envelope_inputs(path)
    print(f"OK: {data['vehicle']['name']} loads in BobSim ({type(inputs).__name__})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
