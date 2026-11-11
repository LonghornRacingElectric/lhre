"""Scenario files: the launch file is the stage 2 Launch, every file loads
clean, and every problem is reported with its code, place and line."""

import dataclasses
import hashlib
import itertools
import json
import re
import tomllib
from pathlib import Path

import pytest
from fakes import Launch, ToyCar

from vcusil import check, cosim, report, scenario_file
from vcusil.candecode import CanTable
from vcusil.firmware import (EVENT_MODES, HVC_ENERGIZED, HVC_NOT_ENERGIZED, HVC_PRECHARGING,
                             HVC_STATES, STATE_DRIVE, Firmware)
from vcusil.scenario import (CAR_SIGNALS, DEFAULTS, DRIVER_SIGNALS, Driver, RestOfCar, Segment,
                             Timeline)
from vcusil.scenario_file import (MAX_MARK, NAME, SCENARIO_DIR, ScenarioError, first_step_ms,
                                  last_step_ms)

LAUNCH = (SCENARIO_DIR / 'launch.toml').read_text()
PARAMS = Path(__file__).resolve().parents[3] / 'App' / 'config' / 'params.hpp'
STATE_FAULT = 3
# faults.hpp FaultBit, and the kTorqueCutFaults that exit DRIVE.
BRAKE_OVER_THROTTLE = 1 << 7
SHUTDOWN_OPEN = 1 << 8
CONTACTORS_OPEN = 1 << 17
TORQUE_CUT_FAULTS = sum(1 << bit for bit in (0, 3, 4, 6, 8, 9, 10, 11, 12, 13, 18, 20))


def edited(*edits) -> str:
    text = LAUNCH
    for edit in edits:
        if callable(edit):
            text = edit(text)
            continue
        old, new = edit
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    return text


def problems_of(text: str, name='launch') -> list:
    with pytest.raises(ScenarioError) as caught:
        scenario_file.loads(text.encode('utf-8', 'surrogateescape'), name)
    return caught.value.problems


def line_of(text: str, anchor) -> int | None:
    """The line holding anchor (a unique substring), plus an offset. Lines
    end at \\n only, as in TOML: splitlines() also breaks at U+2028."""
    if anchor is None:
        return None
    anchor, offset = anchor if isinstance(anchor, tuple) else (anchor, 0)
    found = [n for n, line in enumerate(text.split('\n'), 1) if anchor in line]
    assert len(found) == 1, (anchor, found)
    return found[0] + offset


def drop_blocks(text: str) -> str:
    return text[:text.index('# The start')]


AFTER_SETUP = ('soc_start = 0.9            # at 1.0 the car model\'s pack accepts no regen\n',)


def top(line: str):
    return ('duration_ms = 7000\n', f'duration_ms = 7000\n{line}\n')


def allow(lines: str):
    return ('# The start', f'[allow]\n{lines}\n\n# The start')


def append(lines: str):
    return lambda text: text + lines


def block_before(t_ms: int, lines: str):
    """A new [[at]] block (lines: its keys, t_ms first), just before launch's
    block at t_ms."""
    header = f'[[at]]\nt_ms = {t_ms}\n'
    return (header, f'[[at]]\n{lines}\n\n{header}')


SETUP = ''.join(LAUNCH.splitlines(True)[10:13])  # [setup] and its two keys
assert SETUP.startswith('[setup]\n') and SETUP.count('\n') == 3
RTD_BLOCK = 't_ms = 1000\nmark = "rtd"\nrtd_pressed = true\n'
LAST_PEDAL = 't_ms = 6000\nmark = "pedal-off"\naccelerator = 0.0'


