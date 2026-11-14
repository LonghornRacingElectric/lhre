"""The global invariants (invariants.py) on synthetic rows: each one passing,
failing at its exact first tick with its tick count and message, one step
either side of every threshold, and the firmware's known non-failures it must
accept. The rows are built here from a parked, ready baseline, one per 3 ms
step, so nothing needs the library or the FMU. The pins hold the names to the
loader's and the constants to faults.hpp and the generated CAN table, so a
firmware change fails here until the invariant is updated."""

import ast
import re
from pathlib import Path

import pytest

from vcusil import check, cosim, fwmodel, invariants, report, scenario_file
from vcusil.candecode import CanTable
from vcusil.cosim import STEP_MS
from vcusil.firmware import HVC_ENERGIZED, HVC_PRECHARGING, VCU_FAULTS, VCU_STATES

SIL = Path(__file__).resolve().parents[2]
FAULTS_HPP = SIL.parent / 'App' / 'safety' / 'faults.hpp'
SCENARIO_FILE_PY = Path(scenario_file.__file__)

IDLE, DRIVE, FAULT = VCU_STATES['IDLE'], VCU_STATES['DRIVE'], VCU_STATES['FAULT']

# Parked and ready: energized, loop closed, brake held, nothing sent.
BASE = {'fw_state': IDLE, 'fw_faults': 0, 'fw_inverter_enable': 0, 'fw_torque_request_nm': 0.0,
        'accelerator': 0.0, 'brake_psi': 350.0, 'rtd_pressed': 0, 'hvc_state': HVC_ENERGIZED,
        'contactors_closed': 1, 'shutdown_loop_v': 24.0, 'fw_hard_braking': 0,
        'dcBusVoltage': 550.0, 'dcBusCurrent': 0.0, 'rearU': 0.0}

# launch.yml's numbers: RTD written at 1000 is first sampled at 1002, and the
# 20 ms debounce passes 21 ms later.
RTD_SAMPLED_MS, DRIVE_MS = 1002, 1023


def bits(*names: str) -> int:
    return sum(1 << (VCU_FAULTS[name] - 1) for name in names)


def run(n: int, **values) -> list[dict]:
    """n rows of the baseline from 0 ms, with values on every row."""
    return [{'t_ms': k * STEP_MS, **BASE, **values} for k in range(n)]


def put(rows: list[dict], start_ms: int, end_ms: int | None = None, **values) -> list[dict]:
    """values on the rows from start_ms up to (not including) end_ms."""
    for r in rows:
        if r['t_ms'] >= start_ms and (end_ms is None or r['t_ms'] < end_ms):
            r.update(values)
    return rows


def launch(n: int = 400) -> list[dict]:
    """An honest launch: SHUTDOWN_OPEN on the boot ticks as every run has, RTD
    released from boot and pressed from 1002, DRIVE on the debounced edge."""
    rows = run(n)
    put(rows, 0, 12, fw_faults=bits('SHUTDOWN_OPEN'))
    put(rows, RTD_SAMPLED_MS, rtd_pressed=1)
    return put(rows, DRIVE_MS, fw_state=DRIVE)


def broken(rows: list[dict], waive: dict[str, str] | None = None) -> dict[str, tuple]:
    """(ticks, first_ms, message) of every invariant the rows break."""
    return {r.name: (r.ticks, r.first_ms, r.message)
            for r in invariants.check(rows, waive) if r.result == 'fail'}


def result(rows: list[dict], name: str, waive: dict[str, str] | None = None):
    return next(r for r in invariants.check(rows, waive) if r.name == name)


def test_the_baseline_uses_the_traces_own_column_names():
    assert set(BASE) | {'t_ms'} <= set(cosim.COLUMNS)


def test_an_honest_launch_breaks_nothing():
    rows = launch()
    assert [r['fw_faults'] for r in rows[:5]] == [bits('SHUTDOWN_OPEN')] * 4 + [0]
    results = invariants.check(rows)
    assert [r.name for r in results] == list(invariants.RULES)
    assert [(r.result, r.ticks, r.first_ms, r.message) for r in results] == [
        ('pass', 0, None, '')] * len(invariants.RULES)


# Each case: the rows, and every invariant they break as (ticks, first_ms,
# message); every other invariant must pass.

