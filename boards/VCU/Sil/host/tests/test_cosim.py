"""The co-simulation loop against the real firmware and a scripted car."""

import dataclasses
import math
from dataclasses import dataclass

import pytest
from fakes import Launch, Short, ToyCar

from vcusil import cosim, firmware
from vcusil.firmware import STATE_DRIVE, Firmware, Outputs
from vcusil.plant import OUTPUTS
from vcusil.scenario import Driver, RestOfCar

def test_loop_reads_then_steps_once_per_control_tick(lib):
    car = ToyCar()
    with Firmware(lib) as fw:
        result = cosim.run(Short(), fw, car)
    assert result.status == 'completed'
    assert result.steps == 1000
    expected = [call for k in range(1000) for call in (('read', k), ('step', k))]
    assert car.calls == expected
    assert [r['t_ms'] for r in result.rows] == list(range(0, 3000, 3))
    assert all(r['fw_control_ms'] == r['t_ms'] for r in result.rows)
    assert all(r['fw_torque_frames'] == 1 for r in result.rows)


def test_torque_reaches_the_car_only_while_the_inverter_is_enabled(lib):
    car = ToyCar()
    with Firmware(lib) as fw:
        result = cosim.run(Launch(), fw, car)
    rows = result.rows
    assert any(r['fw_state'] == STATE_DRIVE for r in rows)
    for r, u in zip(rows, car.inputs, strict=True):
        if r['fw_inverter_enable']:
            assert u.motor_torque_nm == r['fw_torque_request_nm']
            assert u.regen_limit_nm == r['fw_torque_limit_nm']
        else:
            assert u.motor_torque_nm == 0.0 and u.regen_limit_nm == 0.0
    assert max(u.motor_torque_nm for u in car.inputs) > 150.0
    assert car.v > 20.0


def test_brake_steering_and_cells_map_as_documented(lib):
    car = ToyCar()
    with Firmware(lib) as fw:
        result = cosim.run(Short(), fw, car)
    for r, u in zip(result.rows, car.inputs, strict=True):
        assert u.brake == pytest.approx(min(r['brake_psi'] / 1500.0, 1.0))
        assert u.steering_rad == pytest.approx(math.radians(r['steering_deg']))
        assert r['cell_v'] == pytest.approx(r['dcBusVoltage'] / 140)
    assert {u.brake for u in car.inputs} == {350.0 / 1500.0, 0.0}


def test_plant_inputs_gate_on_enable_and_clamp_the_brake():
    out = Outputs()
    out.inverter_enable, out.torque_request_nm, out.torque_limit_nm = 0, 99.0, 230.0
    u = cosim.plant_inputs(Driver(brake_psi=3000.0, steering_deg=90.0), out)
    assert (u.motor_torque_nm, u.regen_limit_nm, u.brake) == (0.0, 0.0, 1.0)
    assert u.steering_rad == pytest.approx(math.pi / 2)
    out.inverter_enable, out.direction = 1, 1
    u = cosim.plant_inputs(Driver(brake_psi=750.0), out)
    assert (u.motor_torque_nm, u.regen_limit_nm, u.brake) == (99.0, 230.0, 0.5)


def test_a_failed_plant_step_ends_the_run_and_keeps_the_trace(lib):
    car = ToyCar(fail_at_step=500)
    with Firmware(lib) as fw:
        result = cosim.run(Launch(), fw, car)
    assert result.status == 'plant_failed'
    assert 'step 500' in result.failure
    assert result.failure_lines == ['CVODE: the corrector convergence test failed repeatedly']
    assert result.steps == 501  # the row for the failed step is kept
    assert car.calls[-1] == ('step', 500)


def test_a_non_finite_plant_output_is_a_plant_failure(lib):
    car = ToyCar(nan_at_step=10)
    with Firmware(lib) as fw:
        result = cosim.run(Launch(), fw, car)
    assert result.status == 'plant_failed'
    assert 'non-finite' in result.failure
    assert result.steps == 10