# (id, edits, code, where, line anchor, part of the message, problems in all)
CASES = [
    # toml_syntax (the hints: SYNTAX)
    ('duplicate-key', [('mark = "rtd"\n', 'mark = "rtd"\nmark = "rtd2"\n')], 'toml_syntax', '',
     'mark = "rtd2"', 'each moment needs its own [[at]] block', 1),
    ('setup-twice', [append('\n[setup]  # again\nsoc_start = 0.5\n')], 'toml_syntax', '',
     '[setup]  # again', 'this table is written twice; merge them', 1),
    ('not-utf-8', [('Power up, ready', 'Power\udcff up, ready')], 'toml_syntax', '',
     'description =', 'is not UTF-8 text', 1),
    # wrong_schema
    ('schema-v2', [('scenario.v1"', 'scenario.v2"')], 'wrong_schema', 'schema', 'schema =',
     'written for a different harness version', 1),
    ('schema-other', [('"lhre.vcu-sil-scenario.v1"', '"vcu-sil.v1"')], 'wrong_schema', 'schema',
     'schema =', 'is not "lhre.vcu-sil-scenario.v1"', 1),
    # missing_key
    ('no-schema', [('schema = "lhre.vcu-sil-scenario.v1"\n', '')], 'missing_key', 'schema', None,
     'is missing', 1),
    ('no-description', [(LAUNCH.splitlines(True)[6], '')], 'missing_key', 'description', None,
     'is missing', 1),
    ('no-duration', [('duration_ms = 7000\n', '')], 'missing_key', 'duration_ms', None,
     'is missing', 1),
    ('no-blocks', [drop_blocks], 'missing_key', 'at', None, 'at least one [[at]] block', 1),
    ('block-without-time', [('t_ms = 1000\n', '')], 'missing_key', 'at[3].t_ms',
     ('mark = "rtd"', -1), 'every block needs its time', 1),
    # unknown_key
    ('near-miss-top', [('duration_ms = 7000', 'durations_ms = 7000')], 'unknown_key',
     'durations_ms', 'durations_ms', 'did you mean duration_ms?', 2),
    ('near-miss-block', [('brake_psi = 0.0', 'brake = 0.0')], 'unknown_key', 'at[4].brake',
     'brake = 0.0', 'is not an lhre.vcu-sil-scenario.v1 key; did you mean brake_psi?', 1),
    ('near-miss-setup', [('event_mode = "UNASSIGNED"', 'event = "UNASSIGNED"')], 'unknown_key',
     'setup.event', 'event =', 'is not a [setup] key; did you mean event_mode?', 1),
    ('near-miss-allow', [allow('brake_over_throtle = "on purpose"')], 'unknown_key',
     'allow.brake_over_throtle', 'brake_over_throtle', 'did you mean brake_over_throttle?', 1),
    ('allow-unused-allow', [allow('unused_allow = "quiet"')], 'unknown_key', 'allow.unused_allow',
     'unused_allow =', 'cannot be allowed', 1),
    ('key-after-table', [('duration_ms = 7000\n', ''), (*AFTER_SETUP,
                                                        AFTER_SETUP[0] + 'duration_ms = 7000\n')],
     'unknown_key', 'setup.duration_ms', 'duration_ms = 7000',
     'top-level keys go before the first [table], so TOML put this one in [setup]', 2),
    ('planned-brake', [('brake_psi = 0.0', 'brake_front_psi = 0.0')], 'unknown_key',
     'at[4].brake_front_psi', 'brake_front_psi', 'planned: separate front and rear brake', 1),
    ('planned-after', [('t_ms = 1500\n', 't_ms = 1500\nafter = "state:DRIVE"\n')], 'unknown_key',
     'at[4].after', 'after =', 'planned: times relative to a firmware state, stage 4', 1),
    ('planned-expect', [top('expect = 1')], 'unknown_key', 'expect', 'expect = 1', 'stage 4', 1),
    ('planned-can', [top('can = 1')], 'unknown_key', 'can', 'can = 1', 'stage 6', 1),
    # An inline table's keys are on its one line.
    ('inline-setup', [(SETUP, 'setup = { event = "SKIDPAD" }\n')], 'unknown_key', 'setup.event',
     'setup = {', 'is not a [setup] key; did you mean event_mode?', 1),
    ('inline-allow', [top('allow = { brake_hihg = "on purpose" }')], 'unknown_key',
     'allow.brake_hihg', 'allow = {', 'is not a warning code; did you mean brake_high?', 1),
    # wrong_type
    ('at-number', [drop_blocks, top('at = 5')], 'wrong_type', 'at', 'at = 5',
     'write [[at]], with two brackets', 1),
    ('at-table', [lambda text: drop_blocks(text) + '[at]\nt_ms = 0\n'],
     'wrong_type', 'at', '[at]', 'write [[at]], with two brackets, once per moment', 1),
    ('duration-float', [('duration_ms = 7000', 'duration_ms = 7000.5')], 'wrong_type',
     'duration_ms', 'duration_ms =', 'is not whole milliseconds', 1),
    ('duration-bool', [('duration_ms = 7000', 'duration_ms = true')], 'wrong_type',
     'duration_ms', 'duration_ms =', 'true is not whole milliseconds', 1),
    ('duration-string', [('duration_ms = 7000', 'duration_ms = "7000"')], 'wrong_type',
     'duration_ms', 'duration_ms =', '"7000" is not whole milliseconds', 1),
    ('time-float', [('t_ms = 200\n', 't_ms = 200.0\n')], 'wrong_type', 'at[1].t_ms',
     't_ms = 200.0', '200.0 is not whole milliseconds', 1),
    ('time-exponent', [('t_ms = 1000\n', 't_ms = 1e3\n')], 'wrong_type', 'at[3].t_ms',
     't_ms = 1e3', '1000.0 is not whole milliseconds', 1),
    ('time-bool', [('t_ms = 1000\n', 't_ms = true\n')], 'wrong_type', 'at[3].t_ms',
     't_ms = true', 'true is not whole milliseconds', 1),
    ('time-of-day', [('t_ms = 1000\n', 't_ms = 07:00:00\n')], 'wrong_type', 'at[3].t_ms',
     't_ms = 07:00:00', '07:00:00 is not whole milliseconds', 1),
    ('pedal-bool', [('accelerator = 1.0', 'accelerator = true')], 'wrong_type',
     'at[5].accelerator', 'accelerator = true', 'true is not a number', 1),
    ('pedal-string', [('accelerator = 1.0', 'accelerator = "full"')], 'wrong_type',
     'at[5].accelerator', 'accelerator = "full"', '"full" is not a number', 1),
    # Nesting tomllib can recurse through (too deep: SYNTAX) is a value.
    ('pedal-nested-array', [('accelerator = 1.0', 'accelerator = [[0.5]]')], 'wrong_type',
     'at[5].accelerator', 'accelerator = [[0.5]]', '[[0.5]] is not a number', 1),
    ('switch-number', [('rtd_pressed = true', 'rtd_pressed = 1')], 'wrong_type',
     'at[3].rtd_pressed', 'rtd_pressed = 1', '1 is not true or false; write true or false', 1),
    ('switch-string', [('rtd_pressed = true', 'rtd_pressed = "yes"')], 'wrong_type',
     'at[3].rtd_pressed', 'rtd_pressed = "yes"', 'write true or false', 1),
    ('hvc-number', [('hvc_state = "ENERGIZED"', 'hvc_state = 2')], 'wrong_type',
     'at[2].hvc_state', 'hvc_state = 2', 'write the name: hvc_state = "ENERGIZED"', 1),
    ('mode-number', [('event_mode = "UNASSIGNED"', 'event_mode = 4')], 'wrong_type',
     'setup.event_mode', 'event_mode = 4', 'write the name: event_mode = "ENDURANCE"', 1),
    ('description-number', [(LAUNCH.splitlines(True)[6], 'description = 5\n')], 'wrong_type',
     'description', 'description = 5', 'is not a string', 1),
    ('soc-string', [('soc_start = 0.9', 'soc_start = "high"')], 'wrong_type', 'setup.soc_start',
     'soc_start = "high"', 'is not a number', 1),
    ('reason-number', [allow('brake_high = 1')], 'wrong_type', 'allow.brake_high',
     'brake_high = 1', '1 is not a string', 1),
    # out_of_range
    ('duration-zero', [('duration_ms = 7000', 'duration_ms = 0')], 'out_of_range', 'duration_ms',
     'duration_ms =', 'outside 1..600000 ms', 1),
    ('duration-negative', [('duration_ms = 7000', 'duration_ms = -1')], 'out_of_range',
     'duration_ms', 'duration_ms =', '-1 is outside 1..600000 ms', 1),
    ('duration-long', [('duration_ms = 7000', 'duration_ms = 600001')], 'out_of_range',
     'duration_ms', 'duration_ms =', '600001 is outside', 1),
    # The most digits int() reads (one more: SYNTAX) is a number like any other.
    ('duration-4300-digits', [('duration_ms = 7000', 'duration_ms = 1' + '0' * 4299)],
     'out_of_range', 'duration_ms', 'duration_ms =', 'is outside 1..600000 ms', 1),
    ('time-negative', [('t_ms = 0\n', 't_ms = -1\n')], 'out_of_range', 'at[0].t_ms',
     't_ms = -1', 'time starts at 0', 1),
    ('time-at-end', [('t_ms = 6000\n', 't_ms = 7000\n')], 'out_of_range', 'at[6].t_ms',
     't_ms = 7000', 'is not before duration_ms (7000); the last sample is at 6999', 1),
    ('pedal-over', [('accelerator = 1.0', 'accelerator = 1.5')], 'out_of_range',
     'at[5].accelerator', 'accelerator = 1.5',
     '1.5 is outside 0..1 (a fraction of pedal travel: 0.5 is half pedal)', 1),
    ('pedal-under', [('accelerator = 1.0', 'accelerator = -0.1')], 'out_of_range',
     'at[5].accelerator', 'accelerator = -0.1', '-0.1 is outside 0..1', 1),
    ('pedal-nan', [('accelerator = 1.0', 'accelerator = nan')], 'out_of_range',
     'at[5].accelerator', 'accelerator = nan', 'nan is outside 0..1', 1),
    ('pedal-inf', [('accelerator = 1.0', 'accelerator = inf')], 'out_of_range',
     'at[5].accelerator', 'accelerator = inf', 'inf is outside 0..1', 1),
    ('brake-over', [('brake_psi = 350.0', 'brake_psi = 3000.1')], 'out_of_range',
     'at[0].brake_psi', 'brake_psi = 3000.1', '3000.1 is outside 0..3000', 1),
    ('loop-over', [('shutdown_loop_v = 24.0', 'shutdown_loop_v = 27.5')], 'out_of_range',
     'at[0].shutdown_loop_v', 'shutdown_loop_v = 27.5', '27.5 is outside 0..27', 1),
    ('steering-right', [('steering_deg = 0.0', 'steering_deg = 181')], 'out_of_range',
     'at[0].steering_deg', 'steering_deg = 181', '181 is outside -180..180', 1),
    ('steering-left', [('steering_deg = 0.0', 'steering_deg = -181')], 'out_of_range',
     'at[0].steering_deg', 'steering_deg = -181', '-181 is outside -180..180', 1),
    ('soc-over', [('soc_start = 0.9', 'soc_start = 1.2')], 'out_of_range', 'setup.soc_start',
     'soc_start = 1.2', '1.2 is outside 0..1', 1),
    ('soc-under', [('soc_start = 0.9', 'soc_start = -0.1')], 'out_of_range', 'setup.soc_start',
     'soc_start = -0.1', '-0.1 is outside 0..1', 1),
    # Lines end at \n alone, as tomllib counts them, whatever else a comment
    # or a quoted header holds.
    ('comment-with-u2028', [('# The start: HV off', '# The start:\u2028 HV off'),
                            ('accelerator = 1.0', 'accelerator = 1.5')], 'out_of_range',
     'at[5].accelerator', 'accelerator = 1.5', '1.5 is outside 0..1', 1),
    ('quoted-block-header', [('[[at]]\nt_ms = 200\n',
                              '[["at"]]\nt_ms = 200\naccelerator = 1.5\n')],
     'out_of_range', 'at[1].accelerator', 'accelerator = 1.5', '1.5 is outside 0..1', 1),
    ('quoted-table-header', [('[setup]\n', '[ "setup" ]\n'),
                             ('soc_start = 0.9', 'soc_start = 1.2')],
     'out_of_range', 'setup.soc_start', 'soc_start = 1.2', '1.2 is outside 0..1', 1),
    ('ramp-to-over', [('accelerator = 1.0', 'accelerator = { to = 1.5, ramp_ms = 300 }')],
     'out_of_range', 'at[5].accelerator.to', 'to = 1.5', '1.5 is outside 0..1', 1),
    # bad_value
    ('hvc-misspelt', [('hvc_state = "ENERGIZED"', 'hvc_state = "ENERGISED"')], 'bad_value',
     'at[2].hvc_state', '"ENERGISED"', 'did you mean ENERGIZED?', 1),
    ('hvc-lower-case', [('hvc_state = "ENERGIZED"', 'hvc_state = "energized"')], 'bad_value',
     'at[2].hvc_state', 'hvc_state = "energized"', 'did you mean ENERGIZED? names are upper case',
     1),
    ('mode-mixed-case', [('event_mode = "UNASSIGNED"', 'event_mode = "Skidpad"')], 'bad_value',
     'setup.event_mode', '"Skidpad"', 'did you mean SKIDPAD?', 1),
    ('description-empty', [(LAUNCH.splitlines(True)[6], 'description = ""\n')], 'bad_value',
     'description', 'description = ""', 'is empty', 1),
    ('description-lines', [(LAUNCH.splitlines(True)[6], 'description = "one\\ntwo"\n')],
     'bad_value', 'description', 'description = "one', 'is more than one line', 1),
    ('description-line-separator', [(LAUNCH.splitlines(True)[6],
                                     'description = "one\\u2028two"\n')],
     'bad_value', 'description', 'description = "one', 'is more than one line', 1),
    ('description-long', [(LAUNCH.splitlines(True)[6], f'description = "{"x" * 121}"\n')],
     'bad_value', 'description', 'description = "x', 'is 121 characters; the limit is 120', 1),
    ('mark-not-a-slug', [('mark = "rtd"', 'mark = "Loop Open"')], 'bad_value', 'at[3].mark',
     '"Loop Open"', 'such as "loop-open"', 1),
    # The suggestion is itself a mark: never over 40 characters, never
    # ending in a dash, and never the file-name fallback.
    ('mark-41-characters', [('mark = "rtd"', f'mark = "{"a" * 41}"')], 'bad_value', 'at[3].mark',
     'mark = "aaa', f'at most 40 characters of lowercase words joined by dashes, such as '
                    f'"{"a" * 40}"', 1),
    ('mark-cut-on-a-dash', [('mark = "rtd"', f'mark = "{"a-" * 20}b"')], 'bad_value', 'at[3].mark',
     'mark = "a-a', f'such as "{"a-" * 19}a"', 1),
    ('mark-no-letters', [('mark = "rtd"', 'mark = "!!!"')], 'bad_value', 'at[3].mark',
     'mark = "!!!"', 'such as "loop-open"', 1),
    ('mark-empty', [('mark = "rtd"', 'mark = ""')], 'bad_value', 'at[3].mark', 'mark = ""',
     'such as "loop-open"', 1),
    ('mark-twice', [('mark = "brake-off"', 'mark = "rtd"')], 'bad_value', 'at[4].mark',
     ('t_ms = 1500', 1), 'rtd is already the mark of at[3]', 1),
    ('reason-empty', [allow('brake_high = ""')], 'bad_value', 'allow.brake_high',
     'brake_high = ""', 'is empty', 1),
    # time_order
    ('time-equal', [('t_ms = 1500\n', 't_ms = 1000\n')], 'time_order', 'at[4].t_ms',
     ('mark = "brake-off"', -1), 'two blocks at 1000 ms: merge them', 1),
    ('time-backward', [('t_ms = 1500\n', 't_ms = 900\n')], 'time_order', 'at[4].t_ms',
     't_ms = 900', '900 comes after 1000 (at[3]); keep the blocks in time order', 1),
    # sets_nothing
    ('empty-block', [append('\n[[at]]\nt_ms = 6500\n')], 'sets_nothing', 'at[7]',
     ('t_ms = 6500', -1), 'sets nothing', 1),
    # bad_ramp
    ('ramp-switch', [('rtd_pressed = true', 'rtd_pressed = { to = 1.0, ramp_ms = 10 }')],
     'bad_ramp', 'at[3].rtd_pressed', 'rtd_pressed = {',
     'is a switch and cannot ramp; write true or false', 1),
    ('ramp-state', [('hvc_state = "ENERGIZED"', 'hvc_state = { to = 2.0, ramp_ms = 10 }')],
     'bad_ramp', 'at[2].hvc_state', 'hvc_state = {', 'is a state and cannot ramp', 1),
    ('ramp-no-to', [('accelerator = 1.0', 'accelerator = { ramp_ms = 300 }')], 'bad_ramp',
     'at[5].accelerator', 'accelerator = {', 'it has no to', 1),
    ('ramp-extra-key', [('accelerator = 1.0', 'accelerator = { to = 1.0, ramp_ms = 300, '
                                              'over = 1 }')],
     'bad_ramp', 'at[5].accelerator', 'accelerator = {', 'over is not a ramp key', 1),
    ('ramp-zero-ms', [('accelerator = 1.0', 'accelerator = { to = 1.0, ramp_ms = 0 }')],
     'bad_ramp', 'at[5].accelerator.ramp_ms', 'accelerator = {', 'at least 1 ms', 1),
    # A fractional ramp_ms is the wrong type, like a fractional t_ms.
    ('ramp-fraction-ms', [('accelerator = 1.0', 'accelerator = { to = 1.0, ramp_ms = 1.5 }')],
     'wrong_type', 'at[5].accelerator.ramp_ms', 'accelerator = {', 'not whole milliseconds', 1),
    # ramp_overlap, ramp_past_end, unsampled (pass 2)
    ('ramp-overlap', [('accelerator = 1.0', 'accelerator = { to = 1.0, ramp_ms = 300 }'),
                      ('[[at]]\nt_ms = 6000\n', '[[at]]\nt_ms = 2100\naccelerator = 0.5\n\n'
                                                '[[at]]\nt_ms = 6000\n')],
     'ramp_overlap', 'at[6].accelerator', 'accelerator = 0.5',
     'changes at 2100 ms, inside the ramp of at[5], which runs until 2300 ms', 1),
    ('ramp-overlap-by-1-ms', [('accelerator = 1.0', 'accelerator = { to = 1.0, ramp_ms = 100 }'),
                              block_before(6000, 't_ms = 2099\naccelerator = 0.5')],
     'ramp_overlap', 'at[6].accelerator', 'accelerator = 0.5',
     'changes at 2099 ms, inside the ramp of at[5], which runs until 2100 ms; start this change '
     'at 2100 or later', 1),
    ('ramp-past-end', [(LAST_PEDAL, 't_ms = 6900\nmark = "pedal-off"\n'
                                    'accelerator = { to = 0.0, ramp_ms = 300 }')],
     'ramp_past_end', 'at[6].accelerator', 'ramp_ms = 300 }',
     'ramps until 7200 ms, past duration_ms (7000)', 1),
    ('ramp-past-end-by-1-ms', [(LAST_PEDAL, f'{LAST_PEDAL[:-3]}{{ to = 0.0, ramp_ms = 1001 }}')],
     'ramp_past_end', 'at[6].accelerator', 'ramp_ms = 1001 }',
     'ramps until 7001 ms, past duration_ms (7000)', 1),
    # unsampled: a step is seen from T and a ramp from the first sample after
    # T, until the next change, or through it when that change is a ramp.
    ('pulse-between-samples', [block_before(1500, 't_ms = 1001\nrtd_pressed = false')],
     'unsampled', 'at[3].rtd_pressed', 'rtd_pressed = true',
     'is never sampled: the host samples every 3 ms, and the first sample from 1000 ms is at '
     '1002, after the next change at 1001 (at[4]); hold the value through 1002 ms', 1),
    ('pulse-ends-on-the-sample', [block_before(1500, 't_ms = 1002\nrtd_pressed = false')],
     'unsampled', 'at[3].rtd_pressed', 'rtd_pressed = true',
     'is never sampled: the host samples every 3 ms, and the first sample from 1000 ms is at '
     '1002, which already sees the next change (at[4]); hold the value through 1002 ms', 1),
    ('step-before-a-ramp', [block_before(200, 't_ms = 1\nbrake_psi = 400.0\n\n[[at]]\nt_ms = 2\n'
                                              'brake_psi = { to = 350.0, ramp_ms = 30 }')],
     'unsampled', 'at[1].brake_psi', 'brake_psi = 400.0',
     'is never sampled: the host samples every 3 ms, and the first sample from 1 ms is at 3, '
     'after the next change at 2 (at[2]); start that ramp at 3 ms or later: its first sample '
     'still shows the value before it', 1),
    ('ramp-replaced-before-a-sample', [block_before(1000, 't_ms = 999\n'
                                                          'brake_psi = { to = 0.0, ramp_ms = 1 }'),
                                       (RTD_BLOCK, RTD_BLOCK + 'brake_psi = 350.0\n')],
     'unsampled', 'at[3].brake_psi', 'ramp_ms = 1 }',
     'is never sampled: the host samples every 3 ms, and a ramp starts at the held value, so its '
     'first visible sample is the first one after 999 ms, at 1002, after the next change at '
     '1000 (at[4]); move the next change after 1002 ms', 1),
    ('ramp-replaced-on-its-start-sample', [
        ('brake_psi = 0.0\n', 'brake_psi = 0.0\naccelerator = { to = 1.0, ramp_ms = 2 }\n'),
        block_before(2000, 't_ms = 1502\naccelerator = 0.0')],
     'unsampled', 'at[4].accelerator', 'ramp_ms = 2 }',
     'is never sampled: the host samples every 3 ms, and a ramp starts at the held value, so its '
     'first visible sample is the first one after 1500 ms, at 1503, after the next change at '
     '1502 (at[5]); move the next change after 1503 ms', 1),
    ('after-last-sample', [('duration_ms = 7000', 'duration_ms = 7001'),
                           append('\n[[at]]\nt_ms = 7000\nbrake_psi = 0.0\n')],
     'unsampled', 'at[7].brake_psi', ('t_ms = 7000', 1),
     'is never sampled: the host samples every 3 ms and the last sample is at 6999 ms; move it '
     'earlier, or make duration_ms at least 7003', 1),
    ('ramp-on-the-last-sample', [append('\n[[at]]\nt_ms = 6999\n'
                                        'brake_psi = { to = 100.0, ramp_ms = 1 }\n')],
     'unsampled', 'at[7].brake_psi', 'ramp_ms = 1 }',
     'is never sampled: the host samples every 3 ms and the last sample is at 6999 ms, but a ramp '
     'starts at the held value, so its first visible sample is the first one after 6999 ms, at '
     '7002; move it earlier, or make duration_ms at least 7003', 1),
    ('mark-after-last-sample', [('duration_ms = 7000', 'duration_ms = 7001'),
                                append('\n[[at]]\nt_ms = 7000\nmark = "late"\n')],
     'unsampled', 'at[7].mark', 'mark = "late"',
     'is never shown: the host samples every 3 ms and the last sample is at 6999 ms; move it '
     'earlier, or make duration_ms at least 7003', 1),
]

