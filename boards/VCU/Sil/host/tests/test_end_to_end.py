"""The real firmware against the real car model. Needs VCU_SIL_LIB and VCU_SIL_FMU."""

import json
import os

import pytest

from vcusil import __main__ as cli
from vcusil import cosim, report
from vcusil.firmware import STATE_DRIVE, Firmware
from vcusil.plant import OUTPUTS, Plant, PlantInputs
from vcusil.scenario import Launch


@pytest.fixture(scope='module')
def launch_run(lib, fmu_path, tmp_path_factory):
    out = tmp_path_factory.mktemp('launch')
    status = cli.main(['--lib', os.environ['VCU_SIL_LIB'], '--fmu', str(fmu_path),
                       '--out', str(out)])
    return status, out


def test_launch_completes_and_the_car_accelerates(launch_run):
    status, out = launch_run
    assert status == cli.EXIT_COMPLETED
    meta = json.loads((out / 'meta.json').read_text())
    s = meta['summary']
    assert meta['status'] == 'completed'
    assert s['states'] == ['IDLE', 'PRECHARGE', 'TS_ACTIVE', 'RTD_WAIT', 'DRIVE']
    assert 1020 <= s['first_drive_ms'] <= 1026
    assert s['left_drive_after_entering'] is False
    assert s['faults_seen'] == ['SHUTDOWN_OPEN']  # boot only, before the loop debounce
    assert s['peak_speed_m_s'] > 25.0
    # Rolling backward is invisible to the firmware (wheel sensors report |w|),
    # so it is checked here.
    assert s['min_speed_m_s'] > -0.05
    assert s['saturated_rows'] == 0
    assert s['bspd_condition_ms'] == 0
    # The stock torque map keeps DC power just under the 70 kW target; the
    # power cap itself never engages in this launch.
    assert 60.0 < s['peak_dc_power_kw'] < 70.0
    assert meta['plant']['series_cells'] == 140
    assert meta['fmu']['verified'] is True
    for name in ('trace.csv', 'plot.png', 'console.log', 'fmu.log', 'summary.txt'):
        assert (out / name).stat().st_size > 0, name


def test_the_car_gets_exactly_the_decoded_command(launch_run):
    _, out = launch_run
    rows = report.read_trace(out / 'trace.csv')
    for r in rows:
        if r['fw_inverter_enable']:
            assert r['u_motor_torque_nm'] == r['fw_torque_request_nm']
        else:
            assert r['u_motor_torque_nm'] == 0.0
    drive = [r for r in rows if r['fw_state'] == STATE_DRIVE]
    assert all(r['fw_inverter_enable'] == 1 for r in drive)
    assert max(r['u_motor_torque_nm'] for r in rows) == pytest.approx(210.0)


def test_the_trace_replays_bit_for_bit(launch_run, fmu_path):
    """A fresh model fed the recorded inputs reproduces every recorded output,
    so the trace holds everything the model saw."""
    _, out = launch_run
    rows = report.read_trace(out / 'trace.csv')
    with Plant(fmu_path) as car:
        for k, r in enumerate(rows):
            y = car.read()
            assert all(y[name] == r[name] for name in OUTPUTS), k
            car.step(k, PlantInputs(r['u_motor_torque_nm'], r['u_regen_limit_nm'],
                                    r['u_brake'], r['u_steering_rad']))


def test_two_runs_are_identical(lib, fmu_path):
    def once():
        with Firmware(lib) as fw, Plant(fmu_path) as car:
            return cosim.run(Launch(duration_ms=3000), fw, car).rows
    assert once() == once()


def test_the_held_brake_reaches_the_model(launch_run):
    _, out = launch_run
    rows = report.read_trace(out / 'trace.csv')
    held = [r for r in rows if 3 <= r['t_ms'] < 1500]
    assert all(r['u_brake'] == 350.0 / 1500.0 for r in held)
    assert all(r['mechanicalBrakeTorque'] == pytest.approx(r['u_brake'] * 1500.0) for r in held)


def test_the_firmware_sees_the_backward_spin_as_negative_rpm(launch_run):
    """The model's launch ring turns the motor backward; the inverter reports
    a signed speed, so the firmware sees it too (the wheels report |w|)."""
    _, out = launch_run
    rows = report.read_trace(out / 'trace.csv')
    assert min(r['motorSpeed'] for r in rows) < -10.0
    assert min(r['fw_motor_rpm'] for r in rows) < -100.0
    meta = json.loads((out / 'meta.json').read_text())
    assert meta['summary']['backward_spin']['rows'] > 0


def test_model_time_runs_at_firmware_time(launch_run):
    """Distance equals speed integrated over 3 ms rows, so the model steps
    3 ms per firmware step."""
    _, out = launch_run
    rows = report.read_trace(out / 'trace.csv')
    distance = rows[-1]['rearX'] - rows[0]['rearX']
    integral = sum(r['rearU'] for r in rows[:-1]) * cosim.STEP_MS / 1000
    assert distance == pytest.approx(integral, rel=0.01)
