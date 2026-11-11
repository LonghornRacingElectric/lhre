"""Warnings and python -m vcusil.check: each rule at its boundaries, its
thresholds against params.hpp and against the firmware itself. The models
the rules run are held to the library in test_check_firmware.py."""

import ast
import math
import re
from pathlib import Path

import pytest
from test_firmware import hold, parked

from vcusil import check, scenario_file
from vcusil.firmware import HVC_STATES, SATURATION_BITS, Firmware, FirmwareError
from vcusil.scenario_file import SIGNALS, WARNINGS

SIL = Path(__file__).resolve().parents[2]
PARAMS = SIL.parent / 'App' / 'config' / 'params.hpp'
PARAMS_CPP = SIL.parent / 'App' / 'config' / 'params.cpp'
LOOP_CLOSED, LOOP_UPSTREAM_OPEN = 1, 3  # lhre ShutdownLoopState

# A clean power-up: every rule stays quiet on it. RTD is first sampled at
# 1002 and debounced at 1023, on 350 psi of brake.
BASE = '''schema = "lhre.vcu-sil-scenario.v1"
description = "A clean power-up for the lint tests."
duration_ms = 3000

[[at]]
t_ms = 0
brake_psi = 350.0

[[at]]
t_ms = 200
hvc_state = "PRECHARGING"

[[at]]
t_ms = 600
hvc_state = "ENERGIZED"
contactors_closed = true

[[at]]
t_ms = 1000
rtd_pressed = true

[[at]]
t_ms = 1500
brake_psi = 0.0
'''


def drive(*edits, then: str = '') -> str:
    text = BASE
    for old, new in edits:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    return text + then


def at(t_ms: int, *lines: str) -> str:
    return f'\n[[at]]\nt_ms = {t_ms}\n' + ''.join(f'{line}\n' for line in lines)


def insert_before(t_ms: int, *blocks: str) -> tuple[str, str]:
    """A drive() edit that puts blocks just before BASE's block at t_ms."""
    header = f'[[at]]\nt_ms = {t_ms}\n'
    return header, ''.join(blocks).lstrip('\n') + '\n' + header


def hold_at_2000(key: str, value: object, back: object = 0.0) -> str:
    return at(2000, f'{key} = {value}') + at(2500, f'{key} = {back}')


def brake_off_at(t_ms: int) -> str:
    """BASE with the brake off at t_ms, before the press at 1000."""
    return drive(('t_ms = 1500\nbrake_psi = 0.0', f't_ms = {t_ms}\nbrake_psi = 0.0'),
                 ('[[at]]\nt_ms = 1000\nrtd_pressed = true\n\n', ''),
                 then=at(1000, 'rtd_pressed = true'))


def lint(text: str, **kw) -> check.Findings:
    return check.lint(scenario_file.loads(text.encode(), 'lint-test'), **kw)


def line_of(text: str, anchor: str) -> int:
    found = [n for n, line in enumerate(text.split('\n'), 1) if anchor in line]
    assert len(found) == 1, (anchor, found)
    return found[0]


# Ready to drive from 0 ms, so only the switch decides an early press.
READY_AT_0 = (('brake_psi = 350.0', 'brake_psi = 350.0\nhvc_state = "ENERGIZED"\n'
               'contactors_closed = true'),
              ('[[at]]\nt_ms = 200\nhvc_state = "PRECHARGING"\n\n', ''),
              ('[[at]]\nt_ms = 600\nhvc_state = "ENERGIZED"\ncontactors_closed = true\n\n', ''))


def press_at(t_ms: int) -> str:
    return drive(*READY_AT_0, ('t_ms = 1000\nrtd', f't_ms = {t_ms}\nrtd'))


UNPLUGGED_RTD = ('[[at]]\nt_ms = 1000\nrtd_pressed = true\n', '')
ALLOW_BRAKE_HIGH = ('[[at]]\nt_ms = 0\n', '[allow]\nbrake_high = "a hard stop"\n\n'
                                          '[[at]]\nt_ms = 0\n')

def hard_stop_pedal_at(t_ms: int) -> str:
    """The review's probe: 3000 psi from 0, held at 200 from 2001. The filter
    is under 250 psi (brake-pressed off) from 2115, and under 225 (the lint's
    margin) from 2133."""
    return drive(ALLOW_BRAKE_HIGH, ('brake_psi = 350.0', 'brake_psi = 3000.0'),
                 ('t_ms = 1500\nbrake_psi = 0.0', 't_ms = 2001\nbrake_psi = 200.0'),
                 then=at(t_ms, 'accelerator = 0.5'))


def pedal_before_the_press(pedal: float, off_at: int | None = None) -> str:
    """accelerator at pedal from 900 ms, and back to 0 at off_at (else 2000)."""
    off = at(off_at, 'accelerator = 0.0') if off_at else ''
    return drive(insert_before(1000, at(900, f'accelerator = {pedal}')),
                 insert_before(1500, off), then='' if off_at else at(2000, 'accelerator = 0.0'))


def released(t_ms: int, again: int | None) -> str:
    """BASE, with RTD released at t_ms and pressed again at again."""
    return drive(then=at(t_ms, 'rtd_pressed = false')
                 + (at(again, 'rtd_pressed = true') if again else ''))


def loop_open_from_0_until(t_ms: int) -> str:
    open_at_0 = ('brake_psi = 350.0', 'brake_psi = 350.0\nshutdown_loop_v = 0.0')
    close = at(t_ms, 'shutdown_loop_v = 24.0')
    if t_ms < 1500:
        return drive(open_at_0, insert_before(1000 if t_ms < 1000 else 1500, close))
    return drive(open_at_0, then=close)


def loop_held_from_0(volts: float) -> str:
    return drive(('brake_psi = 350.0', f'brake_psi = 350.0\nshutdown_loop_v = {volts}'))


SHORT_RUN = '''schema = "lhre.vcu-sil-scenario.v1"
description = "Shorter than the RTD debounce."
duration_ms = 15

[[at]]
t_ms = 0
rtd_pressed = false
'''


def ramp_end_then(after: str) -> str:
    """A brake ramp to 300 that ends at 2100, where after changes it."""
    return drive(then=at(2001, 'brake_psi = { to = 300.0, ramp_ms = 99 }')
                 + at(2100, f'brake_psi = {after}'))


def disagree_until(t_ms: int) -> str:
    return drive(('contactors_closed = true\n', ''), then=at(t_ms, 'contactors_closed = true'))


