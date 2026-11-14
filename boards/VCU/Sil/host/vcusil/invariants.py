"""Invariants: what the firmware must do on every run of every drive.

Each one is judged after the run on what left the VCU (the decoded
INVERTER_TORQUE_COMMAND, and the state and faults it reports), what the
drive gave it (the scenario's inputs) and what the car model reports (DC
power), never on a controller's internals. Where the firmware's past
decides, such as a debounced RTD edge or the filtered brake, the oracle is
fwmodel.py's model of it, which test_check_firmware.py holds to the library,
so the check does not trust the code it judges. A drive may waive one by
name with a reason (waive: in its file); a waiver that never fires fails the
run, as the drive no longer tests what it says. The rules each stands on,
and which are the team's design choices, are in the README (Checks after
the run).
"""

from __future__ import annotations

from dataclasses import dataclass

from .cosim import BSPD_POWER_W, BSPD_TRIP_MS, STEP_MS
from .firmware import HVC_ENERGIZED, STATE_DRIVE, VCU_FAULTS, VCU_STATES
from .fwmodel import (CONTACTOR_STATUS_PERIOD_MS, SURE_PEDAL, SURE_PSI, THRESHOLDS, ema,
                      hysteresis, rtd_switch)

_T = THRESHOLDS
# INVERTER_TORQUE_COMMAND's torque_request step (lib/spec/messages/vcu.textproto
# scale 0.1): the decoded command is always a whole number of these.
TORQUE_STEP_NM = 0.1
# faults.hpp kTorqueCutFaults by VcuFaultCode name; a test reads the header
# back. The rules require the first three to cut torque (T.4.2.5, T.4.3.3);
# the others are the team's choice.
TORQUE_CUT_FAULTS = ('APPS_IMPLAUSIBLE', 'BSE1_RANGE', 'BSE2_RANGE', 'BRAKE_FLAG_LATCHED',
                     'SHUTDOWN_OPEN', 'HVC_TIMEOUT', 'INVERTER_TIMEOUT', 'PDU_TIMEOUT',
                     'INVERTER_FAULT', 'BSPD_TRIPPED', 'MOTOR_DISCONNECT', 'ADC_STALE')
RULES_CUT_FAULTS = ('APPS_IMPLAUSIBLE', 'BSE1_RANGE', 'BSE2_RANGE')
_FAULT_NAMES = {code - 1: name for name, code in VCU_FAULTS.items()}  # by bit
CUT_MASK = sum(1 << (VCU_FAULTS[name] - 1) for name in TORQUE_CUT_FAULTS)
BOT_BIT = 1 << (VCU_FAULTS['BRAKE_OVER_THROTTLE'] - 1)
_STATE_NAMES = {number: name for name, number in VCU_STATES.items()}
# EV.3.3.1 and EV.3.4.1: over 80 kW continuously for 100 ms or more, or on a
# 500 ms moving average, is a violation.
POWER_LIMIT_W = 80_000.0
POWER_CONTINUOUS_MS = 100
POWER_AVERAGE_MS = 500


@dataclass(frozen=True)
class Rule:
    name: str
    basis: str  # 'rules' (an FSAE rule requires it) or 'design' (the team's choice)
    rule: str  # what it stands on
    fails: str  # when it fails, one line


RULES = {rule.name: rule for rule in (
    Rule('no_torque_outside_drive', 'rules', 'EV.9.6.1 (2027: EV.10.6.1)',
         'the inverter is enabled with a nonzero torque_request outside DRIVE'),
    Rule('enable_only_in_drive', 'design', 'control_loop.cpp ArbitrateTorque',
         'inverter_enable, or a nonzero torque_request, outside DRIVE'),
    Rule('torque_within_limit', 'design', 'InverterParams::torque_limit_nm',
         f'|torque_request| over {_T["InverterParams::torque_limit_nm"]:g} N.m, by more than '
         f'half a {TORQUE_STEP_NM:g} N.m step'),
    Rule('bot_cuts_torque', 'rules', 'EV.4.7.2',
         'a nonzero torque_request while BRAKE_OVER_THROTTLE is set, or the latch clearing '
         f'with the pedal still at {_T["BseParams::bot_pedal_off"]:.0%} or more'),
    Rule('bot_detected', 'rules', 'EV.4.7.1, with the team\'s 300 psi as brakes engaged',
         'BRAKE_OVER_THROTTLE not set on a tick the modelled filtered brake surely holds '
         f'brake-pressed and the pedal is over {_T["BseParams::bot_pedal_on"]:.0%}'),
    Rule('fault_cuts_torque', 'rules',
         'T.4.2.5 and T.4.3.3 for APPS_IMPLAUSIBLE, BSE1_RANGE and BSE2_RANGE; the rest of '
         'kTorqueCutFaults is design',
         'a nonzero torque_request or inverter_enable on a tick a torque-cut fault is set'),
    Rule('drive_needs_rtd_and_brake', 'rules',
         'EV.9.6.2 (2027: EV.10.6.2), with the team\'s 30 psi as pressed',
         'DRIVE entered off a debounced RTD press edge, with the modelled filtered brake under '
         f'{_T["BseParams::brake_light_psi"]:g} psi, or without CONTACTOR_STATUS reporting '
         'ENERGIZED with both AIRs closed'),
    Rule('power_within_80kw', 'rules', 'EV.3.3.1 and EV.3.4.1, on the car model\'s DC power',
         f'DC power over {POWER_LIMIT_W / 1e3:g} kW for {POWER_CONTINUOUS_MS} ms, or over it '
         f'on a {POWER_AVERAGE_MS} ms moving average'),
    Rule('bspd_not_tripped', 'rules', 'EV.7.7.2, on the car model\'s DC power',
         f'hard braking with more than {BSPD_POWER_W / 1e3:g} kW for {BSPD_TRIP_MS} ms: the '
         'car\'s BSPD would open the shutdown loop'),
)}