def value(sf, signal: str, t_ms: int):
    return sf.timeline.value(signal, t_ms)


# The other side of a boundary above, or a file the format allows that no
# shipped file shows: each loads, and the timeline does what the check says.
LOADS = [
    ('pulse-held-for-a-sample', [block_before(1500, 't_ms = 1003\nrtd_pressed = false')],
     lambda sf: value(sf, 'rtd_pressed', 1002) is True and value(sf, 'rtd_pressed', 1005) is False),
    ('step-held-into-a-ramp', [block_before(200, 't_ms = 1\nbrake_psi = 400.0\n\n[[at]]\nt_ms = 3\n'
                                                 'brake_psi = { to = 350.0, ramp_ms = 30 }')],
     lambda sf: value(sf, 'brake_psi', 3) == 400.0),
    ('ramp-seen-before-the-next-change', [
        block_before(1000, 't_ms = 998\nbrake_psi = { to = 0.0, ramp_ms = 1 }'),
        (RTD_BLOCK, RTD_BLOCK + 'brake_psi = 350.0\n')],
     lambda sf: value(sf, 'brake_psi', 999) == 0.0 and value(sf, 'brake_psi', 1002) == 350.0),
    ('ramp-just-before-the-last-sample', [append('\n[[at]]\nt_ms = 6998\n'
                                                 'brake_psi = { to = 100.0, ramp_ms = 1 }\n')],
     lambda sf: value(sf, 'brake_psi', 6999) == 100.0),
    ('back-to-back-ramps', [('accelerator = 1.0', 'accelerator = { to = 0.5, ramp_ms = 100 }'),
                            block_before(6000, 't_ms = 2100\naccelerator = { to = 1.0, '
                                               'ramp_ms = 100 }')],
     lambda sf: value(sf, 'accelerator', 2100) == 0.5 and value(sf, 'accelerator', 2200) == 1.0),
    ('ramp-ends-at-the-end', [(LAST_PEDAL, f'{LAST_PEDAL[:-3]}{{ to = 0.0, ramp_ms = 1000 }}')],
     lambda sf: 0.0 < value(sf, 'accelerator', 6999) < 0.01 and sf.duration_ms == 7000),
    ('longest-run', [('duration_ms = 7000', 'duration_ms = 600000')],
     lambda sf: sf.duration_ms == 600_000),
    ('mark-40-characters', [('mark = "rtd"', f'mark = "{"a" * 40}"')],
     lambda sf: sf.marks[2] == ('a' * 40, 1000, 1002)),
    ('byte-order-mark-inside-a-comment', [('# The start: HV off', '# The start:\ufeff HV off')],
     lambda sf: sf.parameters() == scenario_file.load('launch').parameters()),
]


