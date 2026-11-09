import math
import os
import zipfile
from types import SimpleNamespace

import pytest

from fakes import description

from vcusil import plant
from vcusil.plant import (INPUTS, OUTPUTS, PARAMETERS, InterfaceError, Plant, PlantInputs,
                          capture_output, check_binary, check_interface)


def test_interface_accepts_the_torque_fmu_shape():
    refs = check_interface(description())
    assert set(refs) == {*INPUTS, *OUTPUTS, *(name for name, _ in PARAMETERS)}


@pytest.mark.parametrize('name, change, message', [
    ('motorTorqueCommand', None, 'motorTorqueCommand: missing'),
    ('wheelSpeedRL', SimpleNamespace(name='wheelSpeedRL', type='Real', causality='output',
                                     unit='rpm', valueReference=900), 'unit rpm'),
    ('dcBusCurrent', SimpleNamespace(name='dcBusCurrent', type='Real', causality='local',
                                     unit='A', valueReference=901), 'want Real output'),
    ('pVehicle.pBattery.Ns', SimpleNamespace(name='pVehicle.pBattery.Ns', type='Real',
                                             causality='parameter', unit=None,
                                             valueReference=902), 'want Integer parameter'),
])
def test_interface_rejects_a_different_fmu(name, change, message):
    with pytest.raises(InterfaceError, match=message):
        check_interface(description(**{name: change}))


def test_interface_needs_fmi2_co_simulation():
    d = description()
    d.coSimulation = None
    with pytest.raises(InterfaceError, match='co-simulation'):
        check_interface(d)


def elf_fmu(tmp_path, machine: int, member='binaries/linux64/Car.so', data=None):
    header = bytearray(64)
    header[:4] = b'\x7fELF'
    header[4], header[5] = 2, 1  # 64-bit, little-endian
    header[18:20] = machine.to_bytes(2, 'little')
    path = tmp_path / 'car.fmu'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr(member, bytes(header) if data is None else data)
    return path


def test_binary_must_match_the_host_cpu(tmp_path):
    arm = elf_fmu(tmp_path, 183)
    check_binary(arm, 'Car', machine='aarch64')
    with pytest.raises(InterfaceError, match='ELF machine 183.*x86_64'):
        check_binary(arm, 'Car', machine='x86_64')
    check_binary(elf_fmu(tmp_path, 62), 'Car', machine='x86_64')
    with pytest.raises(InterfaceError, match='has no binaries/linux64/Other.so'):
        check_binary(arm, 'Other', machine='aarch64')
    with pytest.raises(InterfaceError, match='not an ELF'):
        check_binary(elf_fmu(tmp_path, 0, data=b'MZ' + bytes(30)), 'Car', machine='aarch64')


def test_runtime_output_is_captured(tmp_path):
    log = tmp_path / 'fmu.log'
    with capture_output(log):
        os.write(1, b'from fd 1\n')
        os.write(2, b'from fd 2\n')
    assert log.read_bytes() == b'from fd 1\nfrom fd 2\n'


def test_non_finite_outputs_and_inputs_are_plant_failures():
    p = Plant.__new__(Plant)
    p._slave = SimpleNamespace(getReal=lambda refs: [0.0] * (len(refs) - 1) + [math.nan],
                               setReal=lambda *_: None, doStep=lambda **_: None)
    p._out_refs = list(range(len(OUTPUTS)))
    p._read_refs = p._out_refs
    p._truth_keys = []
    p.slip_from_states = False
    p._in_refs = list(range(len(INPUTS)))
    p.step_s = 0.003
    with pytest.raises(plant.PlantError, match='wheelSpeedRR'):
        p.read()
    with pytest.raises(plant.PlantError, match='non-finite plant input'):
        p.step(0, PlantInputs(motor_torque_nm=math.inf))


def test_truth_keeps_non_finite_values_as_gaps():
    """A NaN in truth is a gap in the log, never a failed run; the slip
    derived from a NaN state is a gap too, under the same key."""
    keys = ['velX', 'slipU_FL', 'slipV_FL', 'slipU_RL', 'slipV_RL']
    truth = [12.5, math.nan, 0.1, 0.05, math.inf]
    p = Plant.__new__(Plant)
    p._slave = SimpleNamespace(getReal=lambda refs: [1.0] * len(OUTPUTS) + truth)
    p._read_refs = list(range(len(OUTPUTS) + len(keys)))
    p._truth_keys = keys
    p.slip_from_states = True
    out = p.read()
    assert out == dict.fromkeys(OUTPUTS, 1.0)
    t = p.truth
    assert t['velX'] == 12.5 and t['slipU_FL'] is None and t['slipV_RL'] is None
    assert t['kappa_FL'] is None and t['alpha_FL'] == pytest.approx(math.atan2(0.1, 0.5))
    assert t['kappa_RL'] == pytest.approx(0.1) and t['alpha_RL'] is None
    # Corners the FMU does not expose read as gaps, so every run has the same keys.
    assert t['kappa_FR'] is None and t['alpha_RR'] is None
    # Without the zero-relaxation check passing, slip is not derived at all.
    p.slip_from_states = False
    p.read()
    assert not any(k.startswith(('kappa_', 'alpha_')) for k in p.truth)