# (id, text, code, the warning's t_ms, line anchor)
FIRES = [
    ('brake-300', drive(then=hold_at_2000('brake_psi', 300.0)), 'brake_near_threshold', 2001,
     'brake_psi = 300.0'),
    ('brake-30', drive(then=hold_at_2000('brake_psi', 30.0)), 'brake_near_threshold', 2001,
     'brake_psi = 30.0'),
    ('brake-10', drive(then=hold_at_2000('brake_psi', 10.0)), 'brake_near_threshold', 2001,
     'brake_psi = 10.0'),
    ('brake-531', drive(then=hold_at_2000('brake_psi', 531.0)), 'brake_near_threshold', 2001,
     'brake_psi = 531.0'),
    # The ramp's end is still the value at the next ramp's start sample.
    ('brake-ramp-end-held-into-a-ramp', ramp_end_then('{ to = 0.0, ramp_ms = 100 }'),
     'brake_near_threshold', 2100, 'brake_psi = { to = 300.0, ramp_ms = 99 }'),
    ('brake-1001', drive(then=hold_at_2000('brake_psi', 1001.0)), 'brake_high', 2001,
     'brake_psi = 1001.0'),
    ('brake-1000.01', drive(then=hold_at_2000('brake_psi', 1000.01)), 'brake_high', 2001,
     'brake_psi = 1000.01'),
    ('pedal-0.05', drive(then=hold_at_2000('accelerator', 0.05)), 'pedal_in_deadzone', 2001,
     'accelerator = 0.05'),
    ('pedal-0.09', drive(then=hold_at_2000('accelerator', 0.09)), 'pedal_in_deadzone', 2001,
     'accelerator = 0.09'),
    ('pedal-0.74', drive(then=hold_at_2000('accelerator', 0.74)), 'pedal_in_deadzone', 2001,
     'accelerator = 0.74'),
    ('pedal-0.8', drive(then=hold_at_2000('accelerator', 0.8)), 'pedal_in_deadzone', 2001,
     'accelerator = 0.8'),
    ('loop-18', drive(then=hold_at_2000('shutdown_loop_v', 18.0, 24.0)), 'loop_near_threshold',
     2001, 'shutdown_loop_v = 18.0'),
    ('loop-19.67', drive(then=hold_at_2000('shutdown_loop_v', 19.67, 24.0)),
     'loop_near_threshold', 2001, 'shutdown_loop_v = 19.67'),
    ('loop-16.1', drive(then=hold_at_2000('shutdown_loop_v', 16.1, 24.0)), 'loop_near_threshold',
     2001, 'shutdown_loop_v = 16.1'),
    # The filter reaches 275 psi on the 12th tick of 400 psi, at 2034.
    ('bot-as-the-filter-rises', drive(then=at(1900, 'accelerator = 0.3')
                                      + at(2000, 'brake_psi = 400.0')),
     'brake_over_throttle', 2034, 'brake_psi = 400.0'),
    ('bot-105-ms-after-a-hard-stop', hard_stop_pedal_at(2106), 'brake_over_throttle', 2106,
     'accelerator = 0.5'),
    ('bot-129-ms-after-a-hard-stop', hard_stop_pedal_at(2130), 'brake_over_throttle', 2130,
     'accelerator = 0.5'),
    ('bot-pedal-0.25', drive(then=at(2000, 'brake_psi = 400.0') + at(2100, 'accelerator = 0.25')),
     'brake_over_throttle', 2100, 'accelerator = 0.25'),
    ('rtd-at-boot', drive(UNPLUGGED_RTD, ('brake_psi = 350.0', 'brake_psi = 350.0\n'
                                                               'rtd_pressed = true')),
     'rtd_at_boot', 0, 'rtd_pressed = true'),
    ('rtd-pressed-at-15', press_at(15), 'rtd_at_boot', 15, 'rtd_pressed = true'),
    ('rtd-pressed-at-21', press_at(21), 'rtd_at_boot', 21, 'rtd_pressed = true'),
    ('rtd-pressed-1000-to-1012', drive(insert_before(1500, at(1012, 'rtd_pressed = false'))),
     'rtd_too_short', 1002, 'rtd_pressed = true'),
    ('rtd-released-for-21-ms', released(2000, 2022), 'rtd_too_short', 2001,
     'rtd_pressed = false'),
    ('rtd-released-at-2977', released(2977, None), 'rtd_too_short', 2979, 'rtd_pressed = false'),
    ('rtd-no-brake', drive(('brake_psi = 350.0', 'brake_psi = 0.0')), 'rtd_without_brake', 1002,
     'rtd_pressed = true'),
    ('rtd-brake-54', drive(insert_before(600, at(500, 'brake_psi = 54.0'))), 'rtd_without_brake',
     1002, 'rtd_pressed = true'),
    ('rtd-brake-off-at-972', brake_off_at(972), 'rtd_without_brake', 1002, 'rtd_pressed = true'),
    ('rtd-brake-off-at-954', brake_off_at(954), 'rtd_without_brake', 1002, 'rtd_pressed = true'),
    ('rtd-brake-29.2', drive(insert_before(600, at(500, 'brake_psi = 29.2'))), 'rtd_without_brake',
     1002, 'rtd_pressed = true'),
    ('rtd-with-pedal', drive(insert_before(1000, at(980, 'accelerator = 0.1'))), 'rtd_with_pedal',
     1002, 'rtd_pressed = true'),
    ('rtd-pedal-0.051', pedal_before_the_press(0.051), 'rtd_with_pedal', 1002,
     'rtd_pressed = true'),
    ('rtd-pedal-0.0908', pedal_before_the_press(0.0908), 'rtd_with_pedal', 1002,
     'rtd_pressed = true'),
    # 0.2 * 0.65 ** 3 = 0.055 at 1023.
    ('rtd-pedal-off-at-1017', pedal_before_the_press(0.2, 1017), 'rtd_with_pedal', 1002,
     'rtd_pressed = true'),
    ('rtd-10-ms-after-energized', drive(('t_ms = 1000\nrtd', 't_ms = 610\nrtd')),
     'rtd_before_ready', 612, 'rtd_pressed = true'),
    # The window opens 50 ms before the press: 597 is still PRECHARGING.
    ('rtd-45-ms-after-energized', drive(('t_ms = 1000\nrtd', 't_ms = 645\nrtd')),
     'rtd_before_ready', 645, 'rtd_pressed = true'),
    ('rtd-without-contactors', drive(('contactors_closed = true\n', '')), 'rtd_before_ready', 1002,
     'rtd_pressed = true'),
    ('rtd-loop-open', loop_open_from_0_until(2000), 'rtd_before_ready', 1002,
     'rtd_pressed = true'),
    # Ready wants the loop above the loop_near_threshold band, which ends at 19.67.
    ('rtd-loop-19.67', loop_held_from_0(19.67), 'rtd_before_ready', 1002, 'rtd_pressed = true'),
    # ... and closes at the debounced edge, 1023.
    ('rtd-loop-opens-at-the-edge', drive(insert_before(1500, at(1023, 'shutdown_loop_v = 0.0'))),
     'rtd_before_ready', 1002, 'rtd_pressed = true'),
    ('disagree-101-ms', drive(('contactors_closed = true\n', ''),
                              insert_before(1000, at(701, 'contactors_closed = true'))),
     'hvc_contactors_disagree', 600, 'hvc_state = "ENERGIZED"'),
    ('disagree-to-the-end', drive(('contactors_closed = true\n', '')), 'hvc_contactors_disagree',
     600, 'hvc_state = "ENERGIZED"'),
    # The block that starts it sets both keys: the warning is on the one that changed.
    ('disagree-set-in-one-block', drive(('contactors_closed = true', 'contactors_closed = false')),
     'hvc_contactors_disagree', 600, 'hvc_state = "ENERGIZED"'),
    ('unused-allow', drive(('[[at]]\nt_ms = 0\n', '[allow]\nbrake_high = "for the test"\n\n'
                                                  '[[at]]\nt_ms = 0\n')),
     'unused_allow', None, 'brake_high = "for the test"'),
]