# no_torque_outside_drive is the rule (the motor would respond); enable_only_
# in_drive the team's stricter design (nothing at all is sent).
OUTSIDE_DRIVE = [
    ('torque-without-enable-in-idle', put(run(10), 6, 12, fw_torque_request_nm=5.0),
     {'enable_only_in_drive': (2, 6, '5 N.m sent in IDLE at 6 ms')}),
    ('enable-without-torque-in-idle', put(run(10), 6, 12, fw_inverter_enable=1),
     {'enable_only_in_drive': (2, 6, 'inverter_enable set in IDLE at 6 ms')}),
    ('enable-and-torque-in-fault',
     put(run(10, fw_state=FAULT), 6, 12, fw_inverter_enable=1, fw_torque_request_nm=5.0),
     {'no_torque_outside_drive': (2, 6, '5 N.m with the inverter enabled in FAULT at 6 ms'),
      'enable_only_in_drive': (2, 6, 'inverter_enable set and 5 N.m sent in FAULT at 6 ms')}),
    ('negative-torque-enabled-on-the-first-tick',
     put(run(10, fw_state=VCU_STATES['TS_ACTIVE']), 0, 3, fw_inverter_enable=1,
         fw_torque_request_nm=-12.5),
     {'no_torque_outside_drive': (1, 0, '-12.5 N.m with the inverter enabled in TS_ACTIVE '
                                        'at 0 ms'),
      'enable_only_in_drive': (1, 0, 'inverter_enable set and -12.5 N.m sent in TS_ACTIVE '
                                     'at 0 ms')}),
    ('enable-in-a-state-the-spec-does-not-name',
     put(run(10, fw_state=9), 3, 6, fw_inverter_enable=1),
     {'enable_only_in_drive': (1, 3, 'inverter_enable set in state 9 at 3 ms')}),
    ('torque-and-enable-in-drive',
     put(launch(), 1104, fw_inverter_enable=1, fw_torque_request_nm=100.0), {}),
    # control_loop.cpp keeps the inverter enabled through BOT with 0 N.m.
    ('enable-with-zero-torque-through-bot-in-drive',
     put(launch(), 1104, accelerator=0.6, fw_inverter_enable=1,
         fw_faults=bits('BRAKE_OVER_THROTTLE')), {}),
    # The shutdown loop opens in DRIVE: FAULT, SHUTDOWN_OPEN and 0 N.m on one
    # tick, which no invariant may call late.
    ('fault-cuts-on-the-tick-it-enters',
     put(put(launch(), 1104, fw_inverter_enable=1, fw_torque_request_nm=100.0), 1155,
         fw_state=FAULT, fw_faults=bits('SHUTDOWN_OPEN'), fw_inverter_enable=0,
         fw_torque_request_nm=0.0), {}),
    ('fault-cuts-a-tick-late',
     put(put(put(launch(), 1104, fw_inverter_enable=1, fw_torque_request_nm=100.0), 1155,
             fw_state=FAULT, fw_faults=bits('SHUTDOWN_OPEN')), 1158, fw_inverter_enable=0,
         fw_torque_request_nm=0.0),
     {'no_torque_outside_drive': (1, 1155, '100 N.m with the inverter enabled in FAULT at '
                                           '1155 ms'),
      'enable_only_in_drive': (1, 1155, 'inverter_enable set and 100 N.m sent in FAULT at '
                                        '1155 ms'),
      'fault_cuts_torque': (1, 1155, '100 N.m sent and inverter_enable set at 1155 ms with '
                                     'SHUTDOWN_OPEN')}),
]

# torque_request is a whole number of 0.1 N.m, so the check allows the frame's
# rounding, half a step, over 230: 230.1 is the first frame value past it.
LIMIT = [
    ('230.0-passes', put(launch(), 1104, fw_inverter_enable=1, fw_torque_request_nm=230.0), {}),
    ('230.05-passes', put(launch(), 1104, fw_inverter_enable=1, fw_torque_request_nm=230.05),
     {}),
    ('minus-230.05-passes',
     put(launch(), 1104, fw_inverter_enable=1, fw_torque_request_nm=-230.05), {}),
    ('230.1-fails',
     put(launch(), 1104, 1110, fw_inverter_enable=1, fw_torque_request_nm=230.1),
     {'torque_within_limit': (2, 1104, '230.1 N.m at 1104 ms, past the 230 N.m limit')}),
    ('minus-230.1-fails',
     put(launch(), 1104, 1107, fw_inverter_enable=1, fw_torque_request_nm=-230.1),
     {'torque_within_limit': (1, 1104, '-230.1 N.m at 1104 ms, past the 230 N.m limit')}),
    ('over-the-limit-outside-drive-breaks-both',
     put(run(10), 9, 12, fw_torque_request_nm=300.0),
     {'enable_only_in_drive': (1, 9, '300 N.m sent in IDLE at 9 ms'),
      'torque_within_limit': (1, 9, '300 N.m at 9 ms, past the 230 N.m limit')}),
]


def bot_released(pedal: float) -> list[dict]:
    """BOT latched in DRIVE at 1104 on a held brake with the pedal at 60 %,
    and released at 1149 with the pedal at pedal."""
    rows = put(launch(), 1104, accelerator=0.6, fw_inverter_enable=1)
    put(rows, 1104, 1149, fw_faults=bits('BRAKE_OVER_THROTTLE'))
    return put(rows, 1149, accelerator=pedal)


