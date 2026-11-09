"""The car: a torque-input BobLib FMU (OrionTorqueFMI) stepped by fmpy.

The FMU contains GPL BobLib code and is never committed; the caller passes
its path. Before instantiating, the interface and the binary's CPU are
checked so a wrong FMU fails with a clear message instead of wrong physics.
"""

from __future__ import annotations

import contextlib
import ctypes
import math
import os
import platform
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

TOLERANCE = 1e-6

# Inputs this host drives. acceleratorPedalCommand exists but is inert in the
# torque FMU: the firmware, not the model, turns pedal travel into torque.
INPUTS = {
    'motorTorqueCommand': 'N.m',
    'regenTorqueLimitCommand': 'N.m',
    'brakePedalCommand': '1',
    'steeringAngleCommand': 'rad',
}

OUTPUTS = {
    'rearX': 'm',
    'rearY': 'm',
    'rearU': 'm/s',
    'rearV': 'm/s',
    'rearYaw': 'rad',
    'rearYawRate': 'rad/s',
    'motorSpeed': 'rad/s',
    'motorTorqueActual': 'N.m',
    'motorTorqueLimited': 'N.m',
    'torqueLimitFlags': '1',
    'mechanicalBrakeTorque': 'N.m',
    'dcBusVoltage': 'V',
    'dcBusCurrent': 'A',
    'busRegenPower': 'W',
    'wheelSpeedFL': 'rad/s',
    'wheelSpeedFR': 'rad/s',
    'wheelSpeedRL': 'rad/s',
    'wheelSpeedRR': 'rad/s',
}

# Fixed parameters: (name, FMI type). Set in initialization mode or read back.
SOC_START = ('pVehicle.pBattery.SOC_start', 'Real')
INITIAL_VEL = ('initialVel', 'Real')
SERIES_CELLS = ('pVehicle.pBattery.Ns', 'Integer')
PEAK_TIMER = ('motor.motor.enablePeakTimer', 'Boolean')
PARAMETERS = (SOC_START, INITIAL_VEL, SERIES_CELLS, PEAK_TIMER)

# The OpenModelica runtime's message when CVODE chatters at an event.
EVENT_GUARD = 'more than 100 state events in one communication step'

# ELF e_machine values for the hosts this runs on.
_ELF_MACHINES = {'x86_64': 62, 'amd64': 62, 'aarch64': 183, 'arm64': 183}


class InterfaceError(ValueError):
    """The FMU is not the torque-input car this host expects."""


class PlantError(RuntimeError):
    """The car model failed: a rejected step or a non-finite output."""


def check_interface(description) -> dict[str, int]:
    """Value references for every variable the host uses, by name."""
    if description.fmiVersion != '2.0' or description.coSimulation is None:
        raise InterfaceError('need an FMI 2.0 co-simulation FMU')
    variables = {v.name: v for v in description.modelVariables}
    problems = []

    def expect(name, kind, causality, unit=None):
        v = variables.get(name)
        if v is None:
            problems.append(f'{name}: missing')
            return
        if v.type != kind or v.causality != causality:
            problems.append(f'{name}: {v.type} {v.causality}, want {kind} {causality}')
        if unit is not None and (v.unit or '1') != unit:
            problems.append(f'{name}: unit {v.unit}, want {unit}')

    for name, unit in INPUTS.items():
        expect(name, 'Real', 'input', unit)
    for name, unit in OUTPUTS.items():
        expect(name, 'Real', 'output', unit)
    for name, kind in PARAMETERS:
        expect(name, kind, 'parameter')
    if problems:
        raise InterfaceError('FMU interface mismatch:\n  ' + '\n  '.join(problems))
    names = (*INPUTS, *OUTPUTS, *(name for name, _ in PARAMETERS))
    return {name: variables[name].valueReference for name in names}


def check_binary(fmu_path: str | Path, model_identifier: str,
                 machine: str | None = None) -> None:
    """Fail early if the FMU's shared library is for another CPU.

    OpenModelica files every 64-bit Linux build under binaries/linux64,
    including aarch64 ones, so the folder name says nothing about the CPU.
    """
    machine = (machine or platform.machine()).lower()
    member = f'binaries/linux64/{model_identifier}.so'
    with zipfile.ZipFile(fmu_path) as fmu:
        if member not in fmu.namelist():
            raise InterfaceError(f'{fmu_path} has no {member}')
        with fmu.open(member) as so:
            header = so.read(20)
    if header[:4] != b'\x7fELF':
        raise InterfaceError(f'{member} is not an ELF shared library')
    found = int.from_bytes(header[18:20], 'little' if header[5] == 1 else 'big')
    want = _ELF_MACHINES.get(machine)
    if found != want:
        raise InterfaceError(
            f'{member} is built for ELF machine {found}; this host ({machine}) needs '
            f'{want}. Build the FMU on this CPU (ADR-016: the artifact must match '
            f'the runtime CPU).')


@contextlib.contextmanager
def capture_output(path: Path):
    """Send file descriptors 1 and 2 to `path` while the FMU runs.

    The OpenModelica runtime prints solver failures there, not to the FMI
    logger, so they are the only record of why a step failed.
    """
    libc = ctypes.CDLL(None)
    sys.stdout.flush()
    sys.stderr.flush()
    libc.fflush(None)
    saved = os.dup(1), os.dup(2)
    with open(path, 'ab') as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
        try:
            yield
        finally:
            libc.fflush(None)
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            os.close(saved[0])
            os.close(saved[1])


