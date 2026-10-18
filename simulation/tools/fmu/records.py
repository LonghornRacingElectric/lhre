import os
import sys
from pathlib import Path

import yaml

try:
    from app.modelica_generator import generate_modelica_stack
except ImportError:  # BobSim before its app/ and engines/ layout
    from _5_App.modelica_generator import generate_modelica_stack

BOBSIM = Path('bobsim').resolve()
FMI_MODEL = 'Experiments/Standards/VehicleFMI.mo'


def write(work: Path) -> str:
    source = Path(os.environ['BOBSIM_VEHICLE'])
    data = yaml.safe_load(source.read_text(encoding='utf-8'))
    boblib = (work / 'BobLib').resolve()
    data['paths'] = {**(data.get('paths') or {}), 'boblib': str(boblib)}
    # VehicleFMI does not use the record's solved QSS pose, so do not require one.
    data['modelica_initialization'] = 'legacy'
    vehicle = (work / 'vehicle.yml').resolve()
    vehicle.write_text(yaml.safe_dump(data, sort_keys=False), encoding='utf-8')
    record = generate_modelica_stack(vehicle, root=BOBSIM).record_name
    if record not in (boblib / FMI_MODEL).read_text(encoding='utf-8'):
        raise SystemExit(f'{source}: BobLib VehicleFMI does not use {record}. '
                         'Keep the vehicle.name and the architecture of the BobLib record.')

    bias = float(data['brake']['front_bias'])
    (work / 'OrionFMI.mo').write_text(
        'model OrionFMI\n'
        '  extends BobLib.Experiments.Standards.VehicleFMI(\n'
        f'    headless = true, brakes(frontBrakeBias = {bias}));\n'
        'end OrionFMI;\n', encoding='utf-8')
    return f'{record} and brake bias {bias} from {source}'


if __name__ == '__main__':
    print(write(Path(sys.argv[1])))