@pytest.mark.parametrize('edits, holds', [c[1:] for c in LOADS], ids=[c[0] for c in LOADS])
def test_loads(edits, holds):
    sf = scenario_file.loads(edited(*edits).encode(), 'launch')
    assert holds(sf)


# (id, edits, line anchor, the whole message): toml_syntax, with the hint the
# key decides, so that following it never leads to a second error.
SYNTAX = [
    ('bool-word', [('rtd_pressed = true', 'rtd_pressed = yes')], 'rtd_pressed = yes',
     'TOML: Invalid value at column 15; write true or false'),
    ('bool-capital', [('rtd_pressed = true', 'rtd_pressed = True')], 'rtd_pressed = True',
     'TOML: Invalid value at column 15; booleans are lowercase: true or false'),
    ('number-word', [('accelerator = 1.0', 'accelerator = full')], 'accelerator = full',
     'TOML: Invalid value at column 15; a number is written bare, such as 0.5'),
    ('number-capital-true', [('accelerator = 1.0', 'accelerator = True')], 'accelerator = True',
     'TOML: Invalid value at column 15; a number is written bare, such as 0.5'),
    ('number-nan', [('accelerator = 1.0', 'accelerator = NaN')], 'accelerator = NaN',
     'TOML: Invalid value at column 15; nan and inf are lowercase in TOML, and out of range '
     'here anyway'),
    ('number-signed-inf', [('accelerator = 1.0', 'accelerator = -Inf')], 'accelerator = -Inf',
     'TOML: Invalid value at column 15; nan and inf are lowercase in TOML, and out of range '
     'here anyway'),
    ('setup-number-word', [('soc_start = 0.9', 'soc_start = high')], 'soc_start = high',
     'TOML: Invalid value at column 13; a number is written bare, such as 0.5'),
    ('ramp-to-word', [('accelerator = 1.0', 'accelerator = { to = full, ramp_ms = 300 }')],
     'to = full', 'TOML: Invalid value at column 22; a number is written bare, such as 0.5'),
    ('ramp-ms-word', [('accelerator = 1.0', 'accelerator = { to = 0.4, ramp_ms = abc }')],
     'ramp_ms = abc', 'TOML: Invalid value at column 37; milliseconds are a whole number '
                      'written bare, such as 1500'),
    ('time-word', [('t_ms = 1000\n', 't_ms = abc\n')], 't_ms = abc',
     'TOML: Invalid value at column 8; milliseconds are a whole number written bare, such as '
     '1500'),
    ('name-word', [('hvc_state = "ENERGIZED"', 'hvc_state = ENERGIZED')], 'hvc_state = ENERGIZED',
     'TOML: Invalid value at column 13; a name is a string in quotes: hvc_state = "ENERGIZED"'),
    ('name-nan', [('hvc_state = "ENERGIZED"', 'hvc_state = NaN')], 'hvc_state = NaN',
     'TOML: Invalid value at column 13; a name is a string in quotes: hvc_state = "ENERGIZED"'),
    ('mode-word', [('event_mode = "UNASSIGNED"', 'event_mode = SKIDPAD')], 'event_mode = SKIDPAD',
     'TOML: Invalid value at column 14; a name is a string in quotes: hvc_state = "ENERGIZED"'),
    ('unknown-key-word', [top('colour = red')], 'colour = red',
     'TOML: Invalid value at column 10; a name is a string in quotes: hvc_state = "ENERGIZED"'),
    ('no-leading-zero', [('accelerator = 1.0', 'accelerator = .5')], 'accelerator = .5',
     'TOML: Invalid value at column 15; write a leading zero: 0.5'),
    ('signed-no-leading-zero', [('accelerator = 1.0', 'accelerator = -.5')], 'accelerator = -.5',
     'TOML: Invalid value at column 15; write a leading zero: 0.5'),
    ('multi-line-ramp', [('brake_psi = 0.0', 'brake_psi = { to = 0.0,\n  ramp_ms = 150 }')],
     'brake_psi = { to = 0.0,', 'TOML: Invalid initial character for a key part at column 24; '
                                'an inline table { ... } must fit on one line, with no comma '
                                'before }'),
    ('ramp-trailing-comma', [('brake_psi = 0.0', 'brake_psi = { to = 0.0, ramp_ms = 150, }')],
     'ramp_ms = 150, }', 'TOML: Invalid initial character for a key part at column 40; an '
                         'inline table { ... } must fit on one line, with no comma before }'),
    ('ramp-unclosed', [('accelerator = 1.0', 'accelerator = { to = 0.4, ramp_ms = 300')],
     'ramp_ms = 300', 'TOML: Unclosed inline table at column 40; an inline table { ... } must '
                      'fit on one line, with no comma before }'),
    ('ramp-unclosed-before-a-comment', [('accelerator = 1.0',
                                         'accelerator = { to = 0.4, ramp_ms = 300  # 0.3 s')],
     'ramp_ms = 300', 'TOML: Unclosed inline table at column 42; an inline table { ... } must '
                      'fit on one line, with no comma before }'),
    ('ramp-missing-comma', [('accelerator = 1.0', 'accelerator = { to = 0.4 ramp_ms = 300 }')],
     'ramp_ms = 300', 'TOML: Unclosed inline table at column 26; separate the keys with a '
                      'comma: { to = 0.4, ramp_ms = 300 }'),
    ('ramp-semicolon', [('accelerator = 1.0', 'accelerator = { to = 0.4; ramp_ms = 300 }')],
     'ramp_ms = 300', 'TOML: Unclosed inline table at column 25; separate the keys with a '
                      'comma: { to = 0.4, ramp_ms = 300 }'),
    ('ramp-units', [('accelerator = 1.0', 'accelerator = { to = 0.4, ramp_ms = 300ms }')],
     'ramp_ms = 300', 'TOML: Unclosed inline table at column 40; units go in a # comment; '
                      'separate the keys with a comma: { to = 0.4, ramp_ms = 300 }'),
    # Not straight after the digits, so it could as well be the next key.
    ('ramp-spaced-units', [('accelerator = 1.0', 'accelerator = { to = 0.4, ramp_ms = 300 ms }')],
     'ramp_ms = 300', 'TOML: Unclosed inline table at column 41; separate the keys with a '
                      'comma: { to = 0.4, ramp_ms = 300 }'),
    # The hint reads the row tomllib names, which a U+0085 earlier must not move.
    ('comment-with-nel', [('# The start: HV off', '# The start:\x85 HV off'),
                          ('rtd_pressed = true', 'rtd_pressed = True')], 'rtd_pressed = True',
     'TOML: Invalid value at column 15; booleans are lowercase: true or false'),
    ('byte-order-mark', [lambda text: '\ufeff' + text], '# The reference drive',
     'the file starts with a byte-order mark (some Windows editors add one); save it as UTF-8 '
     'without a BOM'),
    # tomllib raises these two outside TOMLDecodeError; neither has a place.
    ('integer-of-4301-digits', [('duration_ms = 7000', 'duration_ms = 1' + '0' * 4300)], None,
     'TOML: Exceeds the limit (4300 digits) for integer string conversion: value has 4301 '
     'digits'),
    ('nested-too-deeply', [('accelerator = 1.0', 'accelerator = ' + '[' * 1200)], None,
     'TOML: arrays or inline tables are nested too deeply'),
]