def cleared(pedal: str) -> str:
    return f'BRAKE_OVER_THROTTLE cleared at 1149 ms with the pedal at {pedal}, not under 5%'


# The latch clears once the raw pedal is under bot_pedal_off (5 %); the check
# calls a release early only with the pedal the model's 0.001 or more past it.
BOT_CUTS = [
    ('torque-while-latched',
     put(put(launch(), 1104, accelerator=0.6, fw_inverter_enable=1,
             fw_faults=bits('BRAKE_OVER_THROTTLE')), 1110, 1113, fw_torque_request_nm=10.0),
     {'bot_cuts_torque': (1, 1110, '10 N.m sent at 1110 ms with BRAKE_OVER_THROTTLE set')}),
    ('regen-while-latched',
     put(put(launch(), 1104, accelerator=0.6, fw_inverter_enable=1,
             fw_faults=bits('BRAKE_OVER_THROTTLE')), 1110, 1116, fw_torque_request_nm=-5.0),
     {'bot_cuts_torque': (2, 1110, '-5 N.m sent at 1110 ms with BRAKE_OVER_THROTTLE set')}),
    ('released-at-0', bot_released(0.0), {}),
    ('released-at-5.0-percent', bot_released(0.05), {}),
    ('released-at-5.09-percent', bot_released(0.0509), {}),
    ('released-at-5.11-percent', bot_released(0.0511),
     {'bot_cuts_torque': (1, 1149, cleared('5.1%'))}),
    ('released-at-5.2-percent', bot_released(0.052),
     {'bot_cuts_torque': (1, 1149, cleared('5.2%'))}),
    # Still braking at 60 %: the latch is still due, so bot_detected fails too.
    ('released-at-60-percent', bot_released(0.6),
     {'bot_cuts_torque': (1, 1149, cleared('60.0%')),
      'bot_detected': (17, 1149, 'no BRAKE_OVER_THROTTLE at 1149 ms with the pedal at 60.0% '
                                 'and the filtered brake about 350 psi')}),
    # Only BRAKE_OVER_THROTTLE's own bit clearing counts.
    ('another-fault-clearing-at-60-percent',
     put(run(20, accelerator=0.6, brake_psi=0.0), 0, 30, fw_faults=bits('COOLANT_OVERTEMP')),
     {}),
    # Only the clearing tick's pedal decides, not the tick before.
    ('released-the-tick-the-pedal-drops', put(bot_released(0.0), 1146, 1149, accelerator=0.06),
     {}),
]


def bot_on(n: int, brake: list[float] | float, pedal: float = 0.6,
           latched_ms: int | None = 0) -> list[dict]:
    """n rows parked with the pedal at pedal, the brake at brake (per row, or
    held), and BRAKE_OVER_THROTTLE set from latched_ms (None: never)."""
    rows = run(n, accelerator=pedal)
    for k, r in enumerate(rows):
        r['brake_psi'] = brake[k] if isinstance(brake, list) else brake
    return rows if latched_ms is None else put(rows, latched_ms,
                                               fw_faults=bits('BRAKE_OVER_THROTTLE'))


def missed(t_ms: int, psi: int, pedal: str = '60.0%') -> str:
    return (f'no BRAKE_OVER_THROTTLE at {t_ms} ms with the pedal at {pedal} and the filtered '
            f'brake about {psi} psi')