@dataclass(frozen=True)
class Result:
    name: str
    basis: str
    rule: str
    result: str  # 'pass', 'fail', 'waived' or 'unused_waiver'
    ticks: int  # how many ticks broke it
    first_ms: int | None  # the first that did
    message: str  # what the first one did, or why a waiver is unused
    reason: str | None = None  # the file's waiver

    @property
    def failed(self) -> bool:
        return self.result in ('fail', 'unused_waiver')

    def meta(self) -> dict[str, object]:
        return {'name': self.name, 'basis': self.basis, 'rule': self.rule,
                'result': self.result, 'ticks': self.ticks, 'first_ms': self.first_ms,
                'message': self.message, 'reason': self.reason}


def check(rows: list[dict[str, float | int]], waive: dict[str, str] | None = None,
          complete: bool = True) -> tuple[Result, ...]:
    """Every invariant on a run's rows (cosim.Result.rows, or trace.csv read
    back), with the file's waivers applied. A run cut short (complete False)
    is judged on the ticks it has: what they broke stays broken, but a
    waiver it never needed may be for a tick it never reached, so it is not
    judged."""
    waive = waive or {}
    found = _violations(rows)
    results = []
    for name, rule in RULES.items():
        broken = found[name]
        if broken:
            results.append(Result(name, rule.basis, rule.rule,
                                  'waived' if name in waive else 'fail', len(broken),
                                  broken[0][0], broken[0][1], waive.get(name)))
        elif name in waive and not complete:
            results.append(Result(name, rule.basis, rule.rule, 'pass', 0, None,
                                  'waived; not broken before the run stopped, so the waiver is '
                                  'not judged', waive[name]))
        elif name in waive:
            results.append(Result(name, rule.basis, rule.rule, 'unused_waiver', 0, None,
                                  'waived, but the run never broke it: the drive no longer tests '
                                  'what its waiver says (or delete the waiver)', waive[name]))
        else:
            results.append(Result(name, rule.basis, rule.rule, 'pass', 0, None, ''))
    return tuple(results)


def _faults(mask: int) -> str:
    return ', '.join(_FAULT_NAMES.get(bit, f'bit {bit}') for bit in range(32) if mask >> bit & 1)