class SilentFirmware:
    """Answers like the library but never sends a torque command."""

    def __init__(self):
        self.now = 0

    def advance(self, _inputs, ms):
        out = Outputs()
        out.control_ms, self.now = self.now, self.now + ms
        out.now_ms = self.now
        return out

    def read_snapshot(self):
        return {}

    def read_frames(self):
        return []


def test_a_missing_torque_command_is_a_harness_error():
    result = cosim.run(Short(), SilentFirmware(), ToyCar())
    assert result.status == 'harness_error'
    assert '0 torque commands at 0 ms' in result.failure
    assert result.steps == 0


class LaterFailingFirmware:
    """The real firmware until step 400, then a rejected call."""

    def __init__(self, fw):
        self.fw, self.calls = fw, 0

    def advance(self, inputs, ms):
        self.calls += 1
        if self.calls > 400:
            raise firmware.FirmwareError('vcu_sil_advance failed: ERR_INTERNAL (-5)')
        return self.fw.advance(inputs, ms)

    def read_snapshot(self):
        return self.fw.read_snapshot()

    def read_frames(self):
        return self.fw.read_frames()


def test_a_harness_error_mid_run_keeps_the_rows_so_far(lib):
    with Firmware(lib) as fw:
        result = cosim.run(Launch(), LaterFailingFirmware(fw), ToyCar())
    assert result.status == 'harness_error'
    assert 'ERR_INTERNAL' in result.failure
    assert result.steps == 400


def test_reverse_is_refused_not_driven():
    out = Outputs()
    out.inverter_enable, out.direction, out.torque_request_nm = 1, 0, 50.0
    with pytest.raises(cosim.HarnessError, match='reverse'):
        cosim.plant_inputs(Driver(), out)
    out.inverter_enable = 0  # a disabled inverter ignores the direction bit
    assert cosim.plant_inputs(Driver(), out).motor_torque_nm == 0.0


def test_summary_names_states_faults_and_backward_spin(lib):
    with Firmware(lib) as fw:
        result = cosim.run(Launch(), fw, ToyCar())
        summary = cosim.summarize(result, fw.state_name, fw.fault_names)
    assert summary['states'] == ['IDLE', 'PRECHARGE', 'TS_ACTIVE', 'RTD_WAIT', 'DRIVE']
    assert 1020 <= summary['first_drive_ms'] <= 1026
    assert summary['left_drive_after_entering'] is False
    # The loop is open at boot until its 10 ms debounce passes.
    assert summary['faults_seen'] == ['SHUTDOWN_OPEN']
    assert summary['backward_spin'] == {'rows': 0, 'first_ms': None, 'last_ms': None}
    assert summary['saturated_rows'] == 0
    rows = [dict(r) for r in result.rows[:5]]
    rows[2]['wheelSpeedRL'] = -3.0
    rows[3]['motorSpeed'] = -1.0
    spin = cosim._backward_spin(rows)
    assert spin == {'rows': 2, 'first_ms': 6, 'last_ms': 9}


def test_firmware_inputs_carry_every_car_signal():
    y = {name: float(i + 1) for i, name in enumerate(OUTPUTS)}
    inputs = cosim.firmware_inputs(Driver(accelerator=0.4, brake_psi=100.0, steering_deg=-30.0,
                                          rtd_pressed=True),
                                   RestOfCar(hvc_state=2, contactors_closed=True), y, 140)
    assert list(inputs.wheel_speed_rad_s) == [y[w] for w in cosim.WHEELS]
    assert inputs.motor_speed_rad_s == y['motorSpeed']
    assert inputs.motor_torque_nm == y['motorTorqueActual']
    assert inputs.dc_bus_voltage_v == y['dcBusVoltage']
    assert inputs.dc_bus_current_a == y['dcBusCurrent']
    assert inputs.min_cell_v == inputs.max_cell_v == pytest.approx(y['dcBusVoltage'] / 140)
    assert (inputs.accelerator_travel, inputs.brake_front_psi, inputs.brake_rear_psi) == (
        0.4, 100.0, 100.0)
    assert (inputs.steering_angle_deg, inputs.rtd_pressed) == (-30.0, 1)
    assert (inputs.hvc_state, inputs.contactors_closed, inputs.shutdown_loop_v) == (2, 1, 24.0)