# From rest to 400 psi at 3 ms the filter (alpha 0.10) reads 298.3 at 39 ms and
# 308.5 at 42: surely pressed (301) from 42. From 400 down to 200 at 30 ms it
# reads 256.5 at 63 and 250.8 at 66: surely released (under 251) from 66.
RISE = [0.0] + [400.0] * 39
FALL = [400.0] * 10 + [200.0] * 30
BOT_DETECTED = [
    ('latched-on-a-brake-held-from-boot', bot_on(20, 400.0), {}),
    # The filter is seeded by the first sample, so the bit is due on tick 0.
    ('never-latched-on-a-brake-held-from-boot', bot_on(20, 400.0, latched_ms=None),
     {'bot_detected': (20, 0, missed(0, 400))}),
    ('never-latched-at-299-psi', bot_on(20, 299.0, latched_ms=None), {}),
    ('never-latched-at-300.99-psi', bot_on(20, 300.99, latched_ms=None), {}),
    ('never-latched-at-301-psi', bot_on(20, 301.0, latched_ms=None),
     {'bot_detected': (20, 0, missed(0, 301))}),
    ('never-latched-with-the-pedal-at-25.1-percent',
     bot_on(20, 400.0, pedal=0.251, latched_ms=None), {}),
    ('never-latched-with-the-pedal-at-25.11-percent',
     bot_on(20, 400.0, pedal=0.2511, latched_ms=None),
     {'bot_detected': (20, 0, missed(0, 400, '25.1%'))}),
    ('latched-the-tick-the-filter-passes-301', bot_on(40, RISE, latched_ms=42), {}),
    ('latched-a-tick-after-the-filter-passes-301', bot_on(40, RISE, latched_ms=45),
     {'bot_detected': (1, 42, missed(42, 308))}),
    # Hysteresis: falling to 260 psi stays pressed, so the bit stays due.
    ('never-latched-on-a-brake-falling-to-260',
     bot_on(40, [400.0] * 10 + [260.0] * 30, latched_ms=None),
     {'bot_detected': (40, 0, missed(0, 400))}),
    ('never-latched-on-a-brake-falling-to-200', bot_on(40, FALL, latched_ms=None),
     {'bot_detected': (22, 0, missed(0, 400))}),
    # Back up to 280 psi, between 251 and 301: the state before (off) holds.
    ('never-latched-on-a-brake-back-at-280',
     bot_on(60, FALL[:30] + [280.0] * 30, latched_ms=None),
     {'bot_detected': (22, 0, missed(0, 400))}),
]

CUT_FAULTS = [name for name in VCU_FAULTS if name in invariants.TORQUE_CUT_FAULTS]
OTHER_FAULTS = [name for name in VCU_FAULTS if name not in invariants.TORQUE_CUT_FAULTS]


def faulted(*names: str, torque: float = 50.0, enable: int = 1) -> list[dict]:
    """In DRIVE, the faults set on two ticks from 1104 with torque and enable."""
    return put(launch(), 1104, 1110, fw_faults=bits(*names), fw_torque_request_nm=torque,
               fw_inverter_enable=enable)


FAULT_CUTS = [
    ('torque-only', faulted('APPS_IMPLAUSIBLE', enable=0),
     {'fault_cuts_torque': (2, 1104, '50 N.m sent at 1104 ms with APPS_IMPLAUSIBLE')}),
    ('enable-only', faulted('BSE1_RANGE', torque=0.0),
     {'fault_cuts_torque': (2, 1104, 'inverter_enable set at 1104 ms with BSE1_RANGE')}),
    # Only the cut faults are named, in bit order.
    ('cut-and-other-faults', faulted('HVC_TIMEOUT', 'CONTACTORS_OPEN', 'SHUTDOWN_OPEN'),
     {'fault_cuts_torque': (2, 1104, '50 N.m sent and inverter_enable set at 1104 ms with '
                                     'SHUTDOWN_OPEN, HVC_TIMEOUT')}),
    *((f'cut-{name}', faulted(name),
       {'fault_cuts_torque': (2, 1104, f'50 N.m sent and inverter_enable set at 1104 ms with '
                                       f'{name}')}) for name in CUT_FAULTS),
    *((f'not-cut-{name}', faulted(name),
       {'bot_cuts_torque': (2, 1104, '50 N.m sent at 1104 ms with BRAKE_OVER_THROTTLE set')}
       if name == 'BRAKE_OVER_THROTTLE' else {}) for name in OTHER_FAULTS),
]


def drive_at(t_ms: int, rows: list[dict] | None = None) -> list[dict]:
    """launch() with DRIVE entered at t_ms instead."""
    rows = launch() if rows is None else rows
    return put(put(rows, 0, fw_state=IDLE), t_ms, fw_state=DRIVE)


def entered(t_ms: int, *why: str) -> dict[str, tuple]:
    return {'drive_needs_rtd_and_brake': (1, t_ms, f'DRIVE at {t_ms} ms with {"; ".join(why)}')}