# (id, text, code, lint options)
QUIET = [
    *[(f'brake-{v}', drive(then=hold_at_2000('brake_psi', v)), 'brake_near_threshold', {})
      for v in (350.0, 60.0, 500.0, 0.0)],
    # A ramp's end replaced on its own sample by a step is never held.
    ('brake-ramp-end-never-held', ramp_end_then('0.0'), 'brake_near_threshold', {}),
    ('brake-1000', drive(then=hold_at_2000('brake_psi', 1000.0)), 'brake_high', {}),
    ('brake-1001-of-3000', drive(then=hold_at_2000('brake_psi', 1001.0)), 'brake_high',
     {'brake_psi_full': 3000.0}),
    *[(f'pedal-{v}', drive(then=hold_at_2000('accelerator', v)), 'pedal_in_deadzone', {})
      for v in (0.0900001, 0.1, 0.73, 0.7399999, 1.0, 0.0)],
    *[(f'loop-{v}', drive(then=hold_at_2000('shutdown_loop_v', v, 24.0)), 'loop_near_threshold',
       {}) for v in (19.68, 19.671, 19.8, 16.09, 16.099, 16.0)],
    ('bot-pedal-0.23', drive(then=at(2000, 'brake_psi = 400.0') + at(2100, 'accelerator = 0.23')),
     'brake_over_throttle', {}),
    ('bot-132-ms-after-a-hard-stop', hard_stop_pedal_at(2133), 'brake_over_throttle', {}),
    ('rtd-on-the-brake', BASE, 'rtd_at_boot', {}),
    ('rtd-pressed-at-24', press_at(24), 'rtd_at_boot', {}),
    ('rtd-held', BASE, 'rtd_too_short', {}),
    # The boot reading the press cuts short is no change of the file's.
    ('rtd-pressed-at-15-is-at-boot', press_at(15), 'rtd_too_short', {}),
    ('rtd-released-for-22-ms', released(2000, 2023), 'rtd_too_short', {}),
    ('rtd-released-at-2976', released(2976, None), 'rtd_too_short', {}),
    # The boot reading at 0 is no change of the file's, even in a run too
    # short for the debounce to take it.
    ('rtd-run-shorter-than-the-debounce', SHORT_RUN, 'rtd_too_short', {}),
    # Off the brake after the press: the edge at 1023 still sees 350 psi.
    ('rtd-brake-off-at-1040', drive(('t_ms = 1500', 't_ms = 1040')), 'rtd_without_brake', {}),
    ('rtd-brake-56', drive(insert_before(600, at(500, 'brake_psi = 56.0'))), 'rtd_without_brake',
     {}),
    ('rtd-brake-off-at-975', brake_off_at(975), 'rtd_without_brake', {}),
    ('rtd-pedal-up', BASE, 'rtd_with_pedal', {}),
    ('rtd-pedal-0.049', pedal_before_the_press(0.049), 'rtd_with_pedal', {}),
    ('rtd-pedal-off-at-1014', pedal_before_the_press(0.2, 1014), 'rtd_with_pedal', {}),
    ('rtd-400-ms-after-energized', BASE, 'rtd_before_ready', {}),
    ('rtd-48-ms-after-energized', drive(('t_ms = 1000\nrtd', 't_ms = 648\nrtd')),
     'rtd_before_ready', {}),
    ('rtd-loop-closed-at-900', loop_open_from_0_until(900), 'rtd_before_ready', {}),
    ('rtd-loop-19.68', loop_held_from_0(19.68), 'rtd_before_ready', {}),
    ('rtd-loop-opens-after-the-edge', drive(insert_before(1500, at(1024,
                                                                   'shutdown_loop_v = 0.0'))),
     'rtd_before_ready', {}),
    ('disagree-30-ms', drive(('contactors_closed = true\n', ''),
                             insert_before(1000, at(630, 'contactors_closed = true'))),
     'hvc_contactors_disagree', {}),
    ('disagree-100-ms', drive(('contactors_closed = true\n', ''),
                              insert_before(1000, at(700, 'contactors_closed = true'))),
     'hvc_contactors_disagree', {}),
    ('allow-used', drive(('[[at]]\nt_ms = 0\n', '[allow]\nbrake_high = "for the test"\n\n'
                                                '[[at]]\nt_ms = 0\n'),
                         then=hold_at_2000('brake_psi', 1001.0)), 'unused_allow', {}),
]


def test_the_base_drive_is_clean():
    assert lint(BASE) == check.Findings((), ())


@pytest.mark.parametrize('text, code, t_ms, anchor', [c[1:] for c in FIRES],
                         ids=[c[0] for c in FIRES])