def _violations(rows: list[dict[str, float | int]]) -> dict[str, list[tuple[int, str]]]:
    """(t_ms, what happened) for each tick that breaks each invariant."""
    found: dict[str, list[tuple[int, str]]] = {name: [] for name in RULES}
    if not rows:
        return found
    times = [int(r['t_ms']) for r in rows]
    if times != list(range(0, STEP_MS * len(rows), STEP_MS)):
        raise ValueError('the rows are not one per 3 ms step from 0')
    pedal = [float(r['accelerator']) for r in rows]
    brake = ema([float(r['brake_psi']) for r in rows], _T['BseParams::ema_alpha'])
    # Surely brake-pressed: past the firmware's 300 / 250 psi hysteresis by
    # more than the model's error, so the firmware's own reading agrees.
    pressed = hysteresis(brake, _T['BseParams::pressed_on_psi'] + SURE_PSI,
                         _T['BseParams::pressed_off_psi'] + SURE_PSI)
    edges = set(rtd_switch([bool(r['rtd_pressed']) for r in rows], times).edges)
    limit = _T['InverterParams::torque_limit_nm'] + TORQUE_STEP_NM / 2
    # Rounded: 0.05 + 0.001 is 0.051000000000000004 in floats, which would
    # let a latch clear at exactly 0.051.
    bot_on = round(_T['BseParams::bot_pedal_on'] + SURE_PEDAL, 9)
    bot_off = round(_T['BseParams::bot_pedal_off'] + SURE_PEDAL, 9)
    rtd_brake = _T['BseParams::brake_light_psi'] - SURE_PSI
    power = [float(r['dcBusVoltage']) * float(r['dcBusCurrent']) for r in rows]
    average_rows = -(-POWER_AVERAGE_MS // STEP_MS)
    over_ms = bspd_ms = 0
    for k, r in enumerate(rows):
        t, state, faults = times[k], int(r['fw_state']), int(r['fw_faults'])
        torque, enable = float(r['fw_torque_request_nm']), bool(r['fw_inverter_enable'])
        drive = state == STATE_DRIVE
        name = _STATE_NAMES.get(state, f'state {state}')
        if not drive and enable and torque != 0.0:
            found['no_torque_outside_drive'].append(
                (t, f'{torque:g} N.m with the inverter enabled in {name} at {t} ms'))
        if not drive and (enable or torque != 0.0):
            what = ('inverter_enable set' if enable else '') + (
                f'{" and " if enable else ""}{torque:g} N.m sent' if torque != 0.0 else '')
            found['enable_only_in_drive'].append((t, f'{what} in {name} at {t} ms'))
        if abs(torque) > limit:
            found['torque_within_limit'].append(
                (t, f'{torque:g} N.m at {t} ms, past the '
                    f'{_T["InverterParams::torque_limit_nm"]:g} N.m limit'))
        bot = bool(faults & BOT_BIT)
        if bot and torque != 0.0:
            found['bot_cuts_torque'].append(
                (t, f'{torque:g} N.m sent at {t} ms with BRAKE_OVER_THROTTLE set'))
        if k and int(rows[k - 1]['fw_faults']) & BOT_BIT and not bot and pedal[k] >= bot_off:
            found['bot_cuts_torque'].append(
                (t, f'BRAKE_OVER_THROTTLE cleared at {t} ms with the pedal at '
                    f'{pedal[k]:.1%}, not under {_T["BseParams::bot_pedal_off"]:.0%}'))
        if pressed[k] and pedal[k] > bot_on and not bot:
            found['bot_detected'].append(
                (t, f'no BRAKE_OVER_THROTTLE at {t} ms with the pedal at {pedal[k]:.1%} and the '
                    f'filtered brake about {brake[k]:.0f} psi'))
        cut = faults & CUT_MASK
        if cut and (torque != 0.0 or enable):
            what = (f'{torque:g} N.m sent' if torque != 0.0 else '') + (
                f'{" and " if torque != 0.0 else ""}inverter_enable set' if enable else '')
            found['fault_cuts_torque'].append((t, f'{what} at {t} ms with {_faults(cut)}'))
        # A run that starts in DRIVE entered it with no press at all.
        if drive and (k == 0 or int(rows[k - 1]['fw_state']) != STATE_DRIVE):
            # The firmware read the last CONTACTOR_STATUS before the tick: the
            # rig sends it at multiples of 10 ms with the step's inputs.
            frame = rows[(t // CONTACTOR_STATUS_PERIOD_MS * CONTACTOR_STATUS_PERIOD_MS)
                         // STEP_MS]
            why = []
            if k not in edges:
                why.append('no debounced RTD press edge on that tick')
            if brake[k] < rtd_brake:
                why.append(f'the filtered brake about {brake[k]:.0f} psi')
            if int(frame['hvc_state']) != HVC_ENERGIZED or not frame['contactors_closed']:
                why.append('CONTACTOR_STATUS not ENERGIZED with both AIRs closed')
            if why:
                found['drive_needs_rtd_and_brake'].append(
                    (t, f'DRIVE at {t} ms with {"; ".join(why)}'))
        over_ms = over_ms + STEP_MS if power[k] > POWER_LIMIT_W else 0
        if over_ms >= POWER_CONTINUOUS_MS:
            found['power_within_80kw'].append(
                (t, f'over {POWER_LIMIT_W / 1e3:g} kW for {over_ms} ms at {t} ms '
                    f'({power[k] / 1e3:.1f} kW)'))
        elif k + 1 >= average_rows:
            mean = sum(power[k + 1 - average_rows:k + 1]) / average_rows
            if mean > POWER_LIMIT_W:
                found['power_within_80kw'].append(
                    (t, f'{mean / 1e3:.1f} kW on the {POWER_AVERAGE_MS} ms average at {t} ms'))
        hard = bool(r['fw_hard_braking']) and power[k] > BSPD_POWER_W
        bspd_ms = bspd_ms + STEP_MS if hard else 0
        if bspd_ms >= BSPD_TRIP_MS:
            found['bspd_not_tripped'].append(
                (t, f'hard braking with {power[k] / 1e3:.1f} kW for {bspd_ms} ms at {t} ms'))
    return found