def test_wheel_order_is_the_headers_fl_fr_rl_rr():
    """vcu_sil.h's order, spelled out rather than taken from cosim.WHEELS."""
    y = {name: float(i + 1) for i, name in enumerate(OUTPUTS)}
    inputs = cosim.firmware_inputs(Driver(), RestOfCar(), y, 140)
    assert list(inputs.wheel_speed_rad_s) == [
        y['wheelSpeedFL'], y['wheelSpeedFR'], y['wheelSpeedRL'], y['wheelSpeedRR']]


def test_motor_speed_keeps_its_sign():
    y = {name: 0.0 for name in OUTPUTS}
    y.update(motorSpeed=-55.0, dcBusVoltage=574.0)
    assert cosim.firmware_inputs(Driver(), RestOfCar(), y, 140).motor_speed_rad_s == -55.0


def test_regen_limit_is_the_commands_limit():
    """A limit other than the firmware's default 230 N.m."""
    out = Outputs()
    out.inverter_enable, out.direction = 1, 1
    out.torque_request_nm, out.torque_limit_nm = -40.0, 120.0
    u = cosim.plant_inputs(Driver(), out)
    assert (u.motor_torque_nm, u.regen_limit_nm) == (-40.0, 120.0)


class OverCurrentCar(ToyCar):
    def read(self):
        y = super().read()
        y['dcBusCurrent'] = 5000.0  # beyond INVERTER_CURRENT's +-3276.7 A
        return y


def test_summary_counts_every_saturated_row(lib):
    with Firmware(lib) as fw:
        result = cosim.run(Short(duration_ms=300), fw, OverCurrentCar())
        s = cosim.summarize(result, fw.state_name, fw.fault_names)
    bit = firmware.SATURATION_BITS['bus_current']
    assert all(r['fw_saturated'] & bit for r in result.rows)
    assert s['saturated_rows'] == result.steps


@dataclass(frozen=True)
class ReleaseRtd(Launch):
    duration_ms: int = 3000
    rtd_off_ms: int = 2500

    def at(self, t_ms):
        driver, car = super().at(t_ms)
        if t_ms >= self.rtd_off_ms:
            driver = dataclasses.replace(driver, rtd_pressed=False)
        return driver, car


def test_summary_reports_leaving_drive(lib):
    with Firmware(lib) as fw:
        result = cosim.run(ReleaseRtd(), fw, ToyCar())
        s = cosim.summarize(result, fw.state_name, fw.fault_names)
    # Releasing the toggle leaves DRIVE; with every precondition still met
    # the state machine goes straight on to wait for the next press.
    assert s['states'][-3:] == ['DRIVE', 'TS_ACTIVE', 'RTD_WAIT']
    assert s['left_drive_after_entering'] is True


@dataclass(frozen=True)
class BrakeOnThrottle(Launch):
    """Hard braking while the motor still makes power: the BSPD's case."""
    duration_ms: int = 4002

    def at(self, t_ms):
        driver, car = super().at(t_ms)
        if t_ms >= 3000:
            driver = dataclasses.replace(driver, accelerator=0.24, brake_psi=900.0)
        return driver, car


def test_summary_measures_the_bspd_condition(lib):
    with Firmware(lib) as fw:
        result = cosim.run(BrakeOnThrottle(), fw, ToyCar())
        s = cosim.summarize(result, fw.state_name, fw.fault_names)
    hard = [r for r in result.rows if r['fw_hard_braking']]
    assert hard and hard[0]['t_ms'] >= 3000
    # 24% pedal stays under the brake-over-throttle threshold, so the
    # firmware keeps commanding torque while the toy car draws over 5 kW.
    assert s['bspd_condition_ms'] >= 500
    with Firmware(lib) as fw:
        clean = cosim.summarize(cosim.run(Launch(), fw, ToyCar()), fw.state_name,
                                fw.fault_names)
    assert clean['bspd_condition_ms'] == 0
