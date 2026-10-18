import datetime
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(fmu: Path) -> None:
    omc = subprocess.run(['omc', '--version'], capture_output=True, text=True).stdout.strip()
    record = {
        'bobsim_sha': os.environ.get('BOBSIM_SHA', 'unknown'),
        'vehicle_sha256': sha256(Path(os.environ['BOBSIM_VEHICLE'])),
        'fmu_sha256': sha256(fmu),
        'openmodelica': omc,
        'fmi': '2.0 co-simulation, CVODE',
        'platform': f'{platform.system()} {platform.machine()}',
        'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
    }
    fmu.with_suffix('.json').write_text(json.dumps(record, indent=2) + '\n')


if __name__ == '__main__':
    main(Path(sys.argv[1]))