NO_EDGE = 'no debounced RTD press edge on that tick'
NOT_ENERGIZED = 'CONTACTOR_STATUS not ENERGIZED with both AIRs closed'
# Released from boot through 21 ms arms the switch; pressed from 24 its edge is
# 45. Released only through 18 it never arms: the press reads as held from boot.
DRIVE_NEEDS = [
    ('one-tick-before-the-edge', drive_at(DRIVE_MS - STEP_MS), entered(1020, NO_EDGE)),
    ('one-tick-after-the-edge', drive_at(DRIVE_MS + STEP_MS), entered(1026, NO_EDGE)),
    ('released-21-ms-from-boot', drive_at(45, put(launch(), 24, rtd_pressed=1)), {}),
    ('released-18-ms-from-boot', drive_at(42, put(launch(), 21, rtd_pressed=1)),
     entered(42, NO_EDGE)),
    ('never-pressed', put(launch(), 0, rtd_pressed=0), entered(1023, NO_EDGE)),
    # 350 psi released 23 ticks before the edge filters to 31.0 psi, 24 to 27.9.
    ('brake-released-23-ticks-before', put(launch(), 957, brake_psi=0.0), {}),
    ('brake-released-24-ticks-before', put(launch(), 954, brake_psi=0.0),
     entered(1023, 'the filtered brake about 28 psi')),
    # 29 to 30 psi is within the model's margin of the firmware's 30.
    ('brake-held-at-29-psi', put(launch(), 0, brake_psi=29.0), {}),
    ('brake-held-at-29.5-psi', put(launch(), 0, brake_psi=29.5), {}),
    ('brake-held-at-28.99-psi', put(launch(), 0, brake_psi=28.99),
     entered(1023, 'the filtered brake about 29 psi')),
    ('brake-released-after-entry', put(launch(), 1026, brake_psi=0.0), {}),
    # The firmware read the 1020 ms CONTACTOR_STATUS, which carries row 1020.
    ('hvc-precharging-in-the-last-frame', put(launch(), 1020, 1023, hvc_state=HVC_PRECHARGING),
     entered(1023, NOT_ENERGIZED)),
    ('an-air-open-in-the-last-frame', put(launch(), 1020, 1023, contactors_closed=0),
     entered(1023, NOT_ENERGIZED)),
    ('hvc-energized-from-the-last-frame', put(launch(), 0, 1020, hvc_state=HVC_PRECHARGING), {}),
    ('hvc-energized-only-from-the-edge', put(launch(), 0, 1023, hvc_state=HVC_PRECHARGING),
     entered(1023, NOT_ENERGIZED)),
    ('hvc-drops-after-the-last-frame', put(launch(), 1023, hvc_state=HVC_PRECHARGING), {}),
    # Pressed from 1011, the edge is 1032: the 1030 ms frame carries row 1029.
    ('frame-from-the-step-before-the-edge',
     put(drive_at(1032, put(launch(), 0, 1011, rtd_pressed=0)), 1029, 1032,
         hvc_state=HVC_PRECHARGING),
     entered(1032, NOT_ENERGIZED)),
    ('no-frame-from-the-other-steps',
     put(put(drive_at(1032, put(launch(), 0, 1011, rtd_pressed=0)), 1026, 1029,
             hvc_state=HVC_PRECHARGING), 1032, 1035, hvc_state=HVC_PRECHARGING), {}),
    ('every-reason-at-once',
     put(launch(), 0, rtd_pressed=0, brake_psi=0.0, hvc_state=HVC_PRECHARGING),
     entered(1023, NO_EDGE, 'the filtered brake about 0 psi', NOT_ENERGIZED)),
    # Back to IDLE and into DRIVE again needs a new press.
    ('re-entered-without-a-new-press',
     put(put(launch(), 1104, 1158, fw_state=IDLE), 1158, fw_state=DRIVE),
     entered(1158, NO_EDGE)),
]


def powered(volts: float, amps: float, rows_on: int, n: int = 300, **values) -> list[dict]:
    """n rows with volts, amps and values on rows_on of them from 300 ms."""
    return put(run(n), 300, 300 + rows_on * STEP_MS, dcBusVoltage=volts, dcBusCurrent=amps,
               **values)


def alternating(n: int) -> list[dict]:
    """160 kW on every even row and 0 on every odd one: never two rows over
    80 kW in a row, and over 80 kW on average only over an odd window that
    starts on an even row (84 of 167 rows)."""
    return [{**r, 'dcBusCurrent': 320.0 if k % 2 == 0 else 0.0}
            for k, r in enumerate(run(n, dcBusVoltage=500.0))]


KW_80_1 = (600.0, 133.5)  # 80.1 kW, exact in binary
POWER = [
    ('80-kw-throughout', run(400, dcBusVoltage=500.0, dcBusCurrent=160.0), {}),
    ('80.1-kw-for-99-ms', powered(*KW_80_1, 33), {}),
    ('80.1-kw-for-102-ms', powered(*KW_80_1, 34),
     {'power_within_80kw': (1, 399, 'over 80 kW for 102 ms at 399 ms (80.1 kW)')}),
    ('80.1-kw-for-120-ms', powered(*KW_80_1, 40),
     {'power_within_80kw': (7, 399, 'over 80 kW for 102 ms at 399 ms (80.1 kW)')}),
    # A row at exactly 80 kW restarts the 100 ms.
    ('80.1-kw-broken-by-a-row-at-80',
     put(powered(*KW_80_1, 67), 399, 402, dcBusVoltage=500.0, dcBusCurrent=160.0), {}),
    # 40 rows over would fail, but the run ends 33 rows in.
    ('80.1-kw-cut-short-at-99-ms', powered(*KW_80_1, 40, n=133), {}),
    # Over on both counts from 498 ms: each tick still counts once.
    ('100-kw-for-600-ms', run(200, dcBusVoltage=500.0, dcBusCurrent=200.0),
     {'power_within_80kw': (167, 99, 'over 80 kW for 102 ms at 99 ms (100.0 kW)')}),
    # 167 rows (501 ms) make the 500 ms average: 84 x 160 / 167 = 80.5 kW.
    ('alternating-for-200-rows', alternating(200),
     {'power_within_80kw': (17, 498, '80.5 kW on the 500 ms average at 498 ms')}),
    ('alternating-for-167-rows', alternating(167),
     {'power_within_80kw': (1, 498, '80.5 kW on the 500 ms average at 498 ms')}),
    ('alternating-cut-short-at-166-rows', alternating(166), {}),
]


