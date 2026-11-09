"""Stand-ins shared by the tests: a scripted car and a fake FMU description."""

from dataclasses import dataclass
from types import SimpleNamespace

from vcusil.plant import INPUTS, OUTPUTS, PARAMETERS, PlantError, PlantInputs
from vcusil.scenario import Launch

WHEEL_RADIUS_M = 0.2045
FINAL_DRIVE = 3.31


class ToyCar:
    """A longitudinal car that records how the loop drives it."""

    series_cells = 140

    def __init__(self, fail_at_step: int | None = None, nan_at_step: int | None = None):
        self.v = 0.0
        self.torque = 0.0
        self.calls: list[tuple[str, int]] = []
        self.inputs: list[PlantInputs] = []
        self.fail_at_step = fail_at_step
        self.nan_at_step = nan_at_step

    def read(self) -> dict[str, float]:
        k = len(self.inputs)
        self.calls.append(('read', k))
        if k == self.nan_at_step:
            raise PlantError('non-finite plant output: rearU')
        wheel = self.v / WHEEL_RADIUS_M
        y = {name: 0.0 for name in OUTPUTS}
        y.update(rearU=self.v, wheelSpeedFL=wheel, wheelSpeedFR=wheel, wheelSpeedRL=wheel,
                 wheelSpeedRR=wheel, motorSpeed=wheel * FINAL_DRIVE,
                 motorTorqueActual=self.torque, dcBusVoltage=574.0,
                 dcBusCurrent=self.torque * wheel * FINAL_DRIVE / 0.9 / 574.0)
        return y

    def step(self, k: int, u: PlantInputs) -> None:
        self.calls.append(('step', k))
        assert k == len(self.inputs), 'steps must be consecutive from 0'
        if k == self.fail_at_step:
            raise PlantError(f'step {k}: FMICallException: doStep failed')
        self.inputs.append(u)
        self.torque = u.motor_torque_nm
        self.v = max(0.0, self.v + u.motor_torque_nm * FINAL_DRIVE / WHEEL_RADIUS_M
                     / 250.0 * 0.003 - u.brake * 2.0 * 0.003)

    def failure_lines(self) -> list[str]:
        return ['CVODE: the corrector convergence test failed repeatedly']


@dataclass(frozen=True)
class Short(Launch):
    duration_ms: int = 3000


def description(**changes):
    variables = []
    ref = 0
    for name, unit in INPUTS.items():
        variables.append(SimpleNamespace(name=name, type='Real', causality='input', unit=unit,
                                         valueReference=(ref := ref + 1)))
    for name, unit in OUTPUTS.items():
        variables.append(SimpleNamespace(name=name, type='Real', causality='output', unit=unit,
                                         valueReference=(ref := ref + 1)))
    for name, kind in PARAMETERS:
        variables.append(SimpleNamespace(name=name, type=kind, causality='parameter', unit=None,
                                         valueReference=(ref := ref + 1)))
    for name, change in changes.items():
        variables = [v for v in variables if v.name != name]
        if change is not None:
            variables.append(change)
    return SimpleNamespace(fmiVersion='2.0', coSimulation=SimpleNamespace(), modelVariables=variables)
