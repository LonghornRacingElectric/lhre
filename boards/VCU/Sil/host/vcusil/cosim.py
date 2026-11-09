"""The co-simulation loop: firmware and car model, one 3 ms control tick at a time.

Per step k, at t = 3k ms:
  1. read the car model's outputs at t (before any input is set: several
     outputs feed through directly from the inputs);
  2. hold them, with the scenario's driver and rest-of-car values, for the
     firmware's three 1 ms passes; its control tick runs on the first;
  3. apply that tick's INVERTER_TORQUE_COMMAND to the model over [t, t + 3)
     and step it.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .firmware import STATE_DRIVE, Firmware, FirmwareError, Inputs, Outputs
from .plant import OUTPUTS, PlantError, PlantInputs
from .scenario import Driver, RestOfCar

STEP_MS = 3

# The car model has no hydraulics, so no constant links line pressure to its
# 0..1 brake (1500 N.m total at 1). This placeholder makes 1500 psi full
# brake; at or near full brake the model reverses the car (BobLib issue, see
# README), so scenarios stay well below it.
BRAKE_PSI_AT_FULL_PLANT_BRAKE = 1500.0

WHEELS = ('wheelSpeedFL', 'wheelSpeedFR', 'wheelSpeedRL', 'wheelSpeedRR')

# Firmware outputs copied into the trace, in this order.
FIRMWARE_COLUMNS = (
    'control_ms', 'state', 'faults', 'primary_fault', 'torque_frames', 'frames_dropped',
    'inverter_enable', 'torque_request_nm', 'torque_limit_nm', 'saturated',
    'pedal_travel', 'apps1_travel', 'bse1_psi', 'bse2_psi', 'bse_filtered_psi',
    'bse3_psi', 'steering_angle_deg', 'motor_rpm', 'torque_lookup_nm',
    'torque_power_limited_nm', 'torque_traction_nm', 'torque_command_nm',
    'power_w', 'power_trim_w', 'soe_pct', 'wheel_control_valid',
    'wheel_reference_m_s', 'buzzer', 'hard_braking',
)

COLUMNS = (
    't_ms',
    *OUTPUTS,
    'cell_v',
    'accelerator', 'brake_psi', 'steering_deg', 'rtd_pressed',
    'hvc_state', 'contactors_closed', 'shutdown_loop_v',
    *(f'fw_{name}' for name in FIRMWARE_COLUMNS),
    'u_motor_torque_nm', 'u_regen_limit_nm', 'u_brake', 'u_steering_rad',
)


class HarnessError(RuntimeError):
    """The harness itself broke its contract (not a plant or firmware result)."""


class PlantLike(Protocol):
    series_cells: int
    truth: dict[str, float | None]  # model truth read with the last outputs

    def read(self) -> dict[str, float]: ...
    def step(self, k: int, u: PlantInputs) -> None: ...
    def failure_lines(self) -> list[str]: ...


class Scenario(Protocol):
    name: str
    duration_ms: int

    def at(self, t_ms: int) -> tuple[Driver, RestOfCar]: ...


@dataclass
class Result:
    scenario: str
    rows: list[dict[str, float | int]] = field(default_factory=list)
    # Per row: the firmware's full control snapshot and the model's truth.
    snapshots: list[dict[str, int | float]] = field(default_factory=list)
    truth: list[dict[str, float | None]] = field(default_factory=list)
    # Every CAN frame on both buses: (t_ms, bus, id, from_vcu, data).
    frames: list[tuple[int, int, int, bool, bytes]] = field(default_factory=list)
    status: str = 'completed'  # 'plant_failed', or 'harness_error'
    failure: str | None = None
    failure_lines: list[str] = field(default_factory=list)
    wall_s: float = 0.0

    @property
    def steps(self) -> int:
        return len(self.rows)


def firmware_inputs(driver: Driver, car: RestOfCar, y: dict[str, float],
                    series_cells: int) -> Inputs:
    cell_v = y['dcBusVoltage'] / series_cells
    inputs = Inputs(
        rtd_pressed=int(driver.rtd_pressed),
        accelerator_travel=driver.accelerator,
        brake_front_psi=driver.brake_psi,
        brake_rear_psi=driver.brake_psi,
        steering_angle_deg=driver.steering_deg,
        hvc_state=car.hvc_state,
        contactors_closed=int(car.contactors_closed),
        shutdown_loop_v=car.shutdown_loop_v,
        motor_speed_rad_s=y['motorSpeed'],
        motor_torque_nm=y['motorTorqueActual'],
        dc_bus_voltage_v=y['dcBusVoltage'],
        dc_bus_current_a=y['dcBusCurrent'],
        min_cell_v=cell_v,
        max_cell_v=cell_v,
    )
    for i, name in enumerate(WHEELS):
        inputs.wheel_speed_rad_s[i] = y[name]
    return inputs


def plant_inputs(driver: Driver, out: Outputs,
                 brake_psi_full: float = BRAKE_PSI_AT_FULL_PLANT_BRAKE) -> PlantInputs:
    """What the inverter and the brake lines deliver to the car.

    The inverter applies the decoded torque only while enabled. The driver's
    brake reaches the calipers hydraulically, never through the VCU.
    """
    enabled = bool(out.inverter_enable)
    if enabled and not out.direction:
        # The firmware always commands forward; reverse is not modelled.
        raise HarnessError('the firmware commanded reverse, which the car model cannot take')
    return PlantInputs(
        motor_torque_nm=out.torque_request_nm if enabled else 0.0,
        regen_limit_nm=out.torque_limit_nm if enabled else 0.0,
        brake=min(max(driver.brake_psi / brake_psi_full, 0.0), 1.0),
        steering_rad=math.radians(driver.steering_deg),
    )


def run(scenario: Scenario, firmware: Firmware, plant: PlantLike,
        brake_psi_full: float = BRAKE_PSI_AT_FULL_PLANT_BRAKE,
        progress: Callable[[int, int], None] | None = None) -> Result:
    """Runs the scenario to its end, or to the first plant failure or
    harness error. Either way the rows so far are kept."""
    steps = -(-scenario.duration_ms // STEP_MS)  # whole steps covering the duration
    result = Result(scenario=scenario.name)
    started = time.perf_counter()
    try:
        for k in range(steps):
            t_ms = k * STEP_MS
            y = plant.read()
            driver, car = scenario.at(t_ms)
            out = firmware.advance(firmware_inputs(driver, car, y, plant.series_cells), STEP_MS)
            if out.now_ms != t_ms + STEP_MS or out.control_ms != t_ms:
                raise HarnessError(f'firmware clock {out.now_ms} / tick {out.control_ms} '
                                   f'is off the {t_ms} ms step')
            if out.torque_frames != 1:
                raise HarnessError(f'{out.torque_frames} torque commands at {t_ms} ms, want 1')
            u = plant_inputs(driver, out, brake_psi_full)
            result.rows.append(_row(t_ms, y, plant.series_cells, driver, car, out, u))
            result.snapshots.append(firmware.read_snapshot())
            result.truth.append(dict(plant.truth))
            result.frames.extend(firmware.read_frames())
            plant.step(k, u)
            if progress is not None:
                progress(k + 1, steps)
    except PlantError as exc:
        result.status = 'plant_failed'
        result.failure = str(exc)
        result.failure_lines = plant.failure_lines()
    except (FirmwareError, HarnessError) as exc:
        result.status = 'harness_error'
        result.failure = str(exc)
    result.wall_s = time.perf_counter() - started
    return result


def _row(t_ms: int, y: dict[str, float], series_cells: int, driver: Driver,
         car: RestOfCar, out: Outputs, u: PlantInputs) -> dict[str, float | int]:
    row: dict[str, float | int] = {'t_ms': t_ms, **y}
    row['cell_v'] = y['dcBusVoltage'] / series_cells
    row.update(accelerator=driver.accelerator, brake_psi=driver.brake_psi,
               steering_deg=driver.steering_deg, rtd_pressed=int(driver.rtd_pressed),
               hvc_state=car.hvc_state, contactors_closed=int(car.contactors_closed),
               shutdown_loop_v=car.shutdown_loop_v)
    for name in FIRMWARE_COLUMNS:
        row[f'fw_{name}'] = getattr(out, name)
    row.update(u_motor_torque_nm=u.motor_torque_nm, u_regen_limit_nm=u.regen_limit_nm,
               u_brake=u.brake, u_steering_rad=u.steering_rad)
    return row


def summarize(result: Result, state_name: Callable[[int], str],
              fault_names: Callable[[int], list[str]]) -> dict[str, object]:
    """Numbers a reader checks first, and the ones that expose a car model
    misbehaving (rolling backward hides behind the wheel sensors' |w|)."""
    rows = result.rows
    if not rows:
        return {'steps': 0}
    states: list[str] = []
    for r in rows:
        name = state_name(int(r['fw_state']))
        if not states or states[-1] != name:
            states.append(name)
    drive = [r for r in rows if r['fw_state'] == STATE_DRIVE]
    first_drive = drive[0]['t_ms'] if drive else None
    faults = 0
    for r in rows:
        faults |= int(r['fw_faults'])
    left_drive = bool(drive) and any(
        r['fw_state'] != STATE_DRIVE for r in rows if r['t_ms'] > drive[0]['t_ms'])
    return {
        'steps': len(rows),
        'simulated_s': len(rows) * STEP_MS / 1000,
        'states': states,
        'first_drive_ms': first_drive,
        'left_drive_after_entering': left_drive,
        'faults_seen': fault_names(faults),
        'peak_speed_m_s': max(r['rearU'] for r in rows),
        'final_speed_m_s': rows[-1]['rearU'],
        'min_speed_m_s': min(r['rearU'] for r in rows),
        'min_motor_speed_rad_s': min(r['motorSpeed'] for r in rows),
        'min_wheel_speed_rad_s': {w: min(r[w] for r in rows) for w in WHEELS},
        'backward_spin': _backward_spin(rows),
        'peak_torque_request_nm': max(r['fw_torque_request_nm'] for r in rows),
        'peak_dc_power_kw': max(r['dcBusVoltage'] * r['dcBusCurrent'] for r in rows) / 1e3,
        'saturated_rows': sum(r['fw_saturated'] != 0 for r in rows),
        'bspd_condition_ms': _bspd_condition_ms(rows),
    }


# Below these the model is only settling at rest; the wheel sensors report
# |w|, so the firmware never sees the sign.
BACKWARD_WHEEL_RAD_S = -0.05
BACKWARD_MOTOR_RAD_S = -0.5


def _backward_spin(rows: list[dict[str, float | int]]) -> dict[str, object]:
    spun = [r['t_ms'] for r in rows
            if r['motorSpeed'] < BACKWARD_MOTOR_RAD_S
            or any(r[w] < BACKWARD_WHEEL_RAD_S for w in WHEELS)]
    return {'rows': len(spun), 'first_ms': spun[0] if spun else None,
            'last_ms': spun[-1] if spun else None}


# The FSAE rules have the BSPD trip on hard braking while more than 5 kW goes
# to the motor for 500 ms.
BSPD_POWER_W = 5000.0
BSPD_TRIP_MS = 500


def _bspd_condition_ms(rows: list[dict[str, float | int]]) -> int:
    """Longest stretch of hard braking with more than 5 kW. The rig does not
    model the BSPD, so a run that would trip the car still looks clean."""
    longest = current = 0
    for r in rows:
        if r['fw_hard_braking'] and r['dcBusVoltage'] * r['dcBusCurrent'] > BSPD_POWER_W:
            current += STEP_MS
            longest = max(longest, current)
        else:
            current = 0
    return longest