def hard(rows_on: int, n: int = 600, amps: float = 12.0) -> list[dict]:
    """Hard braking at 500 V and amps on rows_on rows from 300 ms."""
    return powered(500.0, amps, rows_on, n=n, fw_hard_braking=1)


# 166 rows is 498 ms, 167 is 501 ms: past the BSPD's 500.
BSPD = [
    ('6-kw-for-498-ms', hard(166), {}),
    ('6-kw-for-501-ms', hard(167),
     {'bspd_not_tripped': (1, 798, 'hard braking with 6.0 kW for 501 ms at 798 ms')}),
    ('6-kw-for-510-ms', hard(170),
     {'bspd_not_tripped': (4, 798, 'hard braking with 6.0 kW for 501 ms at 798 ms')}),
    ('5-kw-for-a-second', hard(334, amps=10.0, n=700), {}),
    ('6-kw-without-hard-braking',
     put(hard(200), 0, fw_hard_braking=0), {}),
    # One row without hard braking restarts the timer.
    ('6-kw-for-498-ms-twice', put(hard(333), 798, 801, fw_hard_braking=0), {}),
    ('6-kw-for-498-ms-then-501', put(hard(334), 798, 801, fw_hard_braking=0),
     {'bspd_not_tripped': (1, 1299, 'hard braking with 6.0 kW for 501 ms at 1299 ms')}),
    # 170 rows would fail, but the run ends 166 rows in.
    ('6-kw-cut-short-at-498-ms', hard(170, n=266), {}),
]


# 100 and 500 ms fall between 3 ms ticks, so whether a stretch exactly as long
# as the window breaks it (EV.3.3.1 and EV.7.7.2: "or more") only shows with a
# window that is a whole number of ticks.
INCLUSIVE = [
    ('power', 'POWER_CONTINUOUS_MS', 99, powered(*KW_80_1, 33), 'power_within_80kw',
     (1, 396, 'over 80 kW for 99 ms at 396 ms (80.1 kW)')),
    ('bspd', 'BSPD_TRIP_MS', 498, hard(166), 'bspd_not_tripped',
     (1, 795, 'hard braking with 6.0 kW for 498 ms at 795 ms')),
]


@pytest.mark.parametrize('window, ms, rows, name, expected', [c[1:] for c in INCLUSIVE],
                         ids=[c[0] for c in INCLUSIVE])
def test_a_stretch_as_long_as_the_window_breaks_it(monkeypatch, window, ms, rows, name,
                                                   expected):
    assert broken(rows) == {}
    monkeypatch.setattr(invariants, window, ms)
    assert broken(rows) == {name: expected}


CASES = [*OUTSIDE_DRIVE, *LIMIT, *BOT_CUTS, *BOT_DETECTED, *FAULT_CUTS, *DRIVE_NEEDS, *POWER,
         *BSPD]