def test_rule_fires(text, code, t_ms, anchor):
    findings = lint(text)
    found = [p for p in (*findings.active, *(p for p, _ in findings.allowed)) if p.code == code]
    assert len(found) == 1, found
    p = found[0]
    assert (p.level, p.t_ms, p.line) == ('warning', t_ms, line_of(text, anchor)), p
    assert p.where == (f'at {t_ms} ms' if t_ms is not None else 'allow.brake_high')


@pytest.mark.parametrize('text, code, options', [c[1:] for c in QUIET],
                         ids=[c[0] for c in QUIET])
def test_rule_stays_quiet(text, code, options):
    findings = lint(text, **options)
    assert code not in {p.code for p in (*findings.active, *(p for p, _ in findings.allowed))}


def test_every_rule_is_tested_both_ways():
    assert {c[2] for c in FIRES} == {c[2] for c in QUIET} == set(check.RULES)


def test_rules_are_the_codes_allow_takes():
    assert tuple(check.RULES) == WARNINGS


def test_rules_text():
    """--help prints these, and the README's rule table says the same."""
    assert check.RULES == {
        'brake_near_threshold': 'a held brake_psi within 25 psi of 30, 250, 300 or 531 psi: too '
                                'close to the threshold to rely on (inside the 250..300 '
                                'hysteresis the previous state decides)',
        'brake_high': 'a held brake_psi over 2/3 of --brake-psi-full: the car model reverses '
                      'near full brake',
        'pedal_in_deadzone': 'a held accelerator in the 9 % or 74 % deadzone, which asks for no '
                             'torque or for full-pedal torque',
        'loop_near_threshold': "a held shutdown_loop_v in 16.10..19.67 V: within 0.5 V of the "
                               "loop's low (16.60 V) or high (19.17 V) threshold, or between "
                               'them, where it reads what it read before',
        'brake_over_throttle': 'accelerator over 23 % while the modelled filtered brake may hold '
                               'brake-pressed (from 275 psi until under 225; the firmware\'s is '
                               '300/250): torque is cut until the pedal is under 5 %',
        'rtd_at_boot': 'an RTD press before the switch has read released for 20 ms since boot: '
                       'it starts as pressed, so the firmware never sees the press',
        'rtd_too_short': 'an rtd_pressed change that does not hold for the 20 ms debounce (it '
                         'changes back, or the run ends, first): the firmware never sees it',
        'rtd_without_brake': 'the modelled filtered brake at a debounced RTD press edge under 55 '
                             'psi: RTD needs 30 psi (brake_light_psi)',
        'rtd_with_pedal': 'the modelled filtered accelerator at a debounced RTD press edge over '
                          '5 %: RTD needs it at or under the 9 % deadzone (min_travel_deadzone)',
        'rtd_before_ready': 'an RTD press without hvc_state ENERGIZED, contactors_closed true and '
                            'shutdown_loop_v over 19.67 V from 50 ms before it to its debounced '
                            'edge: RTD_WAIT trails them, so the edge may come outside RTD_WAIT, '
                            'which uses it up',
        'hvc_contactors_disagree': 'hvc_state ENERGIZED without contactors_closed, or the '
                                   "reverse, for over 100 ms of the file's time",
        'unused_allow': 'an [allow] code that never fires: the drive no longer tests what it '
                        'claims',
    }


FIRED = {c[0]: c for c in FIRES}

# Whole messages: a rule's wording says what the firmware does, and how sure
# the lint can be of it.
MESSAGES = {
    'brake-300': 'brake_psi holds 300.0, within 25 psi of the brake-pressed hysteresis, on at '
                 '300 and off under 250 psi of filtered brake (inside 250..300 the previous '
                 'state decides): too close to rely on; hold it under 225 or over 325',
    'brake-30': 'brake_psi holds 30.0, within 25 psi of the 30 psi RTD brake (brake_light_psi): '
                'too close to rely on; hold it under 5 or over 55',
    'brake-531': 'brake_psi holds 531.0, within 25 psi of the hard-brake comparators at about '
                 '531 psi (hard_brake_pin_v): too close to rely on; hold it under 506 or over 556',
    'brake-1001': 'brake_psi holds 1001.0, over 2/3 of --brake-psi-full (1000 psi at 1500): the '
                  'car model reverses near full brake',
    'pedal-0.09': 'accelerator holds 0.09, inside the 9 % deadzone (min_travel_deadzone): it asks '
                  'for no torque',
    'pedal-0.74': 'accelerator holds 0.74, past the 74 % deadzone (max_travel_deadzone): it asks '
                  'for full-pedal torque',
    'loop-19.67': "shutdown_loop_v holds 19.67, within 0.5 V of the loop's low (16.60 V) or high "
                  '(19.17 V) threshold, or between them, where it reads what it read before; use '
                  'about 24.0 for closed or 0.0 for open',
    'bot-105-ms-after-a-hard-stop': 'accelerator 0.5 is over 23 % while the filtered brake (about '
                                    '263 psi) may hold brake-pressed, on at 300 and off under '
                                    '250 psi: brake-over-throttle cuts torque until the pedal is '
                                    'under 5 %',
    'rtd-pressed-at-15': 'the RTD switch starts as pressed and needs 20 ms released before a '
                         'press counts, so the firmware never sees this press; hold rtd_pressed '
                         'false for 24 ms first',
    'rtd-pressed-1000-to-1012': 'this press changes back at 1014 ms, but the 20 ms debounce '
                                'needs it held through 1023 ms, so the firmware never sees it; '
                                'hold it for 24 ms or more',
    'rtd-released-for-21-ms': 'this release changes back at 2022 ms, but the 20 ms debounce '
                              'needs it held through 2022 ms, so the firmware never sees it; '
                              'hold it for 24 ms or more',
    'rtd-released-at-2977': 'the last sample is at 2997 ms, but the 20 ms debounce needs this '
                            'release held through 3000 ms, so the firmware never sees it; '
                            'lengthen the run or move it earlier',
    'rtd-no-brake': 'the filtered brake at the debounced edge (1023 ms) is about 0 psi; RTD needs '
                    '30 psi (brake_light_psi), so the firmware refuses it',
    'rtd-brake-off-at-954': 'the filtered brake at the debounced edge (1023 ms) is about 28 psi; '
                            'RTD needs 30 psi (brake_light_psi), so the firmware refuses it',
    'rtd-brake-off-at-972': 'the filtered brake at the debounced edge (1023 ms) is about 53 psi; '
                            'RTD needs 30 psi (brake_light_psi), and under 55 psi is too close '
                            'to rely on',
    # The models leave out ADC rounding, so "refuses" waits for 1 psi or
    # 0.1 % past the threshold.
    'rtd-brake-29.2': 'the filtered brake at the debounced edge (1023 ms) is about 29 psi; RTD '
                      'needs 30 psi (brake_light_psi), and under 55 psi is too close to rely on',
    'rtd-pedal-0.0908': 'the filtered accelerator at the debounced edge (1023 ms) is about 9.1 %; '
                        'RTD needs it at or under 9 % (min_travel_deadzone), and over 5 % is too '
                        'close to rely on',
    'rtd-with-pedal': 'the filtered accelerator at the debounced edge (1023 ms) is about 10.0 %; '
                      'RTD needs it at or under 9 % (min_travel_deadzone), so the firmware '
                      'refuses it',
    'rtd-pedal-off-at-1017': 'the filtered accelerator at the debounced edge (1023 ms) is about '
                             '5.5 %; RTD needs it at or under 9 % (min_travel_deadzone), and '
                             'over 5 % is too close to rely on',
    'rtd-45-ms-after-energized': 'hvc_state ENERGIZED, contactors_closed true and '
                                 'shutdown_loop_v over 19.67 should hold from 50 ms before the '
                                 'press to its debounced edge (597..666 ms), but at 597 ms '
                                 'hvc_state is PRECHARGING and contactors_closed is false; '
                                 'RTD_WAIT trails them by the 10 ms HVC frames and the loop '
                                 'debounce, so the edge may come outside RTD_WAIT, which uses it '
                                 'up',
    'rtd-loop-opens-at-the-edge': 'hvc_state ENERGIZED, contactors_closed true and '
                                  'shutdown_loop_v over 19.67 should hold from 50 ms before the '
                                  'press to its debounced edge (954..1023 ms), but at 1023 ms '
                                  'shutdown_loop_v is 0.0; RTD_WAIT trails them by the 10 ms HVC '
                                  'frames and the loop debounce, so the edge may come outside '
                                  'RTD_WAIT, which uses it up',
    'rtd-loop-19.67': 'hvc_state ENERGIZED, contactors_closed true and shutdown_loop_v over 19.67 '
                      'should hold from 50 ms before the press to its debounced edge '
                      '(954..1023 ms), but at 954 ms shutdown_loop_v is 19.67; RTD_WAIT trails '
                      'them by the 10 ms HVC frames and the loop debounce, so the edge may come '
                      'outside RTD_WAIT, which uses it up',
    'disagree-101-ms': 'hvc_state ENERGIZED with contactors_closed false for 101 ms; the VCU '
                       'counts the HVC as energized only with both (a forgotten '
                       'contactors_closed?)',
    'disagree-to-the-end': 'hvc_state ENERGIZED with contactors_closed false for 2400 ms; the '
                           'VCU counts the HVC as energized only with both (a forgotten '
                           'contactors_closed?)',
    'unused-allow': 'brake_high is allowed but never fires; the drive no longer tests what it '
                    'claims (or remove it from [allow])',
}