@pytest.mark.parametrize('edits, anchor, message', [c[1:] for c in SYNTAX],
                         ids=[c[0] for c in SYNTAX])
def test_syntax_hints(edits, anchor, message):
    text = edited(*edits)
    [p] = problems_of(text)
    assert (p.level, p.code, p.where, p.line) == ('error', 'toml_syntax', '', line_of(text, anchor))
    assert p.message == message


@pytest.mark.parametrize('edits, code, where, anchor, fragment, count',
                         [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_problems(edits, code, where, anchor, fragment, count):
    text = edited(*edits)
    problems = problems_of(text)
    assert len(problems) == count, problems
    assert all(p.level == 'error' for p in problems)
    found = [p for p in problems if p.code == code]
    assert len(found) == 1, problems
    p = found[0]
    assert (p.where, p.line) == (where, line_of(text, anchor)), p
    assert fragment in p.message, p.message


def test_every_error_code_has_a_case():
    codes = {c[2] for c in CASES} | {'bad_name', 'not_found'}  # those two: test_lookup_*
    assert codes == {'not_found', 'bad_name', 'toml_syntax', 'wrong_schema', 'missing_key',
                     'unknown_key', 'wrong_type', 'out_of_range', 'bad_value', 'time_order',
                     'sets_nothing', 'bad_ramp', 'ramp_overlap', 'ramp_past_end', 'unsampled'}


def test_a_missing_key_written_after_a_table_says_where_it_went():
    text = edited(('duration_ms = 7000\n', ''), (*AFTER_SETUP,
                                                 AFTER_SETUP[0] + 'duration_ms = 7000\n'))
    missing = [p for p in problems_of(text) if p.code == 'missing_key']
    assert [p.where for p in missing] == ['duration_ms']
    assert 'the duration_ms written in [setup] counts as part of it' in missing[0].message


def test_all_problems_are_reported_at_once():
    text = edited((LAUNCH.splitlines(True)[6], 'description = 5\n'), top('expect = 1'),
                  ('accelerator = 1.0', 'accelerator = 1.5'))
    problems = problems_of(text)
    assert [p.code for p in problems] == ['wrong_type', 'unknown_key', 'out_of_range']
    assert [p.line for p in problems] == sorted(p.line for p in problems)


def test_timing_checks_wait_for_a_clean_structure():
    """A ramp overlap after a type error would be noise: only the type error."""
    text = edited(('accelerator = 1.0', 'accelerator = { to = 1.0, ramp_ms = 300 }'),
                  ('[[at]]\nt_ms = 6000\n', '[[at]]\nt_ms = 2100\naccelerator = 0.5\n\n'
                                            '[[at]]\nt_ms = 6000\n'),
                  ('rtd_pressed = true', 'rtd_pressed = 1'))
    assert [p.code for p in problems_of(text)] == ['wrong_type']
    without = text.replace('rtd_pressed = 1', 'rtd_pressed = true')
    assert [p.code for p in problems_of(without)] == ['ramp_overlap']


def test_problem_format():
    text = edited(('accelerator = 1.0', 'accelerator = 1.5'))
    path = SCENARIO_DIR / 'my-test.toml'  # need not exist: only its place in the repo counts
    with pytest.raises(ScenarioError) as caught:
        scenario_file.loads(text.encode(), 'my-test', path=path)
    line = line_of(text, 'accelerator = 1.5')
    assert caught.value.report().splitlines() == [
        f'boards/VCU/Sil/scenarios/my-test.toml:{line}: error: at[5].accelerator: 1.5 is '
        'outside 0..1 (a fraction of pedal travel: 0.5 is half pedal) [out_of_range]',
        'boards/VCU/Sil/scenarios/my-test.toml: 1 error. Nothing ran.']
    lineless = scenario_file.Problem('error', 'missing_key', 'description', 'is missing')
    assert lineless.format('x.toml') == 'x.toml: error: description: is missing [missing_key]'


@pytest.mark.parametrize('file_name, rename', [('Launch.toml', 'launch.toml'),
                                               ('my_launch.toml', 'my-launch.toml'),
                                               ('launch.yaml', 'launch.toml')])
def test_lookup_bad_file_names(tmp_path, file_name, rename):
    path = tmp_path / file_name
    path.write_text(LAUNCH)
    with pytest.raises(ScenarioError) as caught:
        scenario_file.load(str(path))
    [p] = caught.value.problems
    assert p.code == 'bad_name' and f'rename it to {rename}' in p.message
    assert caught.value.report().startswith(f'{path}: error: ')


@pytest.mark.parametrize('name, hint', [('Launch', 'did you mean launch?'),
                                        ('my_launch', None),  # my-launch is no scenario
                                        ('launch.yaml', 'rename it to launch.toml')])
def test_lookup_bad_names(name, hint):
    with pytest.raises(ScenarioError) as caught:
        scenario_file.load(name)
    [p] = caught.value.problems
    assert p.code == 'bad_name' and f'{name!r} is not a scenario name' in p.message
    if hint is None:
        assert 'did you mean' not in p.message and 'rename' not in p.message
    else:
        assert hint in p.message
    assert caught.value.report() == f'vcusil: {p.message} [bad_name]'


def test_lookup_not_found(tmp_path):
    with pytest.raises(ScenarioError) as caught:
        scenario_file.load('lanch')
    [p] = caught.value.problems
    assert p.code == 'not_found'
    assert p.message == ("no scenario named 'lanch'; did you mean launch? scenarios/ has: "
                         f'{", ".join(scenario_file.names())}. A path to a .toml file also '
                         'works.')
    missing = tmp_path / 'gone.toml'
    with pytest.raises(ScenarioError) as caught:
        scenario_file.load(str(missing))
    [p] = caught.value.problems
    assert p.code == 'not_found' and p.message == f'no scenario file at {missing}'


@pytest.mark.parametrize('arg', ['launch.toml', 'scenarios/launch', 'sub/LAUNCH.toml',
                                 'lanch.toml'])
def test_lookup_a_missing_path_suggests_the_name(tmp_path, monkeypatch, arg):
    """A path is from the working directory, so launch.toml from anywhere but
    scenarios/ misses; the name alone would do."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ScenarioError) as caught:
        scenario_file.load(arg)
    [p] = caught.value.problems
    assert p.code == 'not_found'
    assert p.message == (f'no scenario file at {arg}; did you mean launch? scenarios/ has: '
                         f'{", ".join(scenario_file.names())}.')


# (the file's name, the name its directory lists, the message): a
# case-insensitive filesystem lists the file's own name, whatever case the
# argument has. Simulated here; test_lookup_on_a_case_insensitive_filesystem
# is the real thing.
LISTED = [
    ('LAUNCH.toml', 'launch.toml',
     'the file is named launch.toml, and only {arg} is in another case; write {dir}/launch.toml'),
    ('launch.toml', 'Launch.toml', 'a scenario file is named in lowercase words joined by '
                                   'dashes and ends in .toml; rename Launch.toml to launch.toml'),
    ('launch.toml', 'launch.toml', None),
]


@pytest.mark.parametrize('written, listed, message', LISTED,
                         ids=['argument-in-another-case', 'file-in-another-case', 'as-listed'])
def test_lookup_names_the_file_as_its_directory_lists_it(tmp_path, monkeypatch, written, listed,
                                                         message):
    path = tmp_path / written
    path.write_text(LAUNCH)
    real = scenario_file.os.listdir
    monkeypatch.setattr(scenario_file.os, 'listdir',
                        lambda p: [listed] if Path(p) == tmp_path else real(p))
    if message is None:
        assert scenario_file.load(str(path)).name == 'launch'
        return
    with pytest.raises(ScenarioError) as caught:
        scenario_file.load(str(path))
    [p] = caught.value.problems
    assert p.code == 'bad_name'
    assert p.message == message.format(arg=path, dir=tmp_path)
    assert caught.value.report().startswith(f'{path}: error: ')


def test_lookup_on_a_case_insensitive_filesystem(tmp_path):
    (tmp_path / 'launch.toml').write_text(LAUNCH)
    if not (tmp_path / 'LAUNCH.toml').is_file():
        pytest.skip('a case-sensitive filesystem (Linux): LAUNCH.toml is another file')
    arg = tmp_path / 'LAUNCH.toml'
    with pytest.raises(ScenarioError) as caught:
        scenario_file.load(str(arg))
    [p] = caught.value.problems
    assert (p.code, p.message) == ('bad_name', f'the file is named launch.toml, and only {arg} '
                                               f'is in another case; write {tmp_path}/launch.toml')
    (tmp_path / 'launch.toml').rename(tmp_path / 'Launch.toml')
    with pytest.raises(ScenarioError) as caught:
        scenario_file.load(str(tmp_path / 'launch.toml'))
    [p] = caught.value.problems
    assert (p.code, p.message) == ('bad_name', 'a scenario file is named in lowercase words '
                                               'joined by dashes and ends in .toml; rename '
                                               'Launch.toml to launch.toml')


def test_scenario_paths_are_the_regular_files(tmp_path, monkeypatch):
    for name in ('launch.toml', 'launch-x.toml', 'b.toml', '.hidden.toml', '._launch.toml',
                 'notes.txt'):
        (tmp_path / name).write_text(LAUNCH)
    (tmp_path / 'sub.toml').mkdir()
    (tmp_path / '.#launch.toml').symlink_to('dev@host.4242')  # an Emacs lock: dangling
    monkeypatch.setattr(scenario_file, 'SCENARIO_DIR', tmp_path)
    # In name order: launch before launch-x, though launch-x.toml sorts first.
    assert scenario_file.scenario_paths() == [tmp_path / n for n in ('b.toml', 'launch.toml',
                                                                     'launch-x.toml')]
    assert scenario_file.names() == ['b', 'launch', 'launch-x']
    with pytest.raises(ScenarioError) as caught:
        scenario_file.load('lanch')
    assert caught.value.problems[0].message == (
        "no scenario named 'lanch'; did you mean launch? scenarios/ has: b, launch, launch-x. A "
        'path to a .toml file also works.')


def test_every_mark_suggestion_is_a_mark():
    for bad in ('Loop Open', '', '!!!', '\xe9', '-rtd-', 'a' * 41, 'a-' * 20 + 'b',
                'x' * 39 + '-yz', 'RTD_1', 'my-scenario!'):
        text = edited(('mark = "rtd"', f'mark = {json.dumps(bad)}'))
        [p] = problems_of(text)
        suggested = re.search(r'such as "(.*)"$', p.message)[1]
        assert NAME.fullmatch(suggested) and len(suggested) <= MAX_MARK, (bad, suggested)
        scenario_file.loads(text.replace(f'mark = {json.dumps(bad)}',
                                         f'mark = "{suggested}"').encode(), 'launch')


def test_launch_file_is_the_stage_2_launch_at_every_ms():
    sf = scenario_file.load('launch')
    reference = Launch()
    assert (sf.name, sf.duration_ms) == (reference.name, reference.duration_ms)
    for t in range(0, reference.duration_ms + 6):
        assert repr(sf.timeline.at(t)) == repr(reference.at(t)), t


def test_launch_file_drives_the_firmware_like_the_stage_2_launch(lib, tmp_path):
    def run(scenario):
        with Firmware(lib) as fw:
            return cosim.run(scenario, fw, ToyCar())
    new, old = run(scenario_file.load('launch').timeline), run(Launch())
    assert new.status == old.status == 'completed'
    assert new.rows == old.rows
    assert new.snapshots == old.snapshots
    assert new.frames == old.frames
    report.write_trace(new, tmp_path / 'new.csv')
    report.write_trace(old, tmp_path / 'old.csv')
    assert (tmp_path / 'new.csv').read_bytes() == (tmp_path / 'old.csv').read_bytes()


@pytest.mark.parametrize('name', scenario_file.names())
def test_every_file_loads_and_checks_clean(name):
    sf = scenario_file.load(name)
    assert sf.name == name == sf.path.stem
    findings = check.lint(sf)
    assert findings.active == ()
    assert {p.code for p, _ in findings.allowed} == set(sf.allow)
    json.dumps(sf.parameters(), allow_nan=False)
    assert sf.display == f'boards/VCU/Sil/scenarios/{name}.toml'


def test_the_four_stage_3_files_are_there():
    # At least: README step 1 adds a file of your own.
    assert set(scenario_file.names()) >= {'brake-over-throttle', 'launch', 'part-throttle',
                                          'shutdown-loop-open'}


@pytest.mark.parametrize('name', scenario_file.names())
def test_every_sample_is_the_ramp_formula_to_the_bit(name):
    """Runs repeat bit for bit only if every sample is exactly
    a + (b - a) * ((t - T) / R), the README's formula: worked out here again
    from the file as tomllib reads it, and compared with ==."""
    sf = scenario_file.load(name)
    doc = tomllib.loads(sf.data.decode())
    for signal in scenario_file.SIGNALS:
        changes = [(block['t_ms'], block[signal]) for block in doc['at'] if signal in block]
        for t in range(0, sf.duration_ms, 3):
            want = held = DEFAULTS[signal]
            for t_ms, value in changes:
                if t_ms > t:
                    break
                if isinstance(value, dict):
                    a, b, ramp_ms = held, value['to'] + 0.0, value['ramp_ms']
                    want = b if t >= t_ms + ramp_ms else a + (b - a) * ((t - t_ms) / ramp_ms)
                    held = b
                elif signal == 'hvc_state':
                    want = held = HVC_STATES[value]
                else:
                    want = held = value if isinstance(value, bool) else value + 0.0
            got = sf.timeline.value(signal, t)
            assert got == want and type(got) is type(want), (signal, t, got, want)


def _shown(changes: list[tuple[int, int]], t: int) -> int | None:
    """Which change (t_ms, ramp_ms) the sample at t shows, by the README's
    rules: the last at or before t, but a ramp at t itself still shows the
    value before it. None: the default."""
    shown = None
    for i, (t_ms, ramp_ms) in enumerate(changes):
        if t_ms < t or (t_ms == t and not ramp_ms):
            shown = i
    return shown


def test_unsampled_is_exactly_the_changes_no_sample_shows():
    """Every one to three brake changes in 0..8 ms, each a step or a ramp of
    1 to 4 ms, in runs that end 1 to 4 ms after the last: unsampled names
    exactly the changes that _shown() finds no sample for. _shown() is
    checked against the Timeline as it goes."""
    head = f'schema = "{scenario_file.SCHEMA}"\ndescription = "d"\nduration_ms = {{}}\n'
    outcomes = {True: 0, False: 0}
    for n in (1, 2, 3):
        for times in itertools.combinations(range(9), n):
            for ramps in itertools.product(range(5), repeat=n):
                changes = list(zip(times, ramps, strict=True))
                if any(t + r > after for (t, r), (after, _) in itertools.pairwise(changes)):
                    continue  # ramp_overlap
                ends = [100.0 * (i + 1) for i in range(n)]  # all differ, and from the default
                segments, held = [], DEFAULTS['brake_psi']
                blocks = ''
                for (t, r), end in zip(changes, ends, strict=True):
                    segments.append(Segment(t, held, end, r))
                    held = end
                    blocks += f'[[at]]\nt_ms = {t}\nbrake_psi = ' + (
                        f'{{ to = {end}, ramp_ms = {r} }}\n' if r else f'{end}\n')
                for extra in (1, 2, 3, 4):
                    duration = times[-1] + extra
                    timeline = Timeline('x', duration, (('brake_psi', tuple(segments)),))
                    seen = set()
                    for s in range(0, last_step_ms(duration) + 1, 3):
                        i = _shown(changes, s)
                        seen.add(i)
                        value = timeline.value('brake_psi', s)
                        if i is None:
                            assert value == DEFAULTS['brake_psi'], (changes, s)
                        elif s >= changes[i][0] + changes[i][1]:
                            assert value == ends[i], (changes, s)
                        else:  # inside ramp i, past its start
                            start = segments[i].start
                            assert min(start, ends[i]) < value < max(start, ends[i]), (changes, s)
                    try:
                        scenario_file.loads((head.format(duration) + blocks).encode(), 'x')
                        problems = []
                    except ScenarioError as exc:
                        problems = exc.problems
                    assert {p.code for p in problems} <= {'unsampled', 'ramp_past_end'}, problems
                    unsampled = {p.where for p in problems if p.code == 'unsampled'}
                    assert unsampled == {f'at[{i}].brake_psi' for i in range(n) if i not in seen}, (
                        changes, duration, problems)
                    outcomes[bool(unsampled)] += 1
    assert min(outcomes.values()) > 1000, outcomes


def test_numbers_become_floats():
    text = edited(('accelerator = 1.0', 'accelerator = 1'), ('brake_psi = 0.0', 'brake_psi = -0.0'))
    sf = scenario_file.loads(text.encode(), 'launch')
    pedal = sf.blocks[5].values['accelerator']
    assert type(pedal) is float and repr(pedal) == '1.0'
    assert repr(sf.blocks[4].values['brake_psi']) == '0.0'
    at = sf.parameters()['at']
    assert repr(at[5]['accelerator']) == '1.0' and repr(at[4]['brake_psi']) == '0.0'
    assert repr(sf.timeline.at(2000)[0].accelerator) == '1.0'
    # A switch stays a bool, a name stays as written, a ramp keeps its parts.
    ramp = edited(('accelerator = 1.0', 'accelerator = { to = 1, ramp_ms = 300 }'))
    params = scenario_file.loads(ramp.encode(), 'launch').parameters()
    assert params['at'][5]['accelerator'] == {'to': 1.0, 'ramp_ms': 300}
    assert type(params['at'][5]['accelerator']['to']) is float
    assert params['at'][3]['rtd_pressed'] is True and params['at'][2]['hvc_state'] == 'ENERGIZED'


def test_signals_are_the_dataclass_fields():
    """Adding a Driver or RestOfCar field forces a decision about its file key."""
    signals = scenario_file.SIGNALS
    assert tuple(signals) == DRIVER_SIGNALS + CAR_SIGNALS
    assert {s for s, v in signals.items() if v.owner == 'driver'} == set(DRIVER_SIGNALS)
    assert {s for s, v in signals.items() if v.owner == 'rest_of_car'} == set(CAR_SIGNALS)
    defaults = {**dataclasses.asdict(Driver()), **dataclasses.asdict(RestOfCar())}
    tl = scenario_file.loads(edited(drop_blocks, append('[[at]]\nt_ms = 0\nmark = "start"\n'))
                             .encode(), 'launch').timeline
    assert {s: tl.value(s, 0) for s in signals} == defaults
    for name, signal in signals.items():
        assert signal.unit, name
        if signal.kind == 'number':
            assert signal.lo < signal.hi, name
            assert signal.lo <= defaults[name] <= signal.hi, name


def test_names_map_to_numbers():
    assert (HVC_STATES['NOT_ENERGIZED'], HVC_STATES['PRECHARGING'], HVC_STATES['ENERGIZED']) == (
        HVC_NOT_ENERGIZED, HVC_PRECHARGING, HVC_ENERGIZED)
    for name, number in HVC_STATES.items():
        text = edited(('hvc_state = "ENERGIZED"', f'hvc_state = "{name}"'))
        sf = scenario_file.loads(text.encode(), 'launch')
        assert sf.timeline.at(600)[1].hvc_state == number
        assert sf.parameters()['at'][2]['hvc_state'] == name


def test_names_match_the_spec(can_table_path):
    enums = CanTable(can_table_path).enums
    assert HVC_STATES == {name: int(number) for number, name in enums['HvcState'].items()}
    assert EVENT_MODES == {name: int(number) for number, name in enums['EventMode'].items()}


def test_setup():
    for name, number in EVENT_MODES.items():
        text = edited(('event_mode = "UNASSIGNED"', f'event_mode = "{name}"'))
        assert scenario_file.loads(text.encode(), 'launch').setup.event_mode == number
    sf = scenario_file.load('launch')
    assert sf.setup == scenario_file.Setup(event_mode=0, soc_start=0.9)
    assert sf.parameters()['setup'] == {'event_mode': 'UNASSIGNED', 'soc_start': 0.9}
    bare = scenario_file.loads(edited(('[setup]\n', ''), ('event_mode = "UNASSIGNED"', ''),
                                      ('soc_start = 0.9', '')).encode(), 'launch')
    assert bare.setup == scenario_file.Setup(None, None) and bare.parameters()['setup'] == {}
    half = edited(('event_mode = "UNASSIGNED"', ''))
    sf = scenario_file.loads(half.encode(), 'launch')
    assert sf.setup == scenario_file.Setup(None, 0.9) and sf.parameters()['setup'] == {
        'soc_start': 0.9}


def test_marks_and_first_steps():
    sf = scenario_file.load('launch')
    assert sf.marks == (('precharge', 200, 201), ('energized', 600, 600), ('rtd', 1000, 1002),
                        ('brake-off', 1500, 1500), ('pedal-on', 2000, 2001),
                        ('pedal-off', 6000, 6000))
    assert [first_step_ms(t) for t in (0, 1, 2, 3, 200, 1000, 1500, 2000)] == [
        0, 3, 3, 3, 201, 1002, 1500, 2001]
    assert (last_step_ms(7000), last_step_ms(7001), last_step_ms(7003)) == (6999, 6999, 7002)
    assert last_step_ms(1) == 0 and last_step_ms(3) == 0 and last_step_ms(4) == 3


def test_resolve(tmp_path, monkeypatch):
    assert scenario_file.resolve('launch') == SCENARIO_DIR / 'launch.toml'
    (tmp_path / 'sub').mkdir()
    (tmp_path / 'sub' / 'my-test.toml').write_text(LAUNCH)
    (tmp_path / 'here.toml').write_text(LAUNCH)
    monkeypatch.chdir(tmp_path)
    # A path is relative to the working directory; a .toml without a / is a path too.
    assert scenario_file.resolve('sub/my-test.toml') == Path('sub/my-test.toml')
    assert scenario_file.resolve('here.toml') == Path('here.toml')
    assert scenario_file.load('sub/my-test.toml').name == 'my-test'
    with pytest.raises(ScenarioError, match='did you mean launch'):
        scenario_file.resolve('lanch')
    with pytest.raises(ScenarioError, match=re.escape(', '.join(scenario_file.names()))):
        scenario_file.resolve('here')  # a name, not ./here.toml


def test_same_bytes_same_sha256_and_parameters(tmp_path):
    copy = tmp_path / 'launch.toml'
    copy.write_bytes((SCENARIO_DIR / 'launch.toml').read_bytes())
    a, b = scenario_file.load('launch'), scenario_file.load(str(copy))
    assert a.sha256 == b.sha256 == hashlib.sha256(copy.read_bytes()).hexdigest()
    assert a.parameters() == b.parameters() and a.timeline == b.timeline


def test_comment_only_change_keeps_the_timeline_and_changes_the_sha256():
    a = scenario_file.load('launch')
    b = scenario_file.loads((LAUNCH + '# one more comment\n').encode(), 'launch')
    assert a.timeline == b.timeline and a.parameters() == b.parameters()
    assert a.sha256 != b.sha256


def test_display_path(tmp_path):
    inside = scenario_file.load('launch')
    assert inside.display == inside.repo_path == 'boards/VCU/Sil/scenarios/launch.toml'
    outside = tmp_path / 'mine.toml'
    outside.write_text(LAUNCH)
    sf = scenario_file.load(str(outside))
    assert sf.display == str(outside) and sf.repo_path is None
    assert sf.path == outside.resolve()
    # Another --repo: the same file is outside it.
    elsewhere = scenario_file.load('launch', repo=tmp_path)
    assert elsewhere.repo_path is None and elsewhere.display == str(SCENARIO_DIR / 'launch.toml')


def _run_demo(lib, name):
    with Firmware(lib) as fw:
        result = cosim.run(scenario_file.load(name).timeline, fw, ToyCar())
    assert result.status == 'completed'
    return result.rows


def test_brake_over_throttle_demo_cuts_torque_but_keeps_drive(lib):
    """The filtered brake (alpha 0.10 per 3 ms tick) reaches 300 psi at 4104
    without ADC rounding, which can move it a tick; the raw pedal falls under
    5 % between 5337 and 5340; the filtered pedal passes 9 % again at 5853."""
    rows = _run_demo(lib, 'brake-over-throttle')
    bot = [r['t_ms'] for r in rows if int(r['fw_faults']) & BRAKE_OVER_THROTTLE]
    assert 4101 <= bot[0] <= 4107 and 5337 <= bot[-1] + 3 <= 5343
    assert bot == list(range(bot[0], bot[-1] + 3, 3))  # one stretch
    latched = [r for r in rows if bot[0] <= r['t_ms'] <= bot[-1]]
    assert all(r['fw_torque_request_nm'] == 0.0 for r in latched)
    assert all(r['fw_state'] == STATE_DRIVE and r['fw_inverter_enable'] == 1 for r in latched)
    # Torque before the brake, and again once the reapplied pedal passes 9 %.
    assert any(r['fw_torque_request_nm'] > 0 for r in rows if 3900 <= r['t_ms'] < 4000)
    assert all(r['fw_torque_request_nm'] == 0.0 for r in rows if bot[-1] < r['t_ms'] < 5850)
    assert all(r['fw_torque_request_nm'] > 0 for r in rows if 5862 <= r['t_ms'])
    drive = [r for r in rows if r['t_ms'] >= 1026]
    assert all(r['fw_state'] == STATE_DRIVE for r in drive)
    assert not any(int(r['fw_faults']) & TORQUE_CUT_FAULTS for r in drive)
    assert not any(r['fw_hard_braking'] for r in rows)


def test_shutdown_demo_faults_within_20_ms(lib):
    """The loop is first sampled at 4002 and its debounce needs 10 ms, which
    the 3 ms ticks reach at 4014."""
    rows = _run_demo(lib, 'shutdown-loop-open')
    fault = [r['t_ms'] for r in rows if r['fw_state'] == STATE_FAULT]
    assert fault[0] == first_step_ms(4000) + check._debounced(
        check.THRESHOLDS['ShutdownParams::debounce_ms']) == 4014
    assert fault == list(range(fault[0], rows[-1]['t_ms'] + 3, 3))  # and stays there
    after = [r for r in rows if r['t_ms'] >= fault[0]]
    assert all(r['fw_torque_request_nm'] == 0.0 and r['fw_inverter_enable'] == 0 for r in after)
    assert all(int(r['fw_faults']) & SHUTDOWN_OPEN for r in after)
    # The state left DRIVE before the HVC frame at 4020, so the AIRs opening
    # never counts as CONTACTORS_OPEN.
    assert not any(int(r['fw_faults']) & CONTACTORS_OPEN for r in rows)
    assert all(r['fw_state'] == STATE_DRIVE for r in rows if 1026 <= r['t_ms'] < fault[0])


def _torque_table() -> tuple[list[float], float]:
    """TorqueParams::power_limit_torque and max_rpm, read from params.hpp."""
    text = PARAMS.read_text()
    table = re.search(r'power_limit_torque\[kLookupPoints\] = \{([^}]*)\}', text)[1]
    max_rpm = re.search(r'float max_rpm = ([\d.]+)f;', text)[1]
    return [float(v) for v in table.split(',')], float(max_rpm)


def test_part_throttle_demo_follows_the_pedal(lib):
    """torque = table(rpm) x travel^1.5, travel being the pedal past the 9 %
    and 74 % deadzones: about a third of the full-pedal torque at 40 % and
    70 % at 60 %. The holds start once the pedal filter has settled."""
    rows = _run_demo(lib, 'part-throttle')
    assert 1020 <= next(r['t_ms'] for r in rows if r['fw_state'] == STATE_DRIVE) <= 1026
    table, max_rpm = _torque_table()

    def full_pedal(rpm):  # longhorn::Lookup1D over 0..max_rpm
        pos = min(abs(rpm), max_rpm) / max_rpm * (len(table) - 1)
        i = min(int(pos), len(table) - 2)
        return table[i] + (table[i + 1] - table[i]) * (pos - i)
    for start, end, pedal, share in ((2400, 4000, 0.4, 0.33), (4300, 6000, 0.6, 0.70)):
        travel = (pedal - 0.09) / (0.74 - 0.09)
        assert travel ** 1.5 == pytest.approx(share, abs=0.005)
        for r in (r for r in rows if start <= r['t_ms'] < end):
            assert r['fw_torque_request_nm'] == pytest.approx(
                full_pedal(r['fw_motor_rpm']) * travel ** 1.5, rel=0.01), r['t_ms']
    forty = max(r['fw_torque_request_nm'] for r in rows if 2400 <= r['t_ms'] < 4000)
    sixty = min(r['fw_torque_request_nm'] for r in rows if 4300 <= r['t_ms'] < 6000)
    assert sixty > forty
    # The raw pedal falls through 9 % at 6255; the filter takes a few ms more.
    assert all(r['fw_torque_request_nm'] == 0.0 for r in rows if r['t_ms'] >= 6270)
    assert all(r['fw_state'] == STATE_DRIVE for r in rows if r['t_ms'] >= 1026)