@pytest.mark.parametrize('rows, expected', [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_a_run_breaks_exactly_these_invariants(rows, expected):
    assert broken(rows) == expected


def test_every_invariant_is_tested_failing():
    assert set().union(*(c[2] for c in CASES)) == set(invariants.RULES)


def test_the_latch_clearing_at_exactly_5_1_percent_fails():
    assert broken(bot_released(0.051)) == {'bot_cuts_torque': (1, 1149, cleared('5.1%'))}


def test_drive_on_the_first_tick_is_an_entry_without_a_press():
    rows = run(10, fw_state=DRIVE)
    assert broken(rows) == entered(0, NO_EDGE)


def test_the_torque_limit_is_params_hpps(monkeypatch):
    assert fwmodel.THRESHOLDS['InverterParams::torque_limit_nm'] == 230.0
    rows = put(launch(), 1104, fw_inverter_enable=1, fw_torque_request_nm=200.1)
    assert broken(rows) == {}
    monkeypatch.setitem(fwmodel.THRESHOLDS, 'InverterParams::torque_limit_nm', 200.0)
    assert broken(rows) == {'torque_within_limit': (32, 1104, '200.1 N.m at 1104 ms, past the '
                                                              '200 N.m limit')}


def test_trace_csv_read_back_gives_the_same_results(tmp_path):
    rows = put(bot_released(0.6), 1155, 1158, fw_torque_request_nm=300.0)
    full = [{c: r.get(c, 0.0) for c in cosim.COLUMNS} for r in rows]
    path = tmp_path / 'trace.csv'
    report.write_trace(cosim.Result('launch', rows=full), path)
    back = report.read_trace(path)
    assert all(isinstance(v, float) for v in back[0].values())
    assert invariants.check(back) == invariants.check(rows)
    assert broken(back) == {
        'torque_within_limit': (1, 1155, '300 N.m at 1155 ms, past the 230 N.m limit'),
        'bot_cuts_torque': (1, 1149, cleared('60.0%')),
        'bot_detected': (17, 1149, 'no BRAKE_OVER_THROTTLE at 1149 ms with the pedal at 60.0% '
                                   'and the filtered brake about 350 psi')}


# Waivers.

def test_a_waived_invariant_keeps_what_broke_it():
    rows = put(run(10), 9, 12, fw_torque_request_nm=300.0)
    results = {r.name: r for r in invariants.check(rows, {'torque_within_limit': 'the test'})}
    waived = results['torque_within_limit']
    assert (waived.result, waived.failed, waived.reason) == ('waived', False, 'the test')
    assert waived.meta() == {
        'name': 'torque_within_limit', 'basis': 'design', 'rule': 'InverterParams::torque_limit_nm',
        'result': 'waived', 'ticks': 1, 'first_ms': 9,
        'message': '300 N.m at 9 ms, past the 230 N.m limit', 'reason': 'the test'}
    # The one it does not waive still fails.
    assert (results['enable_only_in_drive'].result, results['enable_only_in_drive'].failed,
            results['enable_only_in_drive'].reason) == ('fail', True, None)


def test_a_waiver_the_run_never_uses_fails():
    results = invariants.check(launch(), {'bspd_not_tripped': 'brakes hard at speed'})
    unused = next(r for r in results if r.name == 'bspd_not_tripped')
    assert unused.failed
    assert unused.meta() == {
        'name': 'bspd_not_tripped', 'basis': 'rules',
        'rule': 'EV.7.7.2, on the car model\'s DC power', 'result': 'unused_waiver', 'ticks': 0,
        'first_ms': None,
        'message': 'waived, but the run never broke it: the drive no longer tests what its '
                   'waiver says (or delete the waiver)',
        'reason': 'brakes hard at speed'}
    assert [r.name for r in results if r.failed] == ['bspd_not_tripped']


def test_a_passing_result_is_not_failed():
    r = result(launch(), 'bot_detected')
    assert not r.failed
    assert r.meta() == {'name': 'bot_detected', 'basis': 'rules',
                        'rule': invariants.RULES['bot_detected'].rule, 'result': 'pass',
                        'ticks': 0, 'first_ms': None, 'message': '', 'reason': None}
    assert invariants.check(launch(), None) == invariants.check(launch(), {})


# Empty and malformed runs.

def test_an_empty_run_passes_every_invariant():
    assert [(r.name, r.result) for r in invariants.check([])] == [
        (name, 'pass') for name in invariants.RULES]
    # A waiver on an empty run never fires.
    assert result([], 'bot_detected', {'bot_detected': 'x'}).result == 'unused_waiver'


def test_a_one_tick_run_is_judged():
    assert broken(run(1, fw_inverter_enable=1)) == {
        'enable_only_in_drive': (1, 0, 'inverter_enable set in IDLE at 0 ms')}


BAD_TIMES = [
    ('starts-at-3', [3, 6, 9]),
    ('a-gap', [0, 3, 9]),
    ('a-repeat', [0, 3, 3]),
    ('2-ms-steps', [0, 2, 4]),
    ('backwards', [3, 0]),
]


@pytest.mark.parametrize('times', [c[1] for c in BAD_TIMES], ids=[c[0] for c in BAD_TIMES])
def test_rows_not_one_per_step_from_0_are_refused(times):
    rows = [{**r, 't_ms': t} for r, t in zip(run(len(times)), times, strict=True)]
    with pytest.raises(ValueError, match='the rows are not one per 3 ms step from 0'):
        invariants.check(rows)


# Pins.

def test_the_invariants_are_the_loaders_waivable_names_in_order():
    assert tuple(invariants.RULES) == scenario_file.INVARIANTS


def _loader_codes() -> set[str]:
    """Every error code scenario_file.py passes to self.add or Problem()."""
    codes = set()
    for node in ast.walk(ast.parse(SCENARIO_FILE_PY.read_text())):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == 'add' and node.args:
            code = node.args[0]
        elif isinstance(func, ast.Name) and func.id == 'Problem' and len(node.args) > 1:
            code = node.args[1]
        else:
            continue
        codes |= {c.value for c in ast.walk(code)
                  if isinstance(c, ast.Constant) and isinstance(c.value, str)}
    return codes


def test_invariant_names_are_no_other_code():
    codes = _loader_codes()
    assert {'yaml_syntax', 'unknown_key', 'not_modelled', 'bad_value', 'missing_key'} <= codes
    names = set(invariants.RULES)
    others = {
        'warnings': set(scenario_file.WARNINGS),
        'lint rules': set(check.RULES),
        'loader errors': codes,
        'results': {'expect_failed', 'not_evaluated', 'unused_waiver'},
    }
    assert {kind: names & taken for kind, taken in others.items()} == {
        kind: set() for kind in others}


@pytest.mark.parametrize('name', list(invariants.RULES))
def test_every_invariant_says_what_it_stands_on(name):
    rule = invariants.RULES[name]
    assert rule.name == name
    assert rule.basis in {'rules', 'design'}
    assert rule.rule.strip() and rule.fails.strip()


def test_only_the_teams_own_choices_are_design():
    assert [name for name, rule in invariants.RULES.items() if rule.basis == 'design'] == [
        'enable_only_in_drive', 'torque_within_limit']


def test_the_failure_lines_quote_the_thresholds():
    fails = {name: rule.fails for name, rule in invariants.RULES.items()}
    assert '230 N.m' in fails['torque_within_limit'] and '0.1 N.m' in fails['torque_within_limit']
    assert '5% or more' in fails['bot_cuts_torque']
    assert 'over 25%' in fails['bot_detected']
    assert 'under 30 psi' in fails['drive_needs_rtd_and_brake']
    assert fails['power_within_80kw'] == ('DC power over 80 kW for 100 ms, or over it on a 500 '
                                          'ms moving average')
    assert 'more than 5 kW for 500 ms' in fails['bspd_not_tripped']


def _fault_bits() -> dict[str, int]:
    """faults.hpp's FaultBit enum: kFaultXxx -> its bit."""
    enum = re.search(r'enum FaultBit : uint32_t \{(.*?)\};', FAULTS_HPP.read_text(), re.S)
    assert enum, FAULTS_HPP
    return {name: int(bit) for name, bit in re.findall(r'(kFault\w+) = 1u << (\d+),',
                                                       enum.group(1))}


def _spec_name(fault_bit: str) -> str:
    """kFaultApps1Range -> APPS1_RANGE."""
    return re.sub(r'(?<!^)(?=[A-Z])', '_', fault_bit.removeprefix('kFault')).upper()


def test_vcu_faults_bits_are_faults_hpps():
    fault_bits = _fault_bits()
    assert len(fault_bits) == len(VCU_FAULTS)
    assert {_spec_name(name): bit + 1 for name, bit in fault_bits.items()} == VCU_FAULTS
    assert invariants.BOT_BIT == 1 << fault_bits['kFaultBrakeOverThrottle']


def test_torque_cut_faults_are_faults_hpps_ktorquecutfaults():
    fault_bits = _fault_bits()
    mask = re.search(r'kTorqueCutFaults =(.*?);', FAULTS_HPP.read_text(), re.S)
    assert mask, FAULTS_HPP
    names = re.findall(r'kFault\w+', mask.group(1))
    by_code = {code: name for name, code in VCU_FAULTS.items()}
    # A fault maps by its bit (bit n is VcuFaultCode n + 1), not by its spelling.
    cut = [by_code[fault_bits[name] + 1] for name in names]
    assert len(cut) == len(set(cut)) == len(invariants.TORQUE_CUT_FAULTS)
    assert set(cut) == set(invariants.TORQUE_CUT_FAULTS)
    assert invariants.CUT_MASK == sum(1 << fault_bits[name] for name in names)


def test_the_rules_cut_faults_are_cut_faults():
    assert set(invariants.RULES_CUT_FAULTS) <= set(invariants.TORQUE_CUT_FAULTS)


def test_states_faults_and_torque_step_match_the_spec(can_table_path):
    table = CanTable(can_table_path)
    assert VCU_STATES == {name: int(number)
                          for number, name in table.enums['VcuStateMachine'].items()}
    assert VCU_FAULTS == {name: int(number) for number, name in table.enums['VcuFaultCode'].items()
                          if name != 'NONE'}
    assert table.enums['VcuFaultCode']['0'] == 'NONE'
    command = next(m for m in table.messages if m['name'] == 'INVERTER_TORQUE_COMMAND')
    torque = next(s for s in command['signals'] if s['name'] == 'torque_request')
    assert torque['scale'] == invariants.TORQUE_STEP_NM
    assert torque['signed']