@pytest.mark.parametrize('case', MESSAGES)
def test_warning_messages(case):
    _, text, code, _, _ = FIRED[case]
    findings = lint(text)
    [p] = [p for p in (*findings.active, *(p for p, _ in findings.allowed)) if p.code == code]
    assert p.message == MESSAGES[case]


def test_brake_over_throttle_fires_once_per_stretch():
    one = drive(then=at(2000, 'brake_psi = 400.0') + at(2100, 'accelerator = 0.3')
                + at(2500, 'brake_psi = 0.0'))
    assert [p.t_ms for p in lint(one).active if p.code == 'brake_over_throttle'] == [2100]
    two = one + at(2700, 'accelerator = 0.0') + at(2800, 'brake_psi = 400.0') + at(
        2900, 'accelerator = 0.3')
    assert [p.t_ms for p in lint(two).active if p.code == 'brake_over_throttle'] == [2100, 2901]


def test_ramps_through_a_band_do_not_warn():
    """Only a value held for a sample decides a state."""
    text = drive(('t_ms = 1500\nbrake_psi = 0.0', 't_ms = 1500\nbrake_psi = { to = 0.0, '
                                                  'ramp_ms = 150 }'),
                 then=at(2000, 'shutdown_loop_v = { to = 0.0, ramp_ms = 50 }'))
    assert lint(text) == check.Findings((), ())
    # The ramp's end value is held, so it is checked.
    held = drive(then=at(2000, 'brake_psi = { to = 300.0, ramp_ms = 100 }'))
    assert [(p.code, p.t_ms) for p in lint(held).active] == [('brake_near_threshold', 2100)]


def test_allow_moves_a_warning_to_allowed():
    text = drive(('[[at]]\nt_ms = 0\n', '[allow]\nbrake_high = "the car model must reverse"\n\n'
                                        '[[at]]\nt_ms = 0\n'),
                 then=hold_at_2000('brake_psi', 1200.0))
    findings = lint(text)
    assert findings.active == ()
    [(p, reason)] = findings.allowed
    assert (p.code, p.t_ms, reason) == ('brake_high', 2001, 'the car model must reverse')


def test_rtd_too_short_can_be_allowed():
    text = drive(('[[at]]\nt_ms = 0\n', '[allow]\nrtd_too_short = "a bouncing switch"\n\n'
                                        '[[at]]\nt_ms = 0\n'),
                 insert_before(1500, at(1012, 'rtd_pressed = false')))
    findings = lint(text)
    assert findings.active == ()
    assert [(p.code, p.t_ms, why) for p, why in findings.allowed] == [
        ('rtd_too_short', 1002, 'a bouncing switch')]


def test_the_brake_over_throttle_file_sets_off_only_its_allowed_warning():
    """The filter reaches 275 psi at 4095, before the firmware's 300 (about
    4104; test_scenario_file.py checks the firmware)."""
    findings = check.lint(scenario_file.load('brake-over-throttle'))
    assert findings.active == ()
    assert [(p.code, p.t_ms) for p, _ in findings.allowed] == [('brake_over_throttle', 4095)]