def test_real_fmu_initializes_as_documented(fmu_path):
    with Plant(fmu_path, soc_start=0.9) as car:
        assert car.series_cells == 140
        assert car.peak_timer is True
        assert car.soc_start == pytest.approx(0.9)
        y = car.read()
        assert y['rearU'] == pytest.approx(0.0, abs=1e-9)
        assert 570.0 < y['dcBusVoltage'] < 580.0  # open circuit near 4.1 V/cell


def test_plant_alone_survives_the_launch_torque_profile(fmu_path):
    """The firmware's launch, replayed as plain torque: if this fails, a
    harness failure on the same profile is the model, not the firmware."""
    peak, low = 0.0, 0.0
    with Plant(fmu_path) as car:
        for k in range(7002 // 3):
            t = k * 3
            y = car.read()
            peak, low = max(peak, y['rearU']), min(low, y['rearU'])
            ramp = min(max((t - 2000) / 12, 0.0), 1.0) if t < 6000 else 0.0
            car.step(k, PlantInputs(motor_torque_nm=210.0 * ramp, regen_limit_nm=230.0,
                                    brake=350 / 1500 if t < 1500 else 0.0))
    assert peak > 25.0
    assert low > -0.05


def test_step_sets_each_input_by_name():
    refs = check_interface(description())
    p = Plant.__new__(Plant)
    sent = {}
    p._slave = SimpleNamespace(setReal=lambda r, v: sent.update(zip(r, v)),
                               doStep=lambda **_: None)
    p._in_refs = [refs[n] for n in INPUTS]
    p.step_s = 0.003
    p.step(0, PlantInputs(motor_torque_nm=1.0, regen_limit_nm=2.0, brake=0.3, steering_rad=0.4))
    assert sent == {refs['motorTorqueCommand']: 1.0, refs['regenTorqueLimitCommand']: 2.0,
                    refs['brakePedalCommand']: 0.3, refs['steeringAngleCommand']: 0.4}


def test_step_time_is_k_steps_of_the_firmware_tick():
    steps = []
    p = Plant.__new__(Plant)
    p._slave = SimpleNamespace(setReal=lambda *_: None, doStep=lambda **kw: steps.append(kw))
    p._in_refs = list(range(len(INPUTS)))
    p.step_s = 0.003
    p.step(7, PlantInputs())
    assert steps == [{'currentCommunicationPoint': 7 * 0.003, 'communicationStepSize': 0.003}]
    assert Plant.__init__.__defaults__[-1] == 0.003  # step_s default is the 3 ms tick


def test_failure_lines_keep_error_and_fatal_messages_once():
    p = Plant.__new__(Plant)
    p.messages = [(1, 'logStatusWarning', 'warn'), (3, 'logStatusError', 'CVODE: failed'),
                  (3, 'logStatusError', 'CVODE: failed'), (4, 'logStatusFatal', 'fatal')]
    assert p.failure_lines() == ['CVODE: failed', 'fatal']


def test_a_file_that_is_not_an_fmu_is_an_interface_error(tmp_path):
    bogus = tmp_path / 'car.fmu'
    bogus.write_bytes(b'not a zip')
    with pytest.raises(InterfaceError, match='not a readable FMU'):
        Plant(bogus)


def test_soc_must_be_a_fraction(tmp_path):
    with pytest.raises(ValueError, match='fraction'):
        Plant(tmp_path / 'unused.fmu', soc_start=90.0)


def test_close_frees_the_instance_and_the_library(tmp_path):
    calls = []
    p = Plant.__new__(Plant)
    p._slave = SimpleNamespace(terminate=lambda: calls.append('terminate'),
                               freeInstance=lambda: calls.append('freeInstance'),
                               freeLibrary=lambda: calls.append('freeLibrary'))
    p._initialized = p._instantiated = True
    p._directory = str(tmp_path / 'unzipped')
    (tmp_path / 'unzipped').mkdir()
    p.close()
    assert calls == ['terminate', 'freeInstance', 'freeLibrary']
    assert not (tmp_path / 'unzipped').exists()
    p.close()  # idempotent
    assert calls == ['terminate', 'freeInstance', 'freeLibrary']