@dataclass(frozen=True)
class PlantInputs:
    motor_torque_nm: float = 0.0
    regen_limit_nm: float = 0.0
    brake: float = 0.0  # 0..1 of the model's full brake torque
    steering_rad: float = 0.0


class Plant:
    """One FMU instance, initialized and ready to step from t = 0."""

    def __init__(self, fmu_path: str | Path, soc_start: float = 0.9,
                 step_s: float = 0.003):
        from ._fmpy import (FMU2Slave, defaultCallbacks, extract, fmi2CallbackFunctions,
                            fmi2CallbackLoggerTYPE, read_model_description)

        if not 0.0 <= soc_start <= 1.0:
            raise ValueError(f'soc_start {soc_start} is not a fraction in [0, 1]')
        self.messages: list[tuple[int, str, str]] = []
        self.step_s = step_s
        self._slave = None
        self._instantiated = self._initialized = False
        self._directory = None
        try:
            description = read_model_description(str(fmu_path))
        except Exception as exc:
            raise InterfaceError(f'{fmu_path} is not a readable FMU: {exc}') from exc
        self.refs = check_interface(description)
        model_identifier = description.coSimulation.modelIdentifier
        check_binary(fmu_path, model_identifier)
        self.guid = description.guid

        def on_log(_env, _instance, status, category, message):
            self.messages.append((status, category.decode(errors='replace'),
                                  message.decode(errors='replace')))

        # The callback objects must outlive the instance that calls them.
        self._callbacks = fmi2CallbackFunctions()
        self._callbacks.logger = fmi2CallbackLoggerTYPE(on_log)
        self._callbacks.allocateMemory = defaultCallbacks.allocateMemory
        self._callbacks.freeMemory = defaultCallbacks.freeMemory
        # Environment problems (disk, a missing runtime library) raise
        # OSError and are harness errors; the model refusing to start is a
        # plant failure.
        self._directory = tempfile.mkdtemp(prefix='vcusil-fmu-')
        try:
            extract(str(fmu_path), unzipdir=self._directory)
            self._slave = FMU2Slave(guid=description.guid, unzipDirectory=self._directory,
                                    modelIdentifier=model_identifier, instanceName='vcusil')
            self._slave.instantiate(callbacks=self._callbacks, loggingOn=False)
            self._instantiated = True
            self._slave.setDebugLogging(True, ['logStatusError', 'logStatusFatal'])
            self._slave.setupExperiment(startTime=0.0, tolerance=TOLERANCE)
            self._slave.enterInitializationMode()
            self._slave.setReal([self.refs[SOC_START[0]], self.refs[INITIAL_VEL[0]]],
                                [soc_start, 0.0])
            self._slave.exitInitializationMode()
            self._initialized = True
            self.series_cells = self._slave.getInteger([self.refs[SERIES_CELLS[0]]])[0]
            self.peak_timer = bool(self._slave.getBoolean([self.refs[PEAK_TIMER[0]]])[0])
            self.soc_start = self._slave.getReal([self.refs[SOC_START[0]]])[0]
            if not 100 <= self.series_cells <= 200:
                raise InterfaceError(f'implausible pack: {self.series_cells} cells in series')
        except (InterfaceError, OSError):
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise PlantError(f'initialization: {type(exc).__name__}: {exc}') from exc
        except BaseException:
            self.close()
            raise
        self._out_refs = [self.refs[name] for name in OUTPUTS]
        self._in_refs = [self.refs[name] for name in INPUTS]

    def read(self) -> dict[str, float]:
        """Outputs at the current communication point. Read before setting
        inputs: several outputs feed through directly from them."""
        values = self._slave.getReal(self._out_refs)
        out = dict(zip(OUTPUTS, values))
        bad = [name for name, v in out.items() if not math.isfinite(v)]
        if bad:
            raise PlantError(f'non-finite plant output: {", ".join(bad)}')
        return out

    def step(self, k: int, u: PlantInputs) -> None:
        """Advances from t = k * step_s to (k + 1) * step_s with `u` held."""
        values = (u.motor_torque_nm, u.regen_limit_nm, u.brake, u.steering_rad)
        if not all(math.isfinite(v) for v in values):
            raise PlantError(f'non-finite plant input {u}')
        try:
            self._slave.setReal(self._in_refs, list(values))
            self._slave.doStep(currentCommunicationPoint=k * self.step_s,
                               communicationStepSize=self.step_s)
        except Exception as exc:
            raise PlantError(f'step {k} (t = {k * self.step_s:.3f} s): '
                             f'{type(exc).__name__}: {exc}') from exc

    def failure_lines(self) -> list[str]:
        """The runtime's own error and fatal messages, deduplicated."""
        lines = [message for status, _, message in self.messages if status >= 3]
        return list(dict.fromkeys(lines))

    def close(self) -> None:
        slave, self._slave = self._slave, None
        if slave is not None:
            if self._initialized:
                with contextlib.suppress(Exception):
                    slave.terminate()
            if self._instantiated:
                with contextlib.suppress(Exception):
                    slave.freeInstance()
            with contextlib.suppress(Exception):
                slave.freeLibrary()
        self._initialized = self._instantiated = False
        if self._directory is not None:
            shutil.rmtree(self._directory, ignore_errors=True)
            self._directory = None

    def __enter__(self) -> Plant:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