def test_the_models_start_from_the_firmwares_boot():
    sf = scenario_file.loads(BASE.encode(), 'lint-test')
    models = check._Lint(sf)
    # The switch reads pressed until it has read released for 20 ms.
    assert models.rtd.levels[:8] == [True] * 7 + [False]
    assert models.rtd.edges == [models.times.index(1023)]
    assert models.loop.reported[:5] == [None, None, None, None, True]  # UNKNOWN to 9 ms
    assert models.brake[0] == 350.0 and models.pedal[0] == 0.0  # seeded
    assert models.brake[1] == 350.0


def _evaluate(expr: str) -> float:
    """A params.hpp initializer: numbers (with their f suffix) and + - * /."""
    tree = ast.parse(re.sub(r'(\d+\.\d*|\.\d+)f\b', r'\1', expr.strip()), mode='eval')
    ops = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
           ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b}

    def walk(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            return ops[type(node.op)](walk(node.left), walk(node.right))
        raise ValueError(f'not a plain number: {expr}')
    return walk(tree.body)


def _header_values() -> dict[str, float]:
    values = {}
    for struct, body in re.findall(r'struct (\w+) \{(.*?)\n\};', PARAMS.read_text(), re.S):
        for name, expr in re.findall(r'^\s*(?:float|u?int\d+_t)\s+(\w+)\s*=\s*([^;]+);', body,
                                     re.M):
            values[f'{struct}::{name}'] = expr
    return values


def _overrides(header: str, source: str) -> set[str]:
    """Struct::field for each p.<member>.<field> = in an event mode's Make*()."""
    members = dict((member, struct) for struct, member in re.findall(
        r'^\s*(\w+Params)\s+(\w+);', re.search(r'struct Params \{(.*?)\n\};', header, re.S)[1],
        re.M))
    return {f'{members[member]}::{name}'
            for member, name in re.findall(r'\bp\.(\w+)\.(\w+)(?:\[[^]]*\])?\s*=[^=]', source)}


def test_thresholds_match_params_hpp():
    header = _header_values()
    for key in ('AppsParams::min_travel_deadzone', 'AppsParams::max_travel_deadzone',
                'AppsParams::ema_alpha', 'BseParams::ema_alpha',
                'BseParams::pressed_on_psi', 'BseParams::pressed_off_psi',
                'BseParams::brake_light_psi', 'BseParams::bot_pedal_on',
                'BseParams::bot_pedal_off', 'BseParams::full_scale_psi',
                'BseParams::hard_brake_pin_v', 'ShutdownParams::high_pin_v',
                'ShutdownParams::low_pin_v', 'ShutdownParams::debounce_ms',
                'DigitalParams::rtd_debounce_ms', 'DriveParams::rtd_max_pedal'):
        assert key in check.THRESHOLDS, key
    for key, value in check.THRESHOLDS.items():
        assert key in header, f'{key} is not in params.hpp'
        assert _evaluate(header[key]) == value, key
    # The ABI's brake range is the sensors' full scale.
    assert SIGNALS['brake_psi'].hi == check.THRESHOLDS['BseParams::full_scale_psi']
    assert round(check.HARD_BRAKE_PSI) == 531
    assert (round(check.LOOP_LOW_V, 2), round(check.LOOP_HIGH_V, 2)) == (16.60, 19.17)
    # No event mode changes one, so every mode shares the warnings.
    overridden = _overrides(PARAMS.read_text(), PARAMS_CPP.read_text())
    assert overridden >= {'TorqueParams::power_limit_torque', 'TorqueParams::pedal_map'}
    assert not overridden & set(check.THRESHOLDS), overridden & set(check.THRESHOLDS)


def test_an_override_in_params_cpp_is_caught():
    source = PARAMS_CPP.read_text().replace('p.torque.pedal_curve_exponent = 1.0f;',
                                            'p.torque.pedal_curve_exponent = 1.0f;\n'
                                            '  p.bse.pressed_on_psi = 320.0f;')
    assert 'BseParams::pressed_on_psi' in _overrides(PARAMS.read_text(), source)


def _settle(lib, sequence):
    """Holds each (field, value) on a parked car for 300 ms; the outputs and
    snapshot after each."""
    results = []
    with Firmware(lib) as fw:
        inputs = parked()
        for field, value in sequence:
            if field == 'brake_psi':
                inputs.brake_front_psi = inputs.brake_rear_psi = value
            else:
                setattr(inputs, field, value)
            out = hold(fw, inputs, 300)
            results.append((out, fw.read_snapshot()))
    return results


def test_band_edges_bracket_the_firmware(lib):
    """Each band's edges are on the far side of the firmware's threshold,
    whatever came before; inside a hysteresis band the past decides."""
    lo, hi, _ = check.BRAKE_BANDS[0]
    assert (lo, hi) == (5.0, 55.0)
    steps = _settle(lib, [('brake_psi', 0.0), ('brake_psi', hi), ('brake_psi', lo)])
    assert [snap['pedals_brake_for_rtd'] for _, snap in steps] == [0, 1, 0]
    lo, hi, _ = check.BRAKE_BANDS[2]
    assert (round(lo), round(hi)) == (506, 556)
    steps = _settle(lib, [('brake_psi', 0.0), ('brake_psi', lo), ('brake_psi', hi),
                          ('brake_psi', lo)])
    assert [out.hard_braking for out, _ in steps] == [0, 0, 1, 0]
    lo, hi, _ = check.BRAKE_BANDS[1]
    assert (lo, hi) == (225.0, 325.0)
    steps = _settle(lib, [('brake_psi', 0.0), ('brake_psi', lo), ('brake_psi', hi),
                          ('brake_psi', lo)])
    assert [snap['pedals_brake_pressed'] for _, snap in steps] == [0, 0, 1, 0]
    lo, hi = check.LOOP_BAND
    assert (round(lo, 2), round(hi, 2)) == (16.10, 19.67)
    from_low = _settle(lib, [('shutdown_loop_v', 0.0), ('shutdown_loop_v', 19.0),
                             ('shutdown_loop_v', hi), ('shutdown_loop_v', 19.8)])
    assert [out.loop_state for out, _ in from_low] == [LOOP_UPSTREAM_OPEN, LOOP_UPSTREAM_OPEN,
                                                       LOOP_CLOSED, LOOP_CLOSED]
    from_high = _settle(lib, [('shutdown_loop_v', 24.0), ('shutdown_loop_v', 16.8),
                              ('shutdown_loop_v', lo), ('shutdown_loop_v', 16.0)])
    assert [out.loop_state for out, _ in from_high] == [LOOP_CLOSED, LOOP_CLOSED,
                                                        LOOP_UPSTREAM_OPEN, LOOP_UPSTREAM_OPEN]


def test_file_ranges_match_the_abi(lib):
    """A value a file accepts never fails vcu_sil_advance mid-run, and one
    just outside would."""
    with Firmware(lib) as fw:
        for field, signal in (('accelerator_travel', 'accelerator'),
                              ('brake_front_psi', 'brake_psi'), ('brake_rear_psi', 'brake_psi'),
                              ('shutdown_loop_v', 'shutdown_loop_v')):
            lo, hi = SIGNALS[signal].lo, SIGNALS[signal].hi
            for value in (lo, hi):
                inputs = parked()
                setattr(inputs, field, value)
                fw.advance(inputs, 3)
            for value in (math.nextafter(lo, -math.inf), math.nextafter(hi, math.inf)):
                inputs = parked()
                setattr(inputs, field, value)
                with pytest.raises(FirmwareError, match='ERR_RANGE'):
                    fw.advance(inputs, 3)
        inputs = parked()
        inputs.hvc_state = max(HVC_STATES.values())
        fw.advance(inputs, 3)
        inputs.hvc_state += 1
        with pytest.raises(FirmwareError, match='ERR_RANGE'):
            fw.advance(inputs, 3)
        # Steering is clamped, not refused: the file's range stays short of
        # the sensor's saturation.
        for value in (SIGNALS['steering_deg'].lo, SIGNALS['steering_deg'].hi):
            inputs = parked()
            inputs.steering_angle_deg = value
            assert not fw.advance(inputs, 3).saturated & SATURATION_BITS['steering'], value


def _line(out: list[str], name: str) -> str:
    [line] = [line for line in out if line.startswith(f'ok {name}:')]
    return line


def test_check_command(capsys, tmp_path):
    assert check.main([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [line.split(':')[0] for line in out] == [f'ok {n}' for n in scenario_file.names()]
    assert _line(out, 'launch').startswith('ok launch: 7000 ms, 2334 steps, 7 blocks, '
                                           '0 warnings. Power up')
    assert _line(out, 'brake-over-throttle').endswith(
        '0 warnings, 1 allowed. ' + scenario_file.load('brake-over-throttle').description)
    launch = (scenario_file.SCENARIO_DIR / 'launch.toml').read_text()
    bad = tmp_path / 'bad.toml'
    bad.write_text(launch.replace('accelerator = 1.0', 'accelerator = 1.5'))
    assert check.main([str(bad)]) == 2
    captured = capsys.readouterr()
    line = line_of(bad.read_text(), 'accelerator = 1.5')
    assert captured.err.startswith(f'{bad}:{line}: error: at[5].accelerator: 1.5 is outside')
    assert '[out_of_range]' in captured.err
    assert captured.out == f'FAIL {bad}: 1 error, 0 warnings\n'
    warned = tmp_path / 'warned.toml'
    warned.write_text(launch.replace('brake_psi = 350.0', 'brake_psi = 300.0'))
    assert check.main([str(warned)]) == 0
    assert '[brake_near_threshold]' in capsys.readouterr().err
    assert check.main(['--strict', str(warned)]) == 2
    captured = capsys.readouterr()
    assert '[brake_near_threshold]' in captured.err
    assert captured.out == f'FAIL {warned}: 0 errors, 1 warning (--strict)\n'
    assert check.main(['--strict', 'brake-over-throttle']) == 0  # its warning is allowed
    capsys.readouterr()
    assert check.main(['lanch']) == 2
    captured = capsys.readouterr()
    assert 'did you mean launch?' in captured.err
    assert captured.out == 'FAIL lanch: 1 error, 0 warnings\n'
    with pytest.raises(SystemExit) as stop:
        check.main(['--brake-psi-full', '-1'])
    assert stop.value.code == 2


def test_errors_only(capsys, tmp_path):
    """run.sh's check before a run: errors and the per-file line, and no
    warnings, which the run itself reports."""
    launch = (scenario_file.SCENARIO_DIR / 'launch.toml').read_text()
    warned = tmp_path / 'warned.toml'
    warned.write_text(launch.replace('brake_psi = 350.0', 'brake_psi = 300.0'))
    assert check.main(['--errors-only', str(warned), 'launch']) == 0
    captured = capsys.readouterr()
    assert captured.err == ''
    description = scenario_file.load('launch').description
    assert captured.out == (f'ok warned: 7000 ms, 2334 steps, 7 blocks, warnings not checked. '
                            f'{description}\nok launch: 7000 ms, 2334 steps, 7 blocks, warnings '
                            f'not checked. {description}\n')
    bad = tmp_path / 'bad.toml'
    bad.write_text(launch.replace('accelerator = 1.0', 'accelerator = 1.5'))
    assert check.main(['--errors-only', str(bad), 'launch']) == 2
    captured = capsys.readouterr()
    assert captured.err.count('\n') == 1 and '[out_of_range]' in captured.err
    assert captured.out.splitlines()[0] == f'FAIL {bad}: 1 error, 0 warnings'
    for other in ('--strict', '--explain'):
        with pytest.raises(SystemExit) as stop:
            check.main(['--errors-only', other, 'launch'])
        assert stop.value.code == 2
        assert ('--errors-only reports no warnings, so it cannot go with --strict or --explain'
                in capsys.readouterr().err)


def test_a_bad_number_names_no_private_function(capsys):
    for text in ('abc', '', 'nan', '0', '-1', 'inf'):
        with pytest.raises(SystemExit) as stop:
            check.main(['--brake-psi-full', text, 'launch'])
        assert stop.value.code == 2
        err = capsys.readouterr().err
        assert f'argument --brake-psi-full: {text} is not a positive number' in err, err
        assert '_positive' not in err


def test_no_names_checks_the_files_as_paths(capsys, tmp_path, monkeypatch):
    """A badly named file gets the rename hint, and dot-files are skipped."""
    launch = (scenario_file.SCENARIO_DIR / 'launch.toml').read_text()
    (tmp_path / 'launch.toml').write_text(launch)
    (tmp_path / 'My_Test.toml').write_text(launch)
    (tmp_path / '._launch.toml').write_bytes(b'\x00\x05\x16\x07')  # macOS AppleDouble
    (tmp_path / '.hidden.toml').write_text('not toml')
    monkeypatch.setattr(scenario_file, 'SCENARIO_DIR', tmp_path)
    assert check.main([]) == 2
    captured = capsys.readouterr()
    path = tmp_path / 'My_Test.toml'
    assert captured.out.splitlines()[0] == f'FAIL {path}: 1 error, 0 warnings'
    assert captured.out.splitlines()[1].startswith('ok launch: 7000 ms')
    assert len(captured.out.splitlines()) == 2
    assert 'rename it to my-test.toml' in captured.err
    assert '[bad_name]' in captured.err and 'hidden' not in captured.err


EXPLAIN_LAUNCH = '''launch: 7000 ms, 2334 steps of 3 ms (t = 0 .. 6999 ms)
  t_ms  step  frame  block  mark       sets
     0     0      0  at[0]             accelerator 0.0, brake_psi 350.0, steering_deg 0.0, \
rtd_pressed false (the firmware sees it at 21 ms), hvc_state NOT_ENERGIZED, contactors_closed \
false, shutdown_loop_v 24.0
   200   201    210  at[1]  precharge  hvc_state PRECHARGING
   600   600    600  at[2]  energized  hvc_state ENERGIZED, contactors_closed true
  1000  1002         at[3]  rtd        rtd_pressed true (the firmware sees it at 1023 ms)
  1500  1500         at[4]  brake-off  brake_psi 0.0
  2000  2001         at[5]  pedal-on   accelerator 1.0
  6000  6000         at[6]  pedal-off  accelerator 0.0'''


def test_explain(capsys):
    assert check.main(['--explain', 'launch']) == 0
    assert capsys.readouterr().out.split('\n', 1)[1] == EXPLAIN_LAUNCH + '\n'
    # A ramp's step is the first sample at or after its t_ms, where it still
    # shows the held value (the README says so).
    sf = scenario_file.load('part-throttle')
    assert '  1500  1500         at[4]  brake-off  brake_psi to 0.0 over 150 ms' in (
        check.explain(sf).splitlines())
    assert (sf.timeline.value('brake_psi', 1500), sf.timeline.value('brake_psi', 1503)) == (
        350.0, 343.0)
    assert ('brake-off', 1500, 1500) in sf.marks
    rows = check.explain(scenario_file.load('shutdown-loop-open')).splitlines()
    assert ('  4000  4002         at[6]  loop-open  shutdown_loop_v 0.0 (the firmware sees it at '
            '4014 ms)') in rows


def _sets(text: str) -> dict[int, str]:
    """explain's sets column by block t_ms."""
    rows = check.explain(scenario_file.loads(text.encode(), 'lint-test')).splitlines()[2:]
    return {int(row.split()[0]): re.split(r'  at\[\d+\]  +(?:\S+  +)?', row, maxsplit=1)[1]
            for row in rows}


STEP_INTO_A_RAMP = drive(then=at(2000, 'shutdown_loop_v = 0.0')
                         + at(2001, 'shutdown_loop_v = { to = 24.0, ramp_ms = 30 }'))


def test_explain_notes_come_from_the_models():
    # A loop ramp is seen once the tap crosses 16.60 V (2016) and the 10 ms
    # debounce passes; 24 to 22 V crosses nothing; a dip is swallowed.
    loop = drive(then=at(2000, 'shutdown_loop_v = { to = 0.0, ramp_ms = 50 }')
                 + at(2200, 'shutdown_loop_v = 24.0') + at(2400, 'shutdown_loop_v = 22.0')
                 + at(2600, 'shutdown_loop_v = 0.0') + at(2606, 'shutdown_loop_v = 24.0'))
    sets = _sets(loop)
    assert sets[2000] == 'shutdown_loop_v to 0.0 over 50 ms (the firmware sees it at 2028 ms)'
    assert sets[2200] == 'shutdown_loop_v 24.0 (the firmware sees it at 2214 ms)'
    assert sets[2400] == 'shutdown_loop_v 22.0'
    assert sets[2600] == ('shutdown_loop_v 0.0 (the firmware never sees it: it changes back '
                          'before the 10 ms debounce passes)')
    assert sets[2606] == 'shutdown_loop_v 24.0 (the firmware already reads the loop closed)'
    # A step seen only on the start sample of the ramp after it: that sample
    # is the step's, and the ramp is the firmware's from the next one.
    sets = _sets(STEP_INTO_A_RAMP)
    assert sets[2000] == 'shutdown_loop_v 0.0 (the firmware sees it at 2013 ms)'
    assert sets[2001] == 'shutdown_loop_v to 24.0 over 30 ms (the firmware sees it at 2037 ms)'
    # The loop boots UNKNOWN, so a value at 0 gets no note. Both taps boot
    # reading low, so a value inside the band from 0 reads open.
    assert _sets(loop_open_from_0_until(2000))[0] == 'brake_psi 350.0, shutdown_loop_v 0.0'
    in_band = drive(('brake_psi = 350.0', 'brake_psi = 350.0\nshutdown_loop_v = 18.0'),
                    insert_before(1500, at(1200, 'shutdown_loop_v = 24.0')))
    assert _sets(in_band)[1200] == 'shutdown_loop_v 24.0 (the firmware sees it at 1212 ms)'
    # RTD: a release is debounced like a press; presses the switch never
    # sees say why.
    rtd = released(2000, 2030) + at(2100, 'rtd_pressed = false') + at(2112, 'rtd_pressed = true')
    sets = _sets(rtd)
    assert sets[2000] == 'rtd_pressed false (the firmware sees it at 2022 ms)'
    assert sets[2030] == 'rtd_pressed true (the firmware sees it at 2052 ms)'
    assert sets[2100] == ('rtd_pressed false (the firmware never sees it: it changes back before '
                          'the 20 ms debounce passes)')
    assert sets[2112] == 'rtd_pressed true (the firmware already reads true)'
    assert _sets(press_at(15))[15] == ('rtd_pressed true (the firmware never sees it: the switch '
                                       'starts as pressed and needs 20 ms released first)')
    assert _sets(released(2990, None))[2990] == ('rtd_pressed false (the firmware never sees '
                                                 'it: the run ends before the 20 ms debounce '
                                                 'passes)')


def test_readme_lists_every_key_and_warning():
    text = (SIL / 'README.md').read_text()
    for key in ('schema', 'description', 'duration_ms', 't_ms', 'mark', *SIGNALS,
                *scenario_file.SETUP_KEYS, *check.RULES):
        assert f'`{key}`' in text, key
    rows = re.findall(r'^\| `(\w+)` \|', text, re.M)
    assert [code for code in rows if code in check.RULES] == list(check.RULES)
