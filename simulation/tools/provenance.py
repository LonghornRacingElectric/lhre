"""Write out/<study>/provenance.json before a study runs."""

import datetime
import hashlib
import json
import os
from pathlib import Path


def main() -> None:
    out_dir = Path(os.environ["OUT_DIR"])
    out_dir.mkdir(parents=True, exist_ok=True)
    vehicle = Path(os.environ["BOBSIM_VEHICLE"])
    record = {
        "study": os.environ["STUDY"],
        "bobsim_sha": os.environ["BOBSIM_SHA"],
        "vehicle_sha256": hashlib.sha256(vehicle.read_bytes()).hexdigest(),
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }
    (out_dir / "provenance.json").write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    main()
