"""Scenario files: the launch file is the stage 2 Launch, every file loads
clean, and every problem is reported with its code, place and line."""

import dataclasses
import hashlib
import itertools
import json
import re
from pathlib import Path

import pytest
import yaml
from fakes import Launch, ToyCar

from vcusil import check, cosim, report, scenario_file
from vcusil.candecode import CanTable
from vcusil.firmware import (EVENT_MODES, HVC_ENERGIZED, HVC_NOT_ENERGIZED, HVC_PRECHARGING,
                             HVC_STATES, STATE_DRIVE, Firmware)
from vcusil.scenario import (CAR_SIGNALS, DEFAULTS, DRIVER_SIGNALS, Driver, RestOfCar, Segment,
                             Timeline)
from vcusil.scenario_file import (MAX_MARK, NAME, SCENARIO_DIR, Ramp, ScenarioError, Setup,
                                  first_step_ms, last_step_ms)

LAUNCH = (SCENARIO_DIR / 'launch.yml').read_text()
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
    end at \\n only: splitlines() also breaks at U+2028, which the loader
    refuses rather than count."""
    if anchor is None:
        return None
    anchor, offset = anchor if isinstance(anchor, tuple) else (anchor, 0)
    found = [n for n, line in enumerate(text.split('\n'), 1) if anchor in line]
    assert len(found) == 1, (anchor, found)
    return found[0] + offset


def drop_blocks(text: str) -> str:
    return text[:text.index('\nat:\n') + 1]


AFTER_SETUP = ('  soc_start: 0.9          # at 1.0 the car model\'s pack accepts no regen\n',)


def top(line: str):
    return ('duration_ms: 7000\n', f'duration_ms: 7000\n{line}\n')


def allow(line: str):
    return ('\nat:\n', f'\nallow:\n  {line}\n\nat:\n')


def append(lines: str):
    return lambda text: text + lines


def block(keys: str) -> str:
    """A block of the at list from its keys, one per line, t_ms first."""
    first, *rest = keys.split('\n')
    return f'  - {first}\n' + ''.join(f'    {key}\n' for key in rest)


def block_before(t_ms: int, *blocks: str):
    """New blocks (each: its keys, t_ms first), just before launch's block
    at t_ms."""
    anchor = f'  - t_ms: {t_ms}\n'
    return (anchor, ''.join(block(keys) + '\n' for keys in blocks) + anchor)


def append_block(keys: str):
    return append('\n' + block(keys))


SETUP = ''.join(LAUNCH.splitlines(True)[10:13])  # setup: and its two keys
assert SETUP.startswith('setup:\n') and SETUP.count('\n') == 3
RTD_BLOCK = '  - t_ms: 1000\n    mark: rtd\n    rtd_pressed: true\n'
LAST_PEDAL = '  - t_ms: 6000\n    mark: pedal-off\n    accelerator: 0.0'
PRECHARGE_BLOCK = '  - t_ms: 200\n    mark: precharge\n    hvc_state: PRECHARGING\n'


def indentless(text: str) -> str:
    """launch.yml with its dashes at the margin and the keys two past them."""
    return text.replace('\n  - ', '\n- ').replace('\n    ', '\n  ')


# (id, edits, code, where, line anchor, part of the message, problems in all)
CASES = [
    # yaml_syntax (the strict loader: STRICT; PyYAML's own: SYNTAX)
    ('duplicate-key', [('mark: rtd\n', 'mark: rtd\n    mark: rtd2\n')], 'yaml_syntax',
     'at[3].mark', 'mark: rtd2', 'each moment needs its own block', 1),
    ('setup-twice', [append('\nsetup:  # again\n  soc_start: 0.5\n')], 'yaml_syntax', 'setup',
     'setup:  # again', '; merge them', 1),
    ('not-utf-8', [('Power up, ready', 'Power\udcff up, ready')], 'yaml_syntax', '',
     'description:', 'byte 393 is not UTF-8 text; save the file as UTF-8', 1),
    # wrong_schema
    ('schema-v2', [('scenario.v1\n', 'scenario.v2\n')], 'wrong_schema', 'schema', 'schema:',
     'written for a different harness version', 1),
    ('schema-other', [('lhre.vcu-sil-scenario.v1', 'vcu-sil.v1')], 'wrong_schema', 'schema',
     'schema:', 'vcu-sil.v1 is not lhre.vcu-sil-scenario.v1', 1),
    # missing_key
    ('no-schema', [('schema: lhre.vcu-sil-scenario.v1\n', '')], 'missing_key', 'schema', None,
     'is missing', 1),
    ('no-description', [(LAUNCH.splitlines(True)[6], '')], 'missing_key', 'description', None,
     'is missing', 1),
    ('no-duration', [('duration_ms: 7000\n', '')], 'missing_key', 'duration_ms', None,
     'is missing', 1),
    ('no-blocks', [drop_blocks], 'missing_key', 'at', None, 'at least one block under at:', 1),
    ('no-blocks-under-at', [lambda text: drop_blocks(text) + 'at:\n'], 'missing_key', 'at',
     'at:', 'has no blocks; a scenario needs at least one block under at:', 1),
    ('block-without-time', [('  - t_ms: 1000\n    mark: rtd', '  - mark: rtd')], 'missing_key',
     'at[3].t_ms', '- mark: rtd', 'every block needs its time', 1),
    ('block-without-time-under-a-dash-alone', [('  - t_ms: 1000\n    mark: rtd', '  -\n    mark: rtd')],
     'missing_key', 'at[3].t_ms', ('mark: rtd', -1), 'every block needs its time', 1),
    ('block-without-time-under-a-dash-at-the-margin', [
        lambda text: text.replace('\n  - ', '\n- ').replace('\n    ', '\n  '),
        ('- t_ms: 1000\n  mark: rtd', '-\n  mark: rtd')],
     'missing_key', 'at[3].t_ms', ('mark: rtd', -1), 'every block needs its time', 1),
    # unknown_key
    ('near-miss-top', [('duration_ms: 7000', 'durations_ms: 7000')], 'unknown_key',
     'durations_ms', 'durations_ms', 'did you mean duration_ms?', 2),
    ('near-miss-block', [('brake_psi: 0.0', 'brake: 0.0')], 'unknown_key', 'at[4].brake',
     'brake: 0.0', 'is not an lhre.vcu-sil-scenario.v1 key; did you mean brake_psi?', 1),
    ('near-miss-setup', [('event_mode: UNASSIGNED', 'event: UNASSIGNED')], 'unknown_key',
     'setup.event', 'event:', 'is not a setup key; did you mean event_mode?', 1),
    ('brake-psi-full-in-setup', [('  soc_start: 0.9', '  brake_psi_full: 1500\n  soc_start: 0.9')],
     'unknown_key', 'setup.brake_psi_full', 'brake_psi_full:', 'is not a setup key; it stays the '
     '--brake-psi-full option: it calibrates the car model, not the drive', 1),
    ('near-miss-allow', [allow('brake_over_throtle: on purpose')], 'unknown_key',
     'allow.brake_over_throtle', 'brake_over_throtle', 'did you mean brake_over_throttle?', 1),
    ('allow-unused-allow', [allow('unused_allow: quiet')], 'unknown_key', 'allow.unused_allow',
     'unused_allow:', 'cannot be allowed', 1),
    ('key-indented-into-setup', [('duration_ms: 7000\n', ''),
                                 (*AFTER_SETUP, AFTER_SETUP[0] + '  duration_ms: 7000\n')],
     'unknown_key', 'setup.duration_ms', 'duration_ms: 7000',
     'top-level keys are not indented, so this one counts as part of setup', 2),
    ('key-indented-into-a-block', [('duration_ms: 7000\n', ''), append('    duration_ms: 7000\n')],
     'unknown_key', 'at[6].duration_ms', 'duration_ms: 7000',
     'top-level keys are not indented, so this one counts as part of at[6]', 2),
    ('planned-brake', [('brake_psi: 0.0', 'brake_front_psi: 0.0')], 'unknown_key',
     'at[4].brake_front_psi', 'brake_front_psi', 'planned: separate front and rear brake', 1),
    ('planned-after', [('  - t_ms: 1500\n', '  - t_ms: 1500\n    after: "state:DRIVE"\n')],
     'unknown_key', 'at[4].after', 'after:',
     'planned: times relative to a firmware state, stage 4', 1),
    ('planned-expect', [top('expect: 1')], 'unknown_key', 'expect', 'expect: 1', 'stage 4', 1),
    ('planned-can', [top('can: 1')], 'unknown_key', 'can', 'can: 1', 'stage 6', 1),
    ('unknown-key-word', [top('colour: red')], 'unknown_key', 'colour', 'colour: red',
     'is not an lhre.vcu-sil-scenario.v1 key', 1),
    ('signal-at-the-top-level', [top('accelerator: 0.5')], 'unknown_key', 'accelerator',
     'accelerator: 0.5', 'is not an lhre.vcu-sil-scenario.v1 key; signals go in a block under '
                         'at:, at the time they change', 1),
    ('setup-key-at-the-top-level', [top('soc_start: 0.5')], 'unknown_key', 'soc_start',
     'soc_start: 0.5', 'is not an lhre.vcu-sil-scenario.v1 key; soc_start goes under setup:', 1),
    ('setup-key-in-a-block', [('    mark: rtd\n', '    mark: rtd\n    event_mode: SKIDPAD\n')],
     'unknown_key', 'at[3].event_mode', 'event_mode: SKIDPAD',
     'is not an lhre.vcu-sil-scenario.v1 key; event_mode goes under setup:', 1),
    ('planned-expect-as-a-list', [top('expect:\n  - x')], 'unknown_key', 'expect', 'expect:',
     'stage 4', 1),
    ('near-miss-setup-on-the-next-line', [('event_mode: UNASSIGNED', 'event:\n    UNASSIGNED')],
     'unknown_key', 'setup.event', 'event:', 'is not a setup key; did you mean event_mode?', 1),
    ('near-miss-allow-on-the-next-line', [allow('brake_hihg:\n    why')], 'unknown_key',
     'allow.brake_hihg', 'brake_hihg:', 'did you mean brake_high?', 1),
    ('near-miss-block-on-the-next-line', [('brake_psi: 0.0', 'brake:\n      0.0')],
     'unknown_key', 'at[4].brake', 'brake:', 'did you mean brake_psi?', 1),
    # Keys and names are ASCII: a lookalike or invisible character is named.
    ('lookalike-in-a-key', [('t_ms: 2000', 't_\u043cs: 2000')], 'unknown_key', 'at[5].t_\u043cs',
     't_\u043cs', 'did you mean t_ms? it holds U+043C CYRILLIC SMALL LETTER EM, which is not '
                  'ASCII: retype it', 2),
    ('non-ascii-key', [top('gr\u00f6\u00dfe: 1')], 'unknown_key', 'gr\u00f6\u00dfe',
     'gr\u00f6\u00dfe', 'is not an lhre.vcu-sil-scenario.v1 key; it holds U+00F6 LATIN SMALL '
                        'LETTER O WITH DIAERESIS, which is not ASCII: retype it', 1),
    ('invisible-in-a-key', [('accelerator: 1.0', 'accelerator\u200b: 1.0')], 'unknown_key',
     'at[5].accelerator\u200b', 'accelerator\u200b', 'did you mean accelerator? it holds U+200B '
                                                     'ZERO WIDTH SPACE, which is not ASCII: '
                                                     'retype it', 1),
    # A flow mapping's keys are on its one line.
    ('flow-setup', [(SETUP, 'setup: {event: SKIDPAD}\n')], 'unknown_key', 'setup.event',
     'setup: {', 'is not a setup key; did you mean event_mode?', 1),
    ('flow-allow', [top('allow: {brake_hihg: on purpose}')], 'unknown_key',
     'allow.brake_hihg', 'allow: {', 'is not a warning code; did you mean brake_high?', 1),
    # wrong_type
    ('at-number', [drop_blocks, top('at: 5')], 'wrong_type', 'at', 'at: 5',
     '5 is not a list of blocks; write at:, then each block as a list item, - t_ms: N, once per '
     'moment', 1),
    ('at-mapping', [lambda text: drop_blocks(text) + 'at:\n  t_ms: 0\n'],
     'wrong_type', 'at', 't_ms: 0', 'is a single mapping; write each block as a list item, '
                                    '- t_ms: N, once per moment', 1),
    ('at-item-not-a-block', [(PRECHARGE_BLOCK, '  - 200\n')], 'wrong_type', 'at[1]', '- 200',
     '200 is not a block; a block is - t_ms: N, with its keys under it', 1),
    ('at-item-missing-a-space', [(PRECHARGE_BLOCK, '  - t_ms:200\n')], 'wrong_type', 'at[1]',
     '- t_ms:200', 't_ms:200 is not a block; write a space after the :', 1),
    ('duration-float', [('duration_ms: 7000', 'duration_ms: 7000.5')], 'wrong_type',
     'duration_ms', 'duration_ms:', 'is not whole milliseconds', 1),
    ('duration-bool', [('duration_ms: 7000', 'duration_ms: true')], 'wrong_type',
     'duration_ms', 'duration_ms:', 'true is not whole milliseconds', 1),
    ('duration-string', [('duration_ms: 7000', 'duration_ms: "7000"')], 'wrong_type',
     'duration_ms', 'duration_ms:', '"7000" is not whole milliseconds; write it without quotes',
     1),
    ('time-float', [('t_ms: 200\n', 't_ms: 200.0\n')], 'wrong_type', 'at[1].t_ms',
     't_ms: 200.0', '200.0 is not whole milliseconds', 1),
    ('time-exponent', [('t_ms: 1000\n', 't_ms: 1e3\n')], 'wrong_type', 'at[3].t_ms',
     't_ms: 1e3', '1000.0 is not whole milliseconds', 1),
    ('time-bool', [('t_ms: 1000\n', 't_ms: true\n')], 'wrong_type', 'at[3].t_ms',
     't_ms: true', 'true is not whole milliseconds', 1),
    ('time-of-day', [('t_ms: 1000\n', 't_ms: 07:00:00\n')], 'wrong_type', 'at[3].t_ms',
     't_ms: 07:00:00', '07:00:00 is not whole milliseconds', 1),
    ('pedal-bool', [('accelerator: 1.0', 'accelerator: true')], 'wrong_type',
     'at[5].accelerator', 'accelerator: true', 'true is not a number', 1),
    ('pedal-string', [('accelerator: 1.0', 'accelerator: "full"')], 'wrong_type',
     'at[5].accelerator', 'accelerator: "full"', 'full is not a number', 1),
    ('pedal-word', [('accelerator: 1.0', 'accelerator: full')], 'wrong_type',
     'at[5].accelerator', 'accelerator: full', 'full is not a number', 1),
    ('ramp-to-word', [('accelerator: 1.0', 'accelerator: {to: full, ramp_ms: 300}')],
     'wrong_type', 'at[5].accelerator.to', 'to: full', 'full is not a number', 1),
    ('ramp-ms-word', [('accelerator: 1.0', 'accelerator: {to: 0.4, ramp_ms: abc}')],
     'wrong_type', 'at[5].accelerator.ramp_ms', 'ramp_ms: abc',
     'abc is not whole milliseconds; write an integer, such as 1500', 1),
    ('time-word', [('t_ms: 1000\n', 't_ms: abc\n')], 'wrong_type', 'at[3].t_ms', 't_ms: abc',
     'abc is not whole milliseconds; write an integer, such as 1500', 1),
    # Nesting under the loader's limit (over it: STRICT) is a value.
    ('pedal-nested-list', [('accelerator: 1.0', 'accelerator: [[0.5]]')], 'wrong_type',
     'at[5].accelerator', 'accelerator: [[0.5]]', '[[0.5]] is not a number', 1),
    ('switch-number', [('rtd_pressed: true', 'rtd_pressed: 1')], 'wrong_type',
     'at[3].rtd_pressed', 'rtd_pressed: 1', '1 is not true or false; write true or false', 1),
    ('switch-string', [('rtd_pressed: true', 'rtd_pressed: "yes"')], 'wrong_type',
     'at[3].rtd_pressed', 'rtd_pressed: "yes"', 'yes is not true or false; write true', 1),
    ('hvc-number', [('hvc_state: ENERGIZED', 'hvc_state: 2')], 'wrong_type',
     'at[2].hvc_state', 'hvc_state: 2', 'write the name: hvc_state: ENERGIZED', 1),
    ('mode-number', [('event_mode: UNASSIGNED', 'event_mode: 4')], 'wrong_type',
     'setup.event_mode', 'event_mode: 4', 'write the name: event_mode: ENDURANCE', 1),
    ('description-number', [(LAUNCH.splitlines(True)[6], 'description: 5\n')], 'wrong_type',
     'description', 'description: 5', '5 is not a string; write it in quotes', 1),
    ('soc-string', [('soc_start: 0.9', 'soc_start: high')], 'wrong_type', 'setup.soc_start',
     'soc_start: high', 'high is not a number', 1),
    ('reason-number', [allow('brake_high: 1')], 'wrong_type', 'allow.brake_high',
     'brake_high: 1', '1 is not a string', 1),
    ('setup-list', [(SETUP, 'setup:\n  - event_mode: SKIDPAD\n')], 'wrong_type', 'setup',
     '- event_mode', '[{event_mode: SKIDPAD}] is not a mapping; write setup: on its own line, '
                     'then its keys under it, indented', 1),
    ('allow-text', [top('allow: brake_high')], 'wrong_type', 'allow', 'allow: brake_high',
     'brake_high is not a mapping; write allow: on its own line, then one code: reason line per '
     'warning, indented', 1),
    ('schema-number-on-the-next-line', [('schema: lhre.vcu-sil-scenario.v1\n', 'schema:\n  5\n')],
     'wrong_type', 'schema', ('schema:', 1),
     '5 is not a string; write schema: lhre.vcu-sil-scenario.v1', 1),
    ('duration-float-on-the-next-line', [('duration_ms: 7000', 'duration_ms:\n  7000.5')],
     'wrong_type', 'duration_ms', '7000.5', 'is not whole milliseconds', 1),
    ('reason-number-on-the-next-line', [allow('brake_high:\n    5')], 'wrong_type',
     'allow.brake_high', ('brake_high:', 1), '5 is not a string', 1),
    # out_of_range
    ('duration-zero', [('duration_ms: 7000', 'duration_ms: 0')], 'out_of_range', 'duration_ms',
     'duration_ms:', 'outside 1..600000 ms', 1),
    ('duration-negative', [('duration_ms: 7000', 'duration_ms: -1')], 'out_of_range',
     'duration_ms', 'duration_ms:', '-1 is outside 1..600000 ms', 1),
    ('duration-long', [('duration_ms: 7000', 'duration_ms: 600001')], 'out_of_range',
     'duration_ms', 'duration_ms:', '600001 is outside', 1),
    # The most digits int() reads (one more: STRICT) is a number like any other.
    ('duration-4300-digits', [('duration_ms: 7000', 'duration_ms: 1' + '0' * 4299)],
     'out_of_range', 'duration_ms', 'duration_ms:', 'is outside 1..600000 ms', 1),
    ('time-negative', [('t_ms: 0\n', 't_ms: -1\n')], 'out_of_range', 'at[0].t_ms',
     't_ms: -1', 'time starts at 0', 1),
    ('time-at-end', [('t_ms: 6000\n', 't_ms: 7000\n')], 'out_of_range', 'at[6].t_ms',
     't_ms: 7000', 'is not before duration_ms (7000); the last sample is at 6999', 1),
    ('pedal-over', [('accelerator: 1.0', 'accelerator: 1.5')], 'out_of_range',
     'at[5].accelerator', 'accelerator: 1.5',
     '1.5 is outside 0..1 (a fraction of pedal travel: 0.5 is half pedal)', 1),
    ('pedal-under', [('accelerator: 1.0', 'accelerator: -0.1')], 'out_of_range',
     'at[5].accelerator', 'accelerator: -0.1', '-0.1 is outside 0..1', 1),
    ('pedal-under-without-a-leading-zero', [('accelerator: 1.0', 'accelerator: -.5')],
     'out_of_range', 'at[5].accelerator', 'accelerator: -.5', '-0.5 is outside 0..1', 1),
    ('pedal-nan', [('accelerator: 1.0', 'accelerator: .nan')], 'out_of_range',
     'at[5].accelerator', 'accelerator: .nan', '.nan is outside 0..1', 1),
    ('pedal-inf', [('accelerator: 1.0', 'accelerator: .inf')], 'out_of_range',
     'at[5].accelerator', 'accelerator: .inf', '.inf is outside 0..1', 1),
    ('brake-over', [('brake_psi: 350.0', 'brake_psi: 3000.1')], 'out_of_range',
     'at[0].brake_psi', 'brake_psi: 3000.1', '3000.1 is outside 0..3000', 1),
    ('loop-over', [('shutdown_loop_v: 24.0', 'shutdown_loop_v: 27.5')], 'out_of_range',
     'at[0].shutdown_loop_v', 'shutdown_loop_v: 27.5', '27.5 is outside 0..27', 1),
    ('steering-right', [('steering_deg: 0.0', 'steering_deg: 181')], 'out_of_range',
     'at[0].steering_deg', 'steering_deg: 181', '181 is outside -180..180', 1),
    ('steering-left', [('steering_deg: 0.0', 'steering_deg: -181')], 'out_of_range',
     'at[0].steering_deg', 'steering_deg: -181', '-181 is outside -180..180', 1),
    ('soc-over', [('soc_start: 0.9', 'soc_start: 1.2')], 'out_of_range', 'setup.soc_start',
     'soc_start: 1.2', '1.2 is outside 0..1', 1),
    ('soc-under', [('soc_start: 0.9', 'soc_start: -0.1')], 'out_of_range', 'setup.soc_start',
     'soc_start: -0.1', '-0.1 is outside 0..1', 1),
    ('soc-over-on-the-next-line', [('soc_start: 0.9', 'soc_start:\n    1.5')], 'out_of_range',
     'setup.soc_start', ('soc_start:', 1), '1.5 is outside 0..1', 1),
    ('pedal-over-on-the-next-line', [('accelerator: 1.0', 'accelerator:\n      1.5')],
     'out_of_range', 'at[5].accelerator', '      1.5', '1.5 is outside 0..1', 1),
    # A value is reported at its own line, whatever the line endings and
    # however the block or the ramp is written.
    ('crlf-line-endings', [lambda text: text.replace('\n', '\r\n'),
                           ('accelerator: 1.0', 'accelerator: 1.5')], 'out_of_range',
     'at[5].accelerator', 'accelerator: 1.5', '1.5 is outside 0..1', 1),
    ('flow-block-across-lines', [(PRECHARGE_BLOCK, '  - {t_ms: 200, mark: precharge, '
                                                   'hvc_state: PRECHARGING,\n'
                                                   '     accelerator: 1.5}\n')],
     'out_of_range', 'at[1].accelerator', 'accelerator: 1.5', '1.5 is outside 0..1', 1),
    ('flow-setup-value', [(SETUP, 'setup: {event_mode: UNASSIGNED, soc_start: 1.2}\n')],
     'out_of_range', 'setup.soc_start', 'soc_start: 1.2', '1.2 is outside 0..1', 1),
    ('ramp-to-over', [('accelerator: 1.0', 'accelerator: {to: 1.5, ramp_ms: 300}')],
     'out_of_range', 'at[5].accelerator.to', 'to: 1.5', '1.5 is outside 0..1', 1),
    ('block-ramp-to-over', [('accelerator: 1.0',
                             'accelerator:\n      to: 1.5\n      ramp_ms: 300')],
     'out_of_range', 'at[5].accelerator.to', 'to: 1.5', '1.5 is outside 0..1', 1),
    ('block-ramp-to-over-on-the-next-line', [('accelerator: 1.0', 'accelerator:\n      to:\n'
                                                                  '        1.5\n      ramp_ms: 300')],
     'out_of_range', 'at[5].accelerator.to', ('to:', 1), '1.5 is outside 0..1', 1),
    # bad_value
    ('hvc-misspelt', [('hvc_state: ENERGIZED', 'hvc_state: ENERGISED')], 'bad_value',
     'at[2].hvc_state', 'ENERGISED', 'ENERGISED is not an lhre HvcState name; did you mean '
                                    'ENERGIZED?', 1),
    ('hvc-lower-case', [('hvc_state: ENERGIZED', 'hvc_state: energized')], 'bad_value',
     'at[2].hvc_state', 'hvc_state: energized', 'did you mean ENERGIZED? names are upper case', 1),
    ('mode-mixed-case', [('event_mode: UNASSIGNED', 'event_mode: Skidpad')], 'bad_value',
     'setup.event_mode', 'Skidpad', 'did you mean SKIDPAD?', 1),
    ('description-empty', [(LAUNCH.splitlines(True)[6], 'description: ""\n')], 'bad_value',
     'description', 'description: ""', 'is empty', 1),
    ('description-left-empty', [(LAUNCH.splitlines(True)[6], 'description:\n')], 'bad_value',
     'description', 'description:', 'is empty; say in one line what the drive does', 1),
    ('description-lines', [(LAUNCH.splitlines(True)[6], 'description: "one\\ntwo"\n')],
     'bad_value', 'description', 'description: "one', 'is more than one line', 1),
    ('description-block-scalar', [(LAUNCH.splitlines(True)[6], 'description: |\n  one\n  two\n')],
     'bad_value', 'description', 'description: |', 'is more than one line', 1),
    ('description-line-separator', [(LAUNCH.splitlines(True)[6],
                                     'description: "one\\u2028two"\n')],
     'bad_value', 'description', 'description: "one', 'is more than one line', 1),
    ('description-long', [(LAUNCH.splitlines(True)[6], f'description: {"x" * 121}\n')],
     'bad_value', 'description', 'description: x', 'is 121 characters; the limit is 120', 1),
    ('description-long-on-the-next-line', [(LAUNCH.splitlines(True)[6],
                                            f'description:\n  {"x" * 121}\n')],
     'bad_value', 'description', 'x' * 121, 'is 121 characters; the limit is 120', 1),
    ('invisible-in-a-name', [('hvc_state: ENERGIZED', 'hvc_state: ENERGIZED\u200b')], 'bad_value',
     'at[2].hvc_state', 'hvc_state: ENERGIZED', '"ENERGIZED\\u200b" is not an lhre HvcState '
                                               'name; did you mean ENERGIZED? it holds U+200B '
                                               'ZERO WIDTH SPACE, which is not ASCII: retype it',
     1),
    ('lookalike-in-a-name', [('hvc_state: ENERGIZED', 'hvc_state: \u0395NERGIZED')], 'bad_value',
     'at[2].hvc_state', '\u0395', 'did you mean ENERGIZED? it holds U+0395 GREEK CAPITAL '
                                   'LETTER EPSILON, which is not ASCII: retype it', 1),
    ('mark-not-a-slug', [('mark: rtd', 'mark: Loop Open')], 'bad_value', 'at[3].mark',
     'Loop Open', 'Loop Open is not a mark name', 1),
    # The suggestion is itself a mark: never over 40 characters, never
    # ending in a dash, and never the file-name fallback.
    ('mark-41-characters', [('mark: rtd', f'mark: {"a" * 41}')], 'bad_value', 'at[3].mark',
     'mark: aaa', f'at most 40 characters of lowercase words joined by dashes, such as '
                  f'{"a" * 40}', 1),
    ('mark-cut-on-a-dash', [('mark: rtd', f'mark: {"a-" * 20}b')], 'bad_value', 'at[3].mark',
     'mark: a-a', f'such as {"a-" * 19}a', 1),
    ('mark-no-letters', [('mark: rtd', 'mark: "!!!"')], 'bad_value', 'at[3].mark',
     'mark: "!!!"', '"!!!" is not a mark name', 1),
    ('mark-empty', [('mark: rtd', 'mark: ""')], 'bad_value', 'at[3].mark', 'mark: ""',
     'such as loop-open', 1),
    ('mark-digits-suggested-in-quotes', [('mark: rtd', 'mark: 2026!')], 'bad_value',
     'at[3].mark', 'mark: 2026!', 'such as "2026"', 1),
    ('mark-twice', [('mark: brake-off', 'mark: rtd')], 'bad_value', 'at[4].mark',
     ('t_ms: 1500', 1), 'rtd is already the mark of at[3]', 1),
    ('reason-empty', [allow('brake_high: ""')], 'bad_value', 'allow.brake_high',
     'brake_high: ""', 'is empty', 1),
    # time_order
    ('time-equal', [('t_ms: 1500\n', 't_ms: 1000\n')], 'time_order', 'at[4].t_ms',
     ('mark: brake-off', -1), 'two blocks at 1000 ms: merge them', 1),
    ('time-backward', [('t_ms: 1500\n', 't_ms: 900\n')], 'time_order', 'at[4].t_ms',
     't_ms: 900', '900 comes after 1000 (at[3]); keep the blocks in time order', 1),
    # sets_nothing
    ('empty-block', [append_block('t_ms: 6500')], 'sets_nothing', 'at[7]', 't_ms: 6500',
     'sets nothing', 1),
    ('empty-block-under-a-dash-alone', [append('\n  -\n    t_ms: 6500\n')], 'sets_nothing',
     'at[7]', ('t_ms: 6500', -1), 'sets nothing', 1),
    # bad_ramp
    ('ramp-switch', [('rtd_pressed: true', 'rtd_pressed: {to: 1.0, ramp_ms: 10}')],
     'bad_ramp', 'at[3].rtd_pressed', 'rtd_pressed: {',
     'is a switch and cannot ramp; write true or false', 1),
    ('ramp-state', [('hvc_state: ENERGIZED', 'hvc_state: {to: 2.0, ramp_ms: 10}')],
     'bad_ramp', 'at[2].hvc_state', 'hvc_state: {', 'is a state and cannot ramp', 1),
    ('ramp-no-to', [('accelerator: 1.0', 'accelerator: {ramp_ms: 300}')], 'bad_ramp',
     'at[5].accelerator', 'accelerator: {', 'a ramp is {to: X, ramp_ms: N}: it has no to', 1),
    ('ramp-extra-key', [('accelerator: 1.0', 'accelerator: {to: 1.0, ramp_ms: 300, over: 1}')],
     'bad_ramp', 'at[5].accelerator', 'accelerator: {', 'over is not a ramp key', 1),
    ('ramp-zero-ms', [('accelerator: 1.0', 'accelerator: {to: 1.0, ramp_ms: 0}')],
     'bad_ramp', 'at[5].accelerator.ramp_ms', 'accelerator: {', 'at least 1 ms', 1),
    ('block-ramp-zero-ms', [('accelerator: 1.0', 'accelerator:\n      to: 1.0\n      ramp_ms: 0')],
     'bad_ramp', 'at[5].accelerator.ramp_ms', 'ramp_ms: 0', 'at least 1 ms', 1),
    ('ramp-longer-than-any-run', [('accelerator: 1.0', 'accelerator: {to: 1.0, ramp_ms: 700000}')],
     'bad_ramp', 'at[5].accelerator.ramp_ms', 'accelerator: {',
     '700000 is longer than the longest run, 600000 ms', 1),
    # Unbounded, t_ms + ramp_ms would pass the digits Python will print.
    ('ramp-of-4300-digits', [('accelerator: 1.0',
                              'accelerator: {to: 1.0, ramp_ms: ' + '9' * 4300 + '}')],
     'bad_ramp', 'at[5].accelerator.ramp_ms', 'accelerator: {',
     'is longer than the longest run, 600000 ms', 1),
    ('block-ramp-zero-ms-on-the-next-line', [('accelerator: 1.0', 'accelerator:\n      to: 1.0\n'
                                                                  '      ramp_ms:\n        0')],
     'bad_ramp', 'at[5].accelerator.ramp_ms', ('ramp_ms:', 1), 'at least 1 ms', 1),
    # A block-style ramp is at its value, the line under the signal.
    ('block-ramp-no-to', [('accelerator: 1.0', 'accelerator:\n      ramp_ms: 300')], 'bad_ramp',
     'at[5].accelerator', 'ramp_ms: 300', 'a ramp is {to: X, ramp_ms: N}: it has no to', 1),
    ('block-ramp-switch', [('rtd_pressed: true', 'rtd_pressed:\n      to: 1.0\n      ramp_ms: 10')],
     'bad_ramp', 'at[3].rtd_pressed', 'to: 1.0', 'is a switch and cannot ramp', 1),
    ('ramp-without-spaces', [('accelerator: 1.0', 'accelerator: {to:1.0, ramp_ms:200}')],
     'bad_ramp', 'at[5].accelerator', 'accelerator: {',
     'a ramp is {to: X, ramp_ms: N}: to:1.0 is one key, not a key and a value; write a space '
     'after each :', 1),
    # A fractional ramp_ms is the wrong type, like a fractional t_ms.
    ('ramp-fraction-ms', [('accelerator: 1.0', 'accelerator: {to: 1.0, ramp_ms: 1.5}')],
     'wrong_type', 'at[5].accelerator.ramp_ms', 'accelerator: {', 'not whole milliseconds', 1),
    # ramp_overlap, ramp_past_end, unsampled (pass 2)
    ('ramp-overlap', [('accelerator: 1.0', 'accelerator: {to: 1.0, ramp_ms: 300}'),
                      block_before(6000, 't_ms: 2100\naccelerator: 0.5')],
     'ramp_overlap', 'at[6].accelerator', 'accelerator: 0.5',
     'changes at 2100 ms, inside the ramp of at[5], which runs until 2300 ms', 1),
    ('block-ramp-overlap', [('accelerator: 1.0', 'accelerator: {to: 1.0, ramp_ms: 300}'),
                            block_before(6000, 't_ms: 2100\naccelerator:\n  to: 0.5\n'
                                               '  ramp_ms: 100')],
     'ramp_overlap', 'at[6].accelerator', 'to: 0.5', 'changes at 2100 ms, inside the ramp of '
                                                    'at[5], which runs until 2300 ms', 1),
    ('ramp-overlap-by-1-ms', [('accelerator: 1.0', 'accelerator: {to: 1.0, ramp_ms: 100}'),
                              block_before(6000, 't_ms: 2099\naccelerator: 0.5')],
     'ramp_overlap', 'at[6].accelerator', 'accelerator: 0.5',
     'changes at 2099 ms, inside the ramp of at[5], which runs until 2100 ms; start this change '
     'at 2100 or later', 1),
    ('ramp-past-end', [(LAST_PEDAL, '  - t_ms: 6900\n    mark: pedal-off\n'
                                    '    accelerator: {to: 0.0, ramp_ms: 300}')],
     'ramp_past_end', 'at[6].accelerator', 'ramp_ms: 300}',
     'ramps until 7200 ms, past duration_ms (7000)', 1),
    ('block-ramp-past-end', [(LAST_PEDAL, '  - t_ms: 6000\n    mark: pedal-off\n'
                                          '    accelerator:\n      to: 0.0\n      ramp_ms: 1500')],
     'ramp_past_end', 'at[6].accelerator', 'to: 0.0',
     'ramps until 7500 ms, past duration_ms (7000)', 1),
    ('ramp-past-end-by-1-ms', [(LAST_PEDAL, f'{LAST_PEDAL[:-3]}{{to: 0.0, ramp_ms: 1001}}')],
     'ramp_past_end', 'at[6].accelerator', 'ramp_ms: 1001}',
     'ramps until 7001 ms, past duration_ms (7000)', 1),
    # unsampled: a step is seen from T and a ramp from the first sample after
    # T, until the next change, or through it when that change is a ramp.
    ('pulse-between-samples', [block_before(1500, 't_ms: 1001\nrtd_pressed: false')],
     'unsampled', 'at[3].rtd_pressed', 'rtd_pressed: true',
     'is never sampled: the host samples every 3 ms, and the first sample from 1000 ms is at '
     '1002, after the next change at 1001 (at[4]); hold the value through 1002 ms', 1),
    ('pulse-ends-on-the-sample', [block_before(1500, 't_ms: 1002\nrtd_pressed: false')],
     'unsampled', 'at[3].rtd_pressed', 'rtd_pressed: true',
     'is never sampled: the host samples every 3 ms, and the first sample from 1000 ms is at '
     '1002, which already sees the next change (at[4]); hold the value through 1002 ms', 1),
    ('step-before-a-ramp', [block_before(200, 't_ms: 1\nbrake_psi: 400.0',
                                         't_ms: 2\nbrake_psi: {to: 350.0, ramp_ms: 30}')],
     'unsampled', 'at[1].brake_psi', 'brake_psi: 400.0',
     'is never sampled: the host samples every 3 ms, and the first sample from 1 ms is at 3, '
     'after the next change at 2 (at[2]); start that ramp at 3 ms or later: its first sample '
     'still shows the value before it', 1),
    ('ramp-replaced-before-a-sample', [block_before(1000, 't_ms: 999\n'
                                                          'brake_psi: {to: 0.0, ramp_ms: 1}'),
                                       (RTD_BLOCK, RTD_BLOCK + '    brake_psi: 350.0\n')],
     'unsampled', 'at[3].brake_psi', 'ramp_ms: 1}',
     'is never sampled: the host samples every 3 ms, and a ramp starts at the held value, so its '
     'first visible sample is the first one after 999 ms, at 1002, after the next change at '
     '1000 (at[4]); move the next change after 1002 ms', 1),
    ('ramp-replaced-on-its-start-sample', [
        ('brake_psi: 0.0\n', 'brake_psi: 0.0\n    accelerator: {to: 1.0, ramp_ms: 2}\n'),
        block_before(2000, 't_ms: 1502\naccelerator: 0.0')],
     'unsampled', 'at[4].accelerator', 'ramp_ms: 2}',
     'is never sampled: the host samples every 3 ms, and a ramp starts at the held value, so its '
     'first visible sample is the first one after 1500 ms, at 1503, after the next change at '
     '1502 (at[5]); move the next change after 1503 ms', 1),
    ('after-last-sample', [('duration_ms: 7000', 'duration_ms: 7001'),
                           append_block('t_ms: 7000\nbrake_psi: 0.0')],
     'unsampled', 'at[7].brake_psi', ('t_ms: 7000', 1),
     'is never sampled: the host samples every 3 ms and the last sample is at 6999 ms; move it '
     'earlier, or make duration_ms at least 7003', 1),
    ('ramp-on-the-last-sample', [append_block('t_ms: 6999\nbrake_psi: {to: 100.0, ramp_ms: 1}')],
     'unsampled', 'at[7].brake_psi', 'ramp_ms: 1}',
     'is never sampled: the host samples every 3 ms and the last sample is at 6999 ms, but a ramp '
     'starts at the held value, so its first visible sample is the first one after 6999 ms, at '
     '7002; move it earlier, or make duration_ms at least 7003', 1),
    ('mark-after-last-sample', [('duration_ms: 7000', 'duration_ms: 7001'),
                                append_block('t_ms: 7000\nmark: late')],
     'unsampled', 'at[7].mark', 'mark: late',
     'is never shown: the host samples every 3 ms and the last sample is at 6999 ms; move it '
     'earlier, or make duration_ms at least 7003', 1),
]

def value(sf, signal: str, t_ms: int):
    return sf.timeline.value(signal, t_ms)


def same_drive(sf) -> bool:
    return sf.parameters() == scenario_file.load('launch').parameters()


# The other side of a boundary above, or a file the format allows that no
# shipped file shows: each loads, and the timeline does what the check says.
LOADS = [
    ('pulse-held-for-a-sample', [block_before(1500, 't_ms: 1003\nrtd_pressed: false')],
     lambda sf: value(sf, 'rtd_pressed', 1002) is True and value(sf, 'rtd_pressed', 1005) is False),
    ('step-held-into-a-ramp', [block_before(200, 't_ms: 1\nbrake_psi: 400.0',
                                            't_ms: 3\nbrake_psi: {to: 350.0, ramp_ms: 30}')],
     lambda sf: value(sf, 'brake_psi', 3) == 400.0),
    ('ramp-seen-before-the-next-change', [
        block_before(1000, 't_ms: 998\nbrake_psi: {to: 0.0, ramp_ms: 1}'),
        (RTD_BLOCK, RTD_BLOCK + '    brake_psi: 350.0\n')],
     lambda sf: value(sf, 'brake_psi', 999) == 0.0 and value(sf, 'brake_psi', 1002) == 350.0),
    ('ramp-just-before-the-last-sample', [
        append_block('t_ms: 6998\nbrake_psi: {to: 100.0, ramp_ms: 1}')],
     lambda sf: value(sf, 'brake_psi', 6999) == 100.0),
    ('back-to-back-ramps', [('accelerator: 1.0', 'accelerator: {to: 0.5, ramp_ms: 100}'),
                            block_before(6000, 't_ms: 2100\naccelerator: {to: 1.0, ramp_ms: 100}')],
     lambda sf: value(sf, 'accelerator', 2100) == 0.5 and value(sf, 'accelerator', 2200) == 1.0),
    ('ramp-ends-at-the-end', [(LAST_PEDAL, f'{LAST_PEDAL[:-3]}{{to: 0.0, ramp_ms: 1000}}')],
     lambda sf: 0.0 < value(sf, 'accelerator', 6999) < 0.01 and sf.duration_ms == 7000),
    ('longest-run', [('duration_ms: 7000', 'duration_ms: 600000')],
     lambda sf: sf.duration_ms == 600_000),
    ('mark-40-characters', [('mark: rtd', f'mark: {"a" * 40}')],
     lambda sf: sf.marks[2] == ('a' * 40, 1000, 1002)),
    ('byte-order-mark-inside-a-comment', [('# The start: HV off', '# The start:﻿ HV off')],
     same_drive),
    # The same drive written in the other ways YAML allows.
    ('names-and-marks-in-quotes', [('hvc_state: ENERGIZED', 'hvc_state: "ENERGIZED"'),
                                   ('mark: rtd', "mark: 'rtd'")], same_drive),
    ('a-leading-document-marker', [lambda text: '---\n' + text], same_drive),
    ('a-document-end-marker', [append('...\n')], same_drive),
    ('crlf-line-endings', [lambda text: text.replace('\n', '\r\n')], same_drive),
    ('a-block-in-flow-style', [(PRECHARGE_BLOCK, '  - {t_ms: 200, mark: precharge, '
                                                 'hvc_state: PRECHARGING}\n')], same_drive),
    ('a-dash-on-its-own-line', [('  - t_ms: 200\n', '  -\n    t_ms: 200\n')], same_drive),
    ('setup-in-flow-style', [(SETUP, 'setup: {event_mode: UNASSIGNED, soc_start: 0.9}\n')],
     same_drive),
    ('bool-capitals', [('rtd_pressed: true', 'rtd_pressed: True'),
                       ('contactors_closed: true', 'contactors_closed: TRUE'),
                       ('rtd_pressed: false', 'rtd_pressed: FALSE'),
                       ('contactors_closed: false', 'contactors_closed: False')], same_drive),
    ('a-comment-after-quoted-text', [(LAUNCH.splitlines(True)[6],
                                      f'{LAUNCH.splitlines()[6].replace(": ", ": \"", 1)}" #2\n')],
     same_drive),
    ('a-comment-after-text', [('4 s, coast.\n', '4 s, coast.  # #2 of 4\n'), ('mark: rtd', 'mark: rtd  # on')],
     same_drive),
    ('ramp-in-block-style', [('brake_psi: 0.0', 'brake_psi:\n      to: 0.0\n      ramp_ms: 150')],
     lambda sf: sf.blocks[4].values['brake_psi'] == Ramp(0.0, 150)),
    ('ramp-across-lines', [('brake_psi: 0.0',
                            'brake_psi: {to: 0.0,\n                ramp_ms: 150}')],
     lambda sf: sf.blocks[4].values['brake_psi'] == Ramp(0.0, 150)),
    ('ramp-with-a-trailing-comma', [('brake_psi: 0.0', 'brake_psi: {to: 0.0, ramp_ms: 150, }')],
     lambda sf: sf.blocks[4].values['brake_psi'] == Ramp(0.0, 150)),
    ('fraction-without-a-leading-zero', [('accelerator: 1.0', 'accelerator: .5')],
     lambda sf: value(sf, 'accelerator', 2001) == 0.5),
    ('number-with-an-exponent', [('brake_psi: 350.0', 'brake_psi: 3.5e+2')],
     lambda sf: value(sf, 'brake_psi', 0) == 350.0),
    ('number-with-a-capital-exponent', [('brake_psi: 350.0', 'brake_psi: 3.5E+2')],
     lambda sf: value(sf, 'brake_psi', 0) == 350.0),
    ('fraction-without-digits-after-the-point', [('accelerator: 1.0', 'accelerator: 1.')],
     lambda sf: value(sf, 'accelerator', 2001) == 1.0),
    ('a-time-with-a-plus', [('t_ms: 200\n', 't_ms: +200\n')],
     lambda sf: sf.blocks[1].t_ms == 200 and type(sf.blocks[1].t_ms) is int),
    # Quoted text may hold a tab or a no-break space, which print.
    ('a-tab-and-a-no-break-space-in-text', [(LAUNCH.splitlines(True)[6],
                                             'description: "Power\\tup, 4\\xa0s"\n')],
     lambda sf: sf.description == 'Power\tup, 4\xa0s'),
    ('setup-with-every-key-commented-out', [('  event_mode: UNASSIGNED',
                                             '  # event_mode: UNASSIGNED'),
                                            ('  soc_start: 0.9 ', '  # soc_start: 0.9 ')],
     lambda sf: sf.setup == Setup() and sf.parameters()['setup'] == {}),
    ('folded-description-without-its-line-break',
     [(LAUNCH.splitlines(True)[6], 'description: >-\n  Power up,\n  coast.\n')],
     lambda sf: sf.description == 'Power up, coast.'),
    # The dashes at the margin, as vehicle/vehicle.yml writes its lists.
    ('dashes-at-the-margin', [indentless],
     lambda sf: sf.timeline == scenario_file.loads(LAUNCH.encode(), 'launch').timeline),
    # The hint for mark: rtd #2 (hash-in-a-mark), followed.
    ('a-comment-after-a-mark', [('mark: rtd', 'mark: rtd # 2')],
     lambda sf: [m for m, _, _ in sf.marks][2] == 'rtd'),
    # YAML 1.1's yes, on and dates are text here, so a mark.
    ('yaml-1.1-words-are-text', [('mark: precharge', 'mark: 2026-01-01'),
                                 ('mark: energized', 'mark: on'), ('mark: rtd', 'mark: yes')],
     lambda sf: [m for m, _, _ in sf.marks[:3]] == ['2026-01-01', 'on', 'yes']),
    # true and null are whole words.
    ('words-that-start-with-true-or-null-are-text', [('mark: precharge', 'mark: true-north'),
                                                      ('mark: energized', 'mark: nullable')],
     lambda sf: [m for m, _, _ in sf.marks[:2]] == ['true-north', 'nullable']),
]


@pytest.mark.parametrize('edits, holds', [c[1:] for c in LOADS], ids=[c[0] for c in LOADS])
def test_loads(edits, holds):
    sf = scenario_file.loads(edited(*edits).encode(), 'launch')
    assert holds(sf)


RTD_MARK = line_of(LAUNCH, 'mark: rtd')
DURATION = line_of(LAUNCH, 'duration_ms:')
END = LAUNCH.count('\n')  # the last line

# (id, edits, code, where, line anchor, the whole message): each rule of the
# strict loader (README, Scenarios), so a file means one thing to every YAML
# reader. Text where a number or true/false goes gets the spelling to use.
STRICT = [
    # Plain scalars: only true/false, null, decimal numbers and .inf/.nan
    # are not text.
    ('yes-is-text', [('rtd_pressed: true', 'rtd_pressed: yes')], 'wrong_type',
     'at[3].rtd_pressed', 'rtd_pressed: yes', 'yes is not true or false; write true'),
    ('off-is-text', [('contactors_closed: true', 'contactors_closed: Off')], 'wrong_type',
     'at[2].contactors_closed', 'contactors_closed: Off', 'Off is not true or false; write false'),
    ('a-word-for-a-switch', [('rtd_pressed: true', 'rtd_pressed: pressed')], 'wrong_type',
     'at[3].rtd_pressed', 'rtd_pressed: pressed', 'pressed is not true or false; write true or '
                                                 'false'),
    ('on-is-text', [('rtd_pressed: true', 'rtd_pressed: on')], 'wrong_type', 'at[3].rtd_pressed',
     'rtd_pressed: on', 'on is not true or false; write true'),
    ('y-is-text', [('rtd_pressed: true', 'rtd_pressed: y')], 'wrong_type', 'at[3].rtd_pressed',
     'rtd_pressed: y', 'y is not true or false; write true'),
    ('n-is-text', [('contactors_closed: true', 'contactors_closed: n')], 'wrong_type',
     'at[2].contactors_closed', 'contactors_closed: n', 'n is not true or false; write false'),
    ('a-word-that-starts-with-true', [('rtd_pressed: true', 'rtd_pressed: truely')], 'wrong_type',
     'at[3].rtd_pressed', 'rtd_pressed: truely', 'truely is not true or false; write true or '
                                                 'false'),
    ('mixed-case-is-text', [('rtd_pressed: true', 'rtd_pressed: tRuE')], 'wrong_type',
     'at[3].rtd_pressed', 'rtd_pressed: tRuE', 'tRuE is not true or false; write true'),
    ('underscores-are-text', [('duration_ms: 7000', 'duration_ms: 7_000')], 'wrong_type',
     'duration_ms', 'duration_ms:', '7_000 is not whole milliseconds; write 7000'),
    ('hex-is-text', [('t_ms: 200\n', 't_ms: 0xC8\n')], 'wrong_type', 'at[1].t_ms', 't_ms: 0xC8',
     '0xC8 is not whole milliseconds; write 200'),
    ('octal-is-text', [('t_ms: 200\n', 't_ms: 0o310\n')], 'wrong_type', 'at[1].t_ms',
     't_ms: 0o310', '0o310 is not whole milliseconds; write 200'),
    ('a-date-is-text', [('t_ms: 200\n', 't_ms: 2026-01-01\n')], 'wrong_type', 'at[1].t_ms',
     't_ms: 2026-01-01', '2026-01-01 is not whole milliseconds; write an integer, such as 1500'),
    ('sexagesimal-is-text', [('t_ms: 200\n', 't_ms: 3:20\n')], 'wrong_type', 'at[1].t_ms',
     't_ms: 3:20', '3:20 is not whole milliseconds; write an integer, such as 1500'),
    ('units-are-text', [('accelerator: 1.0', 'accelerator: {to: 0.4, ramp_ms: 300ms}')],
     'wrong_type', 'at[5].accelerator.ramp_ms', 'ramp_ms: 300ms',
     '300ms is not whole milliseconds; units go in a # comment: write 300'),
    ('spaced-units-are-text', [('accelerator: 1.0', 'accelerator: {to: 0.4, ramp_ms: 300 ms}')],
     'wrong_type', 'at[5].accelerator.ramp_ms', 'ramp_ms: 300 ms',
     '300 ms is not whole milliseconds; units go in a # comment: write 300'),
    ('inf-is-text', [('accelerator: 1.0', 'accelerator: -Inf')], 'wrong_type',
     'at[5].accelerator', 'accelerator: -Inf', '-Inf is not a number (a fraction of pedal travel: '
     '0.5 is half pedal); YAML writes .inf and .nan, which are out of range here anyway'),
    ('nan-is-text', [('soc_start: 0.9', 'soc_start: NaN')], 'wrong_type', 'setup.soc_start',
     'soc_start: NaN', 'NaN is not a number (the state of charge as a fraction: 0.9 is 90 %); '
                       'YAML writes .inf and .nan, which are out of range here anyway'),
    ('nan-for-a-name', [('hvc_state: ENERGIZED', 'hvc_state: NaN')], 'bad_value',
     'at[2].hvc_state', 'hvc_state: NaN', 'NaN is not an lhre HvcState name; one of '
                                          f'{", ".join(HVC_STATES)}'),
    ('capitals-are-a-bool', [('accelerator: 1.0', 'accelerator: TRUE')], 'wrong_type',
     'at[5].accelerator', 'accelerator: TRUE', 'true is not a number (a fraction of pedal '
                                               'travel: 0.5 is half pedal)'),
    ('empty-is-null', [('accelerator: 1.0', 'accelerator:')], 'wrong_type', 'at[5].accelerator',
     ('mark: pedal-on', 1), 'null is not a number (a fraction of pedal travel: 0.5 is half '
                            'pedal)'),
    ('tilde-is-null', [('mark: rtd', 'mark: ~')], 'wrong_type', 'at[3].mark', 'mark: ~',
     'null is not a string; a mark is a name, such as loop-open'),
    ('capital-null-is-null', [('hvc_state: ENERGIZED', 'hvc_state: NULL')], 'wrong_type',
     'at[2].hvc_state', 'hvc_state: NULL', 'null is not an lhre HvcState name; write the name, '
                                           f'one of {", ".join(HVC_STATES)}'),
    ('a-number-for-a-mark', [('mark: rtd', 'mark: 2026')], 'wrong_type', 'at[3].mark',
     'mark: 2026', '2026 is not a string; write it in quotes, "2026"'),
    ('dot-inf-is-a-number', [('brake_psi: 350.0', 'brake_psi: -.Inf')], 'out_of_range',
     'at[0].brake_psi', 'brake_psi: -.Inf', '-.inf is outside 0..3000 (line pressure in psi, '
                                            'front and rear alike; the sensors read 0..3000)'),
    ('dot-nan-is-a-number', [('shutdown_loop_v: 24.0', 'shutdown_loop_v: .NaN')], 'out_of_range',
     'at[0].shutdown_loop_v', 'shutdown_loop_v: .NaN', '.nan is outside 0..27 (volts at both '
                                                       'shutdown sense taps: 24.0 is closed, 0.0 '
                                                       'open)'),
    ('capital-dot-inf-is-a-number', [('accelerator: 1.0', 'accelerator: .INF')], 'out_of_range',
     'at[5].accelerator', 'accelerator: .INF', '.inf is outside 0..1 (a fraction of pedal '
                                               'travel: 0.5 is half pedal)'),
    ('capital-dot-nan-is-a-number', [('accelerator: 1.0', 'accelerator: .NAN')], 'out_of_range',
     'at[5].accelerator', 'accelerator: .NAN', '.nan is outside 0..1 (a fraction of pedal '
                                               'travel: 0.5 is half pedal)'),
    # YAML 1.2 core signs .inf, never .nan, and has three spellings of each.
    ('signed-nan-is-text', [('accelerator: 1.0', 'accelerator: -.nan')], 'wrong_type',
     'at[5].accelerator', 'accelerator: -.nan', '-.nan is not a number (a fraction of pedal '
                                                'travel: 0.5 is half pedal); YAML writes .inf and '
                                                '.nan, which are out of range here anyway'),
    ('mixed-case-inf-is-text', [('accelerator: 1.0', 'accelerator: .iNf')], 'wrong_type',
     'at[5].accelerator', 'accelerator: .iNf', '.iNf is not a number (a fraction of pedal '
                                               'travel: 0.5 is half pedal); YAML writes .inf and '
                                               '.nan, which are out of range here anyway'),
    # A leading zero reads as octal to YAML 1.1 and decimal to YAML 1.2.
    ('leading-zero', [('t_ms: 200\n', 't_ms: 0200\n')], 'yaml_syntax', 'at[1].t_ms',
     't_ms: 0200', '0200 has a leading zero, which YAML readers disagree on (to some, 010 is 8); '
                   'write 200'),
    ('signed-leading-zero', [('steering_deg: 0.0', 'steering_deg: -007')], 'yaml_syntax',
     'at[0].steering_deg', 'steering_deg: -007', '-007 has a leading zero, which YAML readers '
                                                 'disagree on (to some, 010 is 8); write -7'),
    ('leading-zero-fraction', [('soc_start: 0.9', 'soc_start: 00.9')], 'yaml_syntax',
     'setup.soc_start', 'soc_start: 00.9', '00.9 has a leading zero, which YAML readers '
                                           'disagree on (to some, 010 is 8); write 0.9'),
    ('plus-leading-zero', [('steering_deg: 0.0', 'steering_deg: +010')], 'yaml_syntax',
     'at[0].steering_deg', 'steering_deg: +010', '+010 has a leading zero, which YAML readers '
                                                 'disagree on (to some, 010 is 8); write +10'),
    # Where text goes, the fix is quotes.
    ('leading-zero-mark', [('mark: precharge', 'mark: 010')], 'yaml_syntax', 'at[1].mark',
     'mark: 010', '010 has a leading zero, which YAML readers disagree on (to some, 010 is 8); '
                  'write it in quotes, "010"'),
    # A quoted scalar is text.
    ('quoted-number-is-text', [('accelerator: 1.0', 'accelerator: "0.5"')], 'wrong_type',
     'at[5].accelerator', 'accelerator: "0.5"', '"0.5" is not a number (a fraction of pedal '
                                                'travel: 0.5 is half pedal); write it without '
                                                'quotes'),
    ('quoted-true-is-text', [('rtd_pressed: true', "rtd_pressed: 'true'")], 'wrong_type',
     'at[3].rtd_pressed', "rtd_pressed: 'true'", '"true" is not true or false; write true'),
    ('quoted-time-is-text', [('t_ms: 1000\n', 't_ms: "1000"\n')], 'wrong_type', 'at[3].t_ms',
     't_ms: "1000"', '"1000" is not whole milliseconds; write it without quotes'),
    # Bare, 00.5 is refused, so the hint is not to drop the quotes.
    ('quoted-leading-zero-is-text', [('accelerator: 1.0', 'accelerator: "00.5"')], 'wrong_type',
     'at[5].accelerator', 'accelerator: "00.5"', '"00.5" is not a number (a fraction of pedal '
                                                 'travel: 0.5 is half pedal)'),
    # A " #" cuts unquoted text short, and yaml.safe_load cuts it too.
    ('hash-in-a-description', [('description: Power up,', 'description: Power up #2,')],
     'yaml_syntax', 'description', 'description:', 'Power up ends at a " #", which starts a '
                                                   'comment; put the whole text in quotes'),
    ('hash-in-a-reason', [allow('brake_high: see issue #42')], 'yaml_syntax', 'allow.brake_high',
     'brake_high:', 'see issue ends at a " #", which starts a comment; put the whole text in '
                    'quotes'),
    ('hash-in-a-mark', [('mark: rtd', 'mark: rtd #2')], 'yaml_syntax', 'at[3].mark', 'mark: rtd',
     'rtd ends at a " #", which starts a comment; a mark has no #, so if the rest is a comment, '
     'write a space after the #'),
    # check prints text: \e[2J would clear the terminal.
    ('escape-in-a-description', [(LAUNCH.splitlines(True)[6], 'description: "Power\\e[2J up"\n')],
     'bad_value', 'description', 'description:', 'holds U+001B, which does not print; delete it'),
    ('invisible-in-a-reason', [allow('brake_high: "on\\u200b purpose"')], 'bad_value',
     'allow.brake_high', 'brake_high:', 'holds U+200B ZERO WIDTH SPACE, which does not print; '
                                        'delete it'),
    # JSON writes an emoji as two escapes; YAML keeps them two halves, which
    # no UTF-8 terminal or file can hold.
    ('surrogate-escapes-in-a-description',
     [(LAUNCH.splitlines(True)[6], 'description: "Launch \\ud83d\\ude80"\n')], 'bad_value',
     'description', 'description:', 'holds U+D83D, half of a UTF-16 pair, which YAML does not '
                                    'join; write the character itself'),
    # A # right after the : leaves no text at all.
    ('hash-starts-a-description', [('description: Power up,', 'description: #2 Power up,')],
     'yaml_syntax', 'description', 'description:', 'is empty because its text starts at a " #", '
                                                   'which starts a comment; put the whole text '
                                                   'in quotes'),
    ('hash-starts-a-reason', [allow('brake_high: #42 needs it')], 'yaml_syntax',
     'allow.brake_high', 'brake_high:', 'is empty because its text starts at a " #", which '
                                        'starts a comment; put the whole text in quotes'),
    # A mark is a name, so a # there is a comment: the mark is missing.
    ('hash-starts-a-mark', [('mark: precharge', 'mark: #1')], 'wrong_type', 'at[1].mark',
     'mark: #1', 'null is not a string; a mark is a name, such as loop-open'),
    ('a-comment-for-a-description', [(LAUNCH.splitlines(True)[6], 'description:  # later\n')],
     'bad_value', 'description', 'description:', 'is empty; say in one line what the drive does'),
    # > and | keep the text's last line break, so it is two lines.
    ('folded-description', [(LAUNCH.splitlines(True)[6],
                             'description: >\n  Power up,\n  coast.\n')], 'yaml_syntax',
     'description', 'description: >', '> keeps the line break at the end of the text; write >- '
                                       'to drop it, or put the text on the line of its key'),
    ('literal-reason', [allow('brake_high: |\n    the point of the drive')], 'yaml_syntax',
     'allow.brake_high', 'brake_high: |', '| keeps the line break at the end of the text; write '
                                          '|- to drop it, or put the text on the line of its '
                                          'key'),
    # A pasted no-break space after a value: YAML strips only spaces and tabs.
    ('no-break-space-after-a-number', [('brake_psi: 0.0', 'brake_psi: 0.0\xa0  # off')],
     'wrong_type', 'at[4].brake_psi', 'brake_psi: 0.0', '"0.0\\xa0" is not a number (line '
     'pressure in psi, front and rear alike; the sensors read 0..3000); it holds U+00A0 '
     'NO-BREAK SPACE, which YAML does not read as a space; delete it'),
    ('no-break-space-after-true', [('rtd_pressed: true', 'rtd_pressed: true\xa0')], 'wrong_type',
     'at[3].rtd_pressed', 'rtd_pressed: true', '"true\\xa0" is not true or false; it holds U+00A0 '
                                               'NO-BREAK SPACE, which YAML does not read as a '
                                               'space; delete it'),
    # A key is plain text.
    ('quoted-key', [('    mark: rtd', '    "mark": rtd')], 'yaml_syntax', 'at[3]', '"mark": rtd',
     'the key "mark" is in quotes; write it bare'),
    ('single-quoted-key', [('duration_ms: 7000', "'duration_ms': 7000")], 'yaml_syntax', '',
     "'duration_ms':", "the key 'duration_ms' is in quotes; write it bare"),
    ('number-key', [('    mark: rtd', '    1000: rtd')], 'yaml_syntax', 'at[3]', '1000: rtd',
     'the key 1000 reads as a number; a key is a name, such as t_ms'),
    ('bool-key', [('  event_mode: UNASSIGNED', '  true: UNASSIGNED')], 'yaml_syntax', 'setup',
     'true: UNASSIGNED', 'the key true reads as true or false; a key is a name, such as t_ms'),
    ('null-key', [('    mark: rtd', '    ~: rtd')], 'yaml_syntax', 'at[3]', '~: rtd',
     'the key ~ reads as null; a key is a name, such as t_ms'),
    ('list-key', [('    mark: rtd', '    [mark, rtd]: rtd')], 'yaml_syntax', 'at[3]',
     '[mark, rtd]: rtd', '[mark, rtd] cannot be a key: a key is a name, such as t_ms'),
    ('explicit-key', [('    mark: rtd', '    ? mark\n    : rtd')], 'yaml_syntax', '', '? mark',
     '? starts an explicit key, which this loader does not read; write key: value'),
    # A key written twice: yaml.safe_load would keep the last.
    ('key-twice-in-a-block', [('mark: rtd\n', 'mark: rtd\n    mark: rtd2\n')], 'yaml_syntax',
     'at[3].mark', 'mark: rtd2', f'is written twice, at lines {RTD_MARK} and {RTD_MARK + 1}; '
                                 'each moment needs its own block'),
    ('value-twice', [('duration_ms: 7000\n', 'duration_ms: 7000\nduration_ms: 6000\n')],
     'yaml_syntax', 'duration_ms', 'duration_ms: 6000',
     f'is written twice, at lines {DURATION} and {DURATION + 1}; keep one'),
    ('mapping-twice', [append('\nsetup:  # again\n  soc_start: 0.5\n')], 'yaml_syntax', 'setup',
     'setup:  # again', f'is written twice, at lines {line_of(LAUNCH, "setup:")} and {END + 2}; '
                        'merge them'),
    ('list-twice', [append('\nat:  # again\n  - t_ms: 6500\n    brake_psi: 0.0\n')], 'yaml_syntax',
     'at', 'at:  # again', f'is written twice, at lines {line_of(LAUNCH, "at:")} and {END + 2}; '
                           'merge them'),
    # Each block is written out.
    ('anchor', [('setup:\n', 'setup: &conditions\n')], 'yaml_syntax', '', 'setup: &',
     '&conditions is an anchor; this loader reads no anchors or aliases, so write each block '
     'out'),
    ('anchor-on-a-value', [('duration_ms: 7000', 'duration_ms: &d 7000')], 'yaml_syntax', '',
     'duration_ms:', '&d is an anchor; this loader reads no anchors or aliases, so write each '
                     'block out'),
    ('anchor-on-a-list', [('\nat:\n', '\nat: &blocks\n')], 'yaml_syntax', '', 'at: &',
     '&blocks is an anchor; this loader reads no anchors or aliases, so write each block out'),
    ('alias', [append('  - *power-up\n')], 'yaml_syntax', '', '*power-up',
     '*power-up is an alias; this loader reads no anchors or aliases, so write the block out'),
    ('merge-key', [('  - t_ms: 200\n', '  - <<: {hvc_state: PRECHARGING}\n    t_ms: 200\n')],
     'yaml_syntax', 'at[1]', '<<:', '<< is a merge key, which this loader does not read; write '
                                    'each key out'),
    ('tag', [('accelerator: 1.0', 'accelerator: !!float 1')], 'yaml_syntax', '',
     'accelerator: !!', '!!float is a tag; this loader reads no tags: write the value plain, and '
                        'text in quotes if it must be'),
    ('str-tag', [('hvc_state: ENERGIZED', 'hvc_state: !!str ENERGIZED')], 'yaml_syntax', '',
     'hvc_state: !!', '!!str is a tag; this loader reads no tags: write the value plain, and '
                      'text in quotes if it must be'),
    ('local-tag', [('setup:\n', 'setup: !conditions\n')], 'yaml_syntax', '', 'setup: !',
     '!conditions is a tag; this loader reads no tags: write the value plain, and text in quotes '
     'if it must be'),
    ('non-specific-tag', [('accelerator: 1.0', 'accelerator: ! 1.0')], 'yaml_syntax', '',
     'accelerator: !', '! is a tag; this loader reads no tags: write the value plain, and text '
                       'in quotes if it must be'),
    # One document (a leading --- is fine: LOADS); its top level is keys.
    ('second-document', [append('---\nschema: lhre.vcu-sil-scenario.v1\n')], 'yaml_syntax', '',
     '---', 'a second document starts here; a scenario file is one document'),
    # %YAML 1.1 would ask for the yes and 010 this loader reads otherwise.
    ('yaml-directive', [lambda text: '%YAML 1.1\n---\n' + text], 'yaml_syntax', '', '%YAML',
     '%YAML is a directive, which this loader does not read; delete the line'),
    ('tag-directive', [lambda text: '%TAG !e! tag:example.com,2026:\n---\n' + text], 'yaml_syntax',
     '', '%TAG', '%TAG is a directive, which this loader does not read; delete the line'),
    ('top-level-list', [lambda text: text[:text.index('schema:')] + '- schema: x\n'],
     'yaml_syntax', '', '- schema', 'the top level is a list, not keys; a scenario file is keys, '
                                    'each at the start of a line, such as schema: '
                                    'lhre.vcu-sil-scenario.v1'),
    # Only an empty or comment-only file is the empty document.
    ('top-level-null', [lambda text: text[:text.index('schema:')] + 'null\n'], 'yaml_syntax', '',
     'null', 'the top level is null, not keys; a scenario file is keys, each at the start of a '
             'line, such as schema: lhre.vcu-sil-scenario.v1'),
    ('top-level-tilde', [lambda text: '--- ~\n'], 'yaml_syntax', '', '--- ~',
     'the top level is null, not keys; a scenario file is keys, each at the start of a line, '
     'such as schema: lhre.vcu-sil-scenario.v1'),
    ('top-level-text', [lambda text: 'schema = "lhre.vcu-sil-scenario.v1"\nduration_ms = 7000\n'],
     'yaml_syntax', '', 'schema =', 'the top level is text, not keys; a scenario file is keys, '
                                    'each at the start of a line, such as schema: '
                                    'lhre.vcu-sil-scenario.v1'),
    ('top-level-number', [lambda text: '7000\n'], 'yaml_syntax', '', '7000',
     'the top level is 7000, not keys; a scenario file is keys, each at the start of a line, '
     'such as schema: lhre.vcu-sil-scenario.v1'),
    # Too deep or too long to read stops the load; nothing crashes.
    ('nested-too-deeply', [('accelerator: 1.0', 'accelerator: ' + '[' * 1200)], 'yaml_syntax',
     '', 'accelerator: [', 'the values are nested more than 100 levels deep'),
    ('integer-of-4301-digits', [('duration_ms: 7000', 'duration_ms: 1' + '0' * 4300)],
     'yaml_syntax', 'duration_ms', 'duration_ms:',
     'an integer of 4301 digits is more than Python reads (4300)'),
    ('negative-integer-of-4301-digits', [('duration_ms: 7000', 'duration_ms: -1' + '0' * 4300)],
     'yaml_syntax', 'duration_ms', 'duration_ms:',
     'an integer of 4301 digits is more than Python reads (4300)'),
]


@pytest.mark.parametrize('edits, code, where, anchor, message', [c[1:] for c in STRICT],
                         ids=[c[0] for c in STRICT])
def test_strict_loading(edits, code, where, anchor, message):
    text = edited(*edits)
    [p] = problems_of(text)
    assert (p.level, p.code, p.where, p.line) == ('error', code, where, line_of(text, anchor))
    assert p.message == message


@pytest.mark.parametrize('text', ['', '# nothing yet\n', '---\n', '--- # a start\n'])
def test_an_empty_file_is_the_empty_document(text):
    assert [(p.code, p.where, p.line) for p in problems_of(text)] == [
        ('missing_key', key, None) for key in ('schema', 'description', 'duration_ms', 'at')]


def test_values_nest_to_the_limit():
    """Under the loader's limit a list is a value like any other; at it the
    load stops, well before Python's recursion limit."""
    depth = scenario_file._MAX_DEPTH - 3  # the file, at and the block hold the value
    text = edited(('accelerator: 1.0', 'accelerator: ' + '[' * depth + ']' * depth))
    assert [p.code for p in problems_of(text)] == ['wrong_type']
    deeper = edited(('accelerator: 1.0', 'accelerator: ' + '[' * (depth + 1) + ']' * (depth + 1)))
    assert [p.code for p in problems_of(deeper)] == ['yaml_syntax']


# (value, as messages write it): the way YAML writes it, in double quotes
# only where, written bare, it would read as something else.
WRITTEN = [
    (True, 'true'), (False, 'false'), (None, 'null'), (7000, '7000'), (0.5, '0.5'),
    (float('inf'), '.inf'), (-float('inf'), '-.inf'), (float('nan'), '.nan'),
    ('ENERGIZED', 'ENERGIZED'), ('Loop Open', 'Loop Open'), ('yes', 'yes'), ('07:00', '07:00'),
    ('say "hi"', 'say "hi"'), ('true', '"true"'), ('Null', '"Null"'), ('7000', '"7000"'),
    ('010', '"010"'), ('.5', '".5"'), ('', '""'), (' x', '" x"'), ('a: b', '"a: b"'),
    ('x # y', '"x # y"'), ('!!!', '"!!!"'), ('- x', '"- x"'), ('one\ntwo', '"one\\ntwo"'),
    ('a: \\', '"a: \\\\"'), ('a\tb', '"a\\tb"'), ('ENERGIZED\r', '"ENERGIZED\\r"'),
    # What does not print is escaped: a message never clears the terminal.
    ('\x1b[2J', '"\\x1b[2J"'), ('\x00', '"\\x00"'), ('4\xa0s', '"4\\xa0s"'),
    ('ENERGIZED\u200b', '"ENERGIZED\\u200b"'), ('\U000e0001', '"\\U000e0001"'),
    ({'to': 1.5, 'ramp_ms': 300}, '{to: 1.5, ramp_ms: 300}'), ([[0.5]], '[[0.5]]'),
]


@pytest.mark.parametrize('value, written', WRITTEN, ids=[repr(w[0]) for w in WRITTEN])
def test_values_in_messages_are_written_as_yaml_writes_them(value, written):
    assert scenario_file._yaml(value) == written


# (id, edits, line anchor, the whole message): yaml_syntax, PyYAML's message
# at the line of the slip, with a hint for the usual ones.
SYNTAX = [
    ('tab-indent', [('    mark: precharge', '\tmark: precharge')], 'mark: precharge',
     "YAML: found character '\\t' that cannot start any token at column 1; YAML indents with "
     'spaces: replace the tab with spaces'),
    ('tab-in-text', [('description: Power up', 'description: Power\tup')], 'description:',
     "YAML: found character '\\t' that cannot start any token at column 19; YAML indents with "
     'spaces: replace the tab with spaces'),
    ('colon-in-text', [('description: Power up,', 'description: Power up: then')],
     'description:', "YAML: mapping values are not allowed here at column 22; a ': ' inside "
                     'text starts a key: put the text in quotes'),
    ('colon-in-a-reason', [allow('brake_high: high: on purpose')], 'brake_high:',
     "YAML: mapping values are not allowed here at column 19; a ': ' inside text starts a key: "
     'put the text in quotes'),
    ('no-space-after-a-colon', [('t_ms: 200\n', 't_ms:200\n')], 'mark: precharge',
     'YAML: mapping values are not allowed here at column 9; write a space after the : on line '
     f'{line_of(LAUNCH, ("mark: precharge", -1))}'),
    ('no-space-after-a-top-level-colon', [('duration_ms: 7000', 'duration_ms:7000')],
     'duration_ms:7000', 'YAML: the key at column 1 has no : after it; write a space after the :'),
    ('no-space-after-the-last-colon', [(LAST_PEDAL, LAST_PEDAL.replace(': 0.0', ':0.0'))],
     'accelerator:0.0', 'YAML: the key at column 5 has no : after it; write a space after the :'),
    ('key-without-a-colon', [('    mark: precharge', '    mark precharge')], 'mark precharge',
     'YAML: the key at column 5 has no : after it; write key: value, with a space after the :'),
    ('space-before-a-colon', [('duration_ms: 7000', 'duration_ms :7000')], 'duration_ms :7000',
     'YAML: the key at column 1 has no : after it; write key: value, with a space after the :'),
    ('crlf-key-without-a-colon', [lambda text: text.replace('\n', '\r\n'),
                                  ('    mark: precharge', '    mark precharge')], 'mark precharge',
     'YAML: the key at column 5 has no : after it; write key: value, with a space after the :'),
    ('ramp-unclosed', [('accelerator: 1.0', 'accelerator: {to: 0.4, ramp_ms: 300')],
     'ramp_ms: 300', 'YAML: the { at column 18 is not closed on its line; close it with } on the '
                     'same line'),
    ('ramp-closed-in-a-comment', [('accelerator: 1.0',
                                   'accelerator: {to: 0.4, ramp_ms: 300  # 0.3 s}')],
     'ramp_ms: 300', 'YAML: the { at column 18 is not closed on its line; a # after a space '
                     'starts a comment, which hides the rest of the line: close the { before it'),
    ('ramp-missing-comma', [('accelerator: 1.0', 'accelerator: {to: 0.4 ramp_ms: 300}')],
     'ramp_ms: 300', "YAML: expected ',' or '}', but got ':' at column 34; separate the keys "
                     'with a comma: {to: 0.4, ramp_ms: 300}'),
    ('ramp-semicolon', [('accelerator: 1.0', 'accelerator: {to: 0.4; ramp_ms: 300}')],
     'ramp_ms: 300', "YAML: expected ',' or '}', but got ':' at column 35; separate the keys "
                     'with a comma: {to: 0.4, ramp_ms: 300}'),
    ('list-unclosed', [('accelerator: 1.0', 'accelerator: [0.4, 300')], 'accelerator: [',
     'YAML: the [ at column 18 is not closed on its line; close it with ] on the same line'),
    ('ramp-unclosed-with-a-hash-in-a-word', [('accelerator: 1.0',
                                              'accelerator: {to: 0.4, ramp_ms: a#b')],
     'ramp_ms: a#b', 'YAML: the { at column 18 is not closed on its line; close it with } on the '
                     'same line'),
    ('quote-unclosed', [('description: Power up', 'description: "Power up')], 'description:',
     'YAML: the quote at column 14 is never closed; end the text with the same quote'),
    ('single-quote-unclosed', [(LAST_PEDAL, LAST_PEDAL.replace('pedal-off', "'pedal-off"))],
     "'pedal-off", 'YAML: the quote at column 11 is never closed; end the text with the same '
                   'quote'),
    # The next quote closes it, even an apostrophe in a comment, and PyYAML
    # stops there, lines later.
    ('quote-closed-in-a-comment', [('description: Power', "description: 'Power")],
     'description:', 'YAML: the quote at column 14 is not closed on its line; end the text with '
                     'the same quote'),
    ('quote-closed-by-a-later-quote', [('description: Power', 'description: "Power'),
                                       allow('brake_high: "on purpose"')],
     'description:', 'YAML: the quote at column 14 is not closed on its line; end the text with '
                     'the same quote'),
    ('dash-without-a-space', [('  - t_ms: 200\n', '  -t_ms: 200\n')], '-t_ms: 200',
     "YAML: expected <block end>, but found '?' at column 3; a block starts with a dash and a "
     'space: - t_ms: 0'),
    ('two-dashes', [('  - t_ms: 200\n', '  --t_ms: 200\n')], '--t_ms: 200',
     "YAML: expected <block end>, but found '?' at column 3; a block starts with a dash and a "
     'space: - t_ms: 0'),
    ('two-dashes-and-a-space', [('  - t_ms: 200\n', '  -- t_ms: 200\n')], '-- t_ms: 200',
     "YAML: expected <block end>, but found '?' at column 3; a block starts with a dash and a "
     'space: - t_ms: 0'),
    # PyYAML stops a line or two after the slip, which the hint names.
    ('first-dash-without-a-space', [('  - t_ms: 0\n', '  -t_ms: 0\n')], ('-t_ms: 0', 1),
     'YAML: mapping values are not allowed here at column 16; write a space after the - on line '
     f'{line_of(LAUNCH, "- t_ms: 0")}: - t_ms: 0'),
    ('dash-without-a-space-before-a-block', [(LAUNCH[LAUNCH.index('  - t_ms: 0\n'):LAUNCH.index('\n  - t_ms: 200')], '  -t_ms: 0\n')],
     ('mark: precharge', -1), "YAML: expected <block end>, but found '-' at column 3; write a "
                              f'space after the - on line {line_of(LAUNCH, "- t_ms: 0")}: - '
                              't_ms: 0'),
    ('no-space-after-a-colon-above-a-comment', [('  - t_ms: 200\n    mark: precharge',
                                                 '  - t_ms:200\n    # note\n    mark: precharge')],
     'mark: precharge', "YAML: expected <block end>, but found '<block mapping start>' at column "
                        f'5; write a space after the : on line {line_of(LAUNCH, ("mark: precharge", -1))}'),
    ('top-level-key-at-the-dashes', [('duration_ms: 7000\n', ''), append('  duration_ms: 7000\n')],
     'duration_ms: 7000', "YAML: expected <block end>, but found '?' at column 3; top-level keys "
                          'start at the left margin'),
    ('crlf-top-level-key-at-the-dashes', [(SETUP, ''), append('  setup:\n    soc_start: 0.9\n'),
                                          lambda text: text.replace('\n', '\r\n')], 'setup:',
     "YAML: expected <block end>, but found '?' at column 3; top-level keys start at the left "
     'margin'),
    ('block-key-at-the-dashes', [('    mark: precharge', '  mark: precharge')], 'mark: precharge',
     "YAML: expected <block end>, but found '?' at column 3; line a block's keys up with its "
     't_ms, two spaces past the -'),
    ('text-after-a-block', [('    mark: precharge\n', '    mark: precharge\n   "x"\n')], '   "x"',
     "YAML: expected <block end>, but found '<scalar>' at column 4"),
    # A quote closed on its line, or a ramp, is no opener left open.
    ('text-after-a-quote', [('mark: rtd', 'mark: "rtd" x')], 'mark: "rtd"',
     "YAML: expected <block end>, but found '<scalar>' at column 17"),
    ('text-after-a-ramp', [('accelerator: 1.0', 'accelerator: {to: 1.0, ramp_ms: 300} x')],
     'ramp_ms: 300} x', "YAML: expected <block end>, but found '<scalar>' at column 42"),
    ('a-quote-across-lines-is-not-the-slip', [(LAUNCH.splitlines(True)[6],
                                               'description: "Power up,\n  ready"\n'),
                                              ('    mark: precharge', '\tmark: precharge')],
     'mark: precharge', "YAML: found character '\\t' that cannot start any token at column 1; "
                        'YAML indents with spaces: replace the tab with spaces'),
    ('keys-separated-by-a-comma', [('  - t_ms: 1500\n    mark: brake-off\n',
                                    '  - t_ms: 1500, mark: brake-off\n')], 't_ms: 1500,',
     'YAML: mapping values are not allowed here at column 21; a comma separates keys only inside '
     '{ }: put each key on its own line'),
    # In text a key's name is a word: the quotes are the fix there.
    ('colon-after-a-key-name-in-text', [('description: Power up,',
                                         'description: Power up at the mark: then,')],
     'description:', "YAML: mapping values are not allowed here at column 34; a ': ' inside text "
                     'starts a key: put the text in quotes'),
    # Quotes are a fix only where text goes.
    ('dash-after-a-time', [('t_ms: 200\n', 't_ms: - 200\n')], 't_ms: - 200',
     'YAML: sequence entries are not allowed here at column 11'),
    ('keys-on-one-line', [('  - t_ms: 1500\n    mark: brake-off\n',
                           '  - t_ms: 1500 mark: brake-off\n')], 't_ms: 1500 mark',
     'YAML: mapping values are not allowed here at column 20; mark is a key of its own: put each '
     'key on its own line'),
    # PyYAML reads a first key without its : as text, with the next line,
    # and fails a line later.
    ('first-block-key-without-a-colon', [('  - t_ms: 600\n', '  - t_ms 600\n')], 't_ms 600',
     'YAML: the key at column 5 has no : after it; write key: value, with a space after the :'),
    ('first-block-key-without-a-colon-and-a-comment', [('  - t_ms: 600\n',
                                                        '  - t_ms 600  # ready\n')], 't_ms 600',
     'YAML: the key at column 5 has no : after it; write key: value, with a space after the :'),
    ('first-key-without-a-colon', [('schema: lhre', 'schema lhre')], 'schema lhre',
     'YAML: the key at column 1 has no : after it; write key: value, with a space after the :'),
    ('first-setup-key-without-a-colon', [('event_mode: UNASSIGNED', 'event_mode UNASSIGNED')],
     'event_mode UNASSIGNED',
     'YAML: the key at column 3 has no : after it; write key: value, with a space after the :'),
    ('block-without-its-dash', [('  - t_ms: 600\n', '  t_ms: 600\n')], '  t_ms: 600',
     "YAML: expected <block end>, but found '?' at column 3; a block starts with a dash: - t_ms: "
     'N'),
    ('ramp-glued-to-its-key', [('accelerator: 1.0', 'accelerator:{to: 1.0, ramp_ms: 200}')],
     'accelerator:{', 'YAML: mapping values are not allowed here at column 34; write a space after '
                      'accelerator:'),
    ('first-block-on-the-at-line', [('\nat:\n', '\nat: - t_ms: 0\n')], 'at: - t_ms',
     'YAML: sequence entries are not allowed here at column 5; put the first block on its own '
     'line under at:'),
    ('block-key-left-of-its-block', [indentless, ('\n  mark: precharge', '\nmark: precharge')],
     'mark: precharge', "YAML: the key at column 1 is left of its block's other keys; line it up "
                        'with the t_ms above it, at column 3'),
    ('key-indented-less', [('    mark: precharge', '   mark: precharge')], 'mark: precharge',
     "YAML: expected <block end>, but found '<block mapping start>' at column 4; indent this "
     'line like the lines above it'),
    ('key-indented-more', [('    mark: precharge', '      mark: precharge')], 'mark: precharge',
     'YAML: mapping values are not allowed here at column 11; indent this key like the keys '
     'above it'),
    ('reserved-character', [('description: Power', 'description: @Power')], 'description:',
     "YAML: found character '@' that cannot start any token at column 14; text that starts with "
     '@ goes in quotes'),
    ('text-that-starts-with-a-bracket', [('description: Power', 'description: [WIP] Power')],
     'description:', "YAML: expected <block end>, but found '<scalar>' at column 20; text that "
                     'starts with [ goes in quotes'),
    ('text-that-starts-with-a-star', [('description: Power', 'description: *draft* Power')],
     'description:', "YAML: expected alphabetic or numeric character, but found '*' at column 20; "
                     'text that starts with * goes in quotes'),
    ('text-that-starts-with-a-dash', [('description: Power', 'description: - Power')],
     'description:', 'YAML: sequence entries are not allowed here at column 14; text that starts '
                     'with - goes in quotes'),
    ('text-that-starts-with-a-bar', [('description: Power', 'description: | Power')],
     'description:', "YAML: expected a comment or a line break, but found 'P' at column 16; text "
                     'that starts with | goes in quotes'),
    ('text-that-starts-with-a-greater-than', [('description: Power', 'description: > Power')],
     'description:', "YAML: expected a comment or a line break, but found 'P' at column 16; text "
                     'that starts with > goes in quotes'),
    ('backslash-in-double-quotes', [(LAUNCH.splitlines(True)[6], 'description: "C:\\path"\n')],
     'description:', "YAML: found unknown escape character 'p' at column 18; in double quotes a "
                     '\\ starts an escape: write \\\\ for a backslash, or put the text in single '
                     'quotes'),
    ('after-the-document-end', [append('...\nexpect: 1\n')], 'expect: 1',
     "YAML: expected '<document start>', but found '<block mapping start>' at column 1"),
    ('control-character', [('description: Power', 'description: Pow\x07er')], 'description:',
     'U+0007 is a character YAML does not allow; delete it'),
    # A no-break space or an invisible character looks like YAML's space.
    ('no-break-space-after-a-colon', [('accelerator: 1.0', 'accelerator:\xa01.0')],
     'accelerator:\xa0', 'YAML: column 17 holds U+00A0 NO-BREAK SPACE, which YAML does not read '
                         'as a space; retype it as a space'),
    ('no-break-space-in-the-indent', [('    accelerator: 1.0', '\xa0   accelerator: 1.0')],
     '\xa0', 'YAML: column 1 holds U+00A0 NO-BREAK SPACE, which YAML does not read as a space; '
             'retype it as a space'),
    ('byte-order-mark-starts-a-line', [('# Run conditions.', '\ufeff# Run conditions.')],
     '# Run conditions.', 'YAML: column 1 holds U+FEFF ZERO WIDTH NO-BREAK SPACE, which does not '
                          'show; delete it'),
    ('byte-order-mark', [lambda text: '﻿' + text], '# The reference drive',
     'the file starts with a byte-order mark (some Windows editors add one); save it as UTF-8 '
     'without a BOM'),
    ('not-utf-8', [('Power up, ready', 'Power\udcff up, ready')], 'description:',
     'byte 393 is not UTF-8 text; save the file as UTF-8'),
    # PyYAML would end a line there, so every later line would be one off and
    # the rest of the comment would be read as YAML.
    ('line-separator-in-a-comment', [('# The start: HV off', '# The start:  HV off')],
     '# The start:', 'U+2028 ends a line for some YAML readers and not others; delete it'),
    ('next-line-in-a-comment', [('# The start: HV off', '# The start:\x85 HV off')],
     '# The start:', 'U+0085 ends a line for some YAML readers and not others; delete it'),
    ('paragraph-separator-in-a-comment', [('# The start: HV off', '# The start:\u2029 HV off')],
     '# The start:', 'U+2029 ends a line for some YAML readers and not others; delete it'),
    ('carriage-return-in-a-comment', [('# Run conditions.', '# Run conditions.\r')],
     '# Run conditions.', 'a carriage return without a line feed ends a line for YAML and not '
                          'for git or grep; save the file with LF line endings'),
    # Old Mac line endings: one line to git and grep, so the first CR.
    ('carriage-returns-only', [lambda text: text.replace('\n', '\r')], '# The reference drive',
     'a carriage return without a line feed ends a line for YAML and not for git or grep; save '
     'the file with LF line endings'),
]


@pytest.mark.parametrize('edits, anchor, message', [c[1:] for c in SYNTAX],
                         ids=[c[0] for c in SYNTAX])
def test_syntax_hints(edits, anchor, message):
    text = edited(*edits)
    [p] = problems_of(text)
    assert (p.level, p.code, p.where, p.line) == ('error', 'yaml_syntax', '', line_of(text, anchor))
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
    assert codes == {'not_found', 'bad_name', 'yaml_syntax', 'wrong_schema', 'missing_key',
                     'unknown_key', 'wrong_type', 'out_of_range', 'bad_value', 'time_order',
                     'sets_nothing', 'bad_ramp', 'ramp_overlap', 'ramp_past_end', 'unsampled'}


def test_a_missing_key_indented_under_another_says_where_it_went():
    text = edited(('duration_ms: 7000\n', ''), (*AFTER_SETUP,
                                                AFTER_SETUP[0] + '  duration_ms: 7000\n'))
    missing = [p for p in problems_of(text) if p.code == 'missing_key']
    assert [p.where for p in missing] == ['duration_ms']
    assert missing[0].message == ('is missing; the duration_ms indented under setup counts as '
                                  'part of it: top-level keys are not indented')


def test_all_problems_are_reported_at_once():
    text = edited((LAUNCH.splitlines(True)[6], 'description: 5\n'), top('expect: 1'),
                  ('accelerator: 1.0', 'accelerator: 1.5'))
    problems = problems_of(text)
    assert [p.code for p in problems] == ['wrong_type', 'unknown_key', 'out_of_range']
    assert [p.line for p in problems] == sorted(p.line for p in problems)


def test_every_strict_loading_problem_is_reported_at_once():
    """The loader's own refusals all come together, and no check runs on a
    file it refused."""
    text = edited(('t_ms: 200\n', 't_ms: 0200\n'), ('mark: rtd\n', 'mark: rtd\n    mark: x\n'),
                  ('accelerator: 1.0', 'accelerator: 1.5'))
    assert [(p.code, p.where) for p in problems_of(text)] == [
        ('yaml_syntax', 'at[1].t_ms'), ('yaml_syntax', 'at[3].mark')]


def test_a_key_written_again_names_its_first_line_and_is_not_read():
    text = edited(('duration_ms: 7000\n', 'duration_ms: 7000\nduration_ms: 6000\n'
                                          'duration_ms: 5000\n'))
    assert [p.message for p in problems_of(text)] == [
        f'is written twice, at lines {DURATION} and {DURATION + n}; keep one' for n in (1, 2)]
    again = edited(append('\nsetup:  # again\n  soc_start: 00.5\n'))
    assert [(p.where, p.line) for p in problems_of(again)] == [('setup', END + 2)]


def test_the_top_level_is_judged_on_what_the_loader_read():
    """A refused key, not a second problem with the list that holds it."""
    [p] = problems_of('- "schema": lhre.vcu-sil-scenario.v1\n')
    assert (p.where, p.line, p.message) == ('[0]', 1, 'the key "schema" is in quotes; write it '
                                                      'bare')


def test_timing_checks_wait_for_a_clean_structure():
    """A ramp overlap after a type error would be noise: only the type error."""
    text = edited(('accelerator: 1.0', 'accelerator: {to: 1.0, ramp_ms: 300}'),
                  block_before(6000, 't_ms: 2100\naccelerator: 0.5'),
                  ('rtd_pressed: true', 'rtd_pressed: 1'))
    assert [p.code for p in problems_of(text)] == ['wrong_type']
    without = text.replace('rtd_pressed: 1', 'rtd_pressed: true')
    assert [p.code for p in problems_of(without)] == ['ramp_overlap']


def test_problem_format():
    text = edited(('accelerator: 1.0', 'accelerator: 1.5'))
    path = SCENARIO_DIR / 'my-test.yml'  # need not exist: only its place in the repo counts
    with pytest.raises(ScenarioError) as caught:
        scenario_file.loads(text.encode(), 'my-test', path=path)
    line = line_of(text, 'accelerator: 1.5')
    assert caught.value.report().splitlines() == [
        f'boards/VCU/Sil/scenarios/my-test.yml:{line}: error: at[5].accelerator: 1.5 is '
        'outside 0..1 (a fraction of pedal travel: 0.5 is half pedal) [out_of_range]',
        'boards/VCU/Sil/scenarios/my-test.yml: 1 error. Nothing ran.']
    lineless = scenario_file.Problem('error', 'missing_key', 'description', 'is missing')
    assert lineless.format('x.yml') == 'x.yml: error: description: is missing [missing_key]'
    with pytest.raises(ScenarioError) as caught:
        scenario_file.loads(text.encode(), 'my-test')
    assert caught.value.display == 'my-test.yml'


@pytest.mark.parametrize('file_name, rename', [('Launch.yml', 'launch.yml'),
                                               ('my_launch.yml', 'my-launch.yml'),
                                               ('launch.yaml', 'launch.yml'),
                                               ('launch.toml', 'launch.yml')])
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
                                        ('launch.yaml', 'rename it to launch.yml'),
                                        ('launch.toml', 'rename it to launch.yml')])
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
                         f'{", ".join(scenario_file.names())}. A path to a .yml file also '
                         'works.')
    missing = tmp_path / 'gone.yml'
    with pytest.raises(ScenarioError) as caught:
        scenario_file.load(str(missing))
    [p] = caught.value.problems
    assert p.code == 'not_found' and p.message == f'no scenario file at {missing}'


@pytest.mark.parametrize('arg', ['launch.yml', 'scenarios/launch', 'sub/LAUNCH.yml',
                                 'lanch.yml'])
def test_lookup_a_missing_path_suggests_the_name(tmp_path, monkeypatch, arg):
    """A path is from the working directory, so launch.yml from anywhere but
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
    ('LAUNCH.yml', 'launch.yml',
     'the file is named launch.yml, and only {arg} is in another case; write {dir}/launch.yml'),
    ('launch.yml', 'Launch.yml', 'a scenario file is named in lowercase words joined by '
                                 'dashes and ends in .yml; rename Launch.yml to launch.yml'),
    ('launch.yml', 'launch.yml', None),
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
    (tmp_path / 'launch.yml').write_text(LAUNCH)
    if not (tmp_path / 'LAUNCH.yml').is_file():
        pytest.skip('a case-sensitive filesystem (Linux): LAUNCH.yml is another file')
    arg = tmp_path / 'LAUNCH.yml'
    with pytest.raises(ScenarioError) as caught:
        scenario_file.load(str(arg))
    [p] = caught.value.problems
    assert (p.code, p.message) == ('bad_name', f'the file is named launch.yml, and only {arg} '
                                               f'is in another case; write {tmp_path}/launch.yml')
    (tmp_path / 'launch.yml').rename(tmp_path / 'Launch.yml')
    with pytest.raises(ScenarioError) as caught:
        scenario_file.load(str(tmp_path / 'launch.yml'))
    [p] = caught.value.problems
    assert (p.code, p.message) == ('bad_name', 'a scenario file is named in lowercase words '
                                               'joined by dashes and ends in .yml; rename '
                                               'Launch.yml to launch.yml')


def test_scenario_paths_are_the_regular_files(tmp_path, monkeypatch):
    for name in ('launch.yml', 'launch-x.yml', 'b.yml', '.hidden.yml', '._launch.yml',
                 'notes.txt', 'old.toml', 'other.yaml'):
        (tmp_path / name).write_text(LAUNCH)
    (tmp_path / 'sub.yml').mkdir()
    (tmp_path / '.#launch.yml').symlink_to('dev@host.4242')  # an Emacs lock: dangling
    monkeypatch.setattr(scenario_file, 'SCENARIO_DIR', tmp_path)
    # In name order: launch before launch-x, though launch-x.yml sorts first.
    assert scenario_file.scenario_paths() == [tmp_path / n for n in ('b.yml', 'launch.yml',
                                                                     'launch-x.yml')]
    assert scenario_file.names() == ['b', 'launch', 'launch-x']
    with pytest.raises(ScenarioError) as caught:
        scenario_file.load('lanch')
    assert caught.value.problems[0].message == (
        "no scenario named 'lanch'; did you mean launch? scenarios/ has: b, launch, launch-x. A "
        'path to a .yml file also works.')
    # .yaml is YAML's other extension, and .toml the format before it.
    assert scenario_file.misnamed_paths() == [tmp_path / 'old.toml', tmp_path / 'other.yaml']
    for name, file in (('other', 'other.yaml'), ('old', 'old.toml')):
        with pytest.raises(ScenarioError) as caught:
            scenario_file.load(name)
        [p] = caught.value.problems
        assert (p.code, p.message) == ('bad_name', f'scenarios/{file} is there, but a scenario '
                                                   f'file ends in .yml; rename it to {name}.yml')


def test_every_mark_suggestion_is_a_mark():
    for bad in ('Loop Open', '', '!!!', '\xe9', '-rtd-', 'a' * 41, 'a-' * 20 + 'b',
                'x' * 39 + '-yz', 'RTD_1', 'my-scenario!', '2026!'):
        text = edited(('mark: rtd', f'mark: {json.dumps(bad)}'))
        [p] = problems_of(text)
        suggested = re.search(r'such as (.*)$', p.message)[1]
        # Written into the file as the message writes it, it loads as a mark.
        sf = scenario_file.loads(text.replace(f'mark: {json.dumps(bad)}',
                                              f'mark: {suggested}').encode(), 'launch')
        mark = sf.marks[2][0]
        assert NAME.fullmatch(mark) and len(mark) <= MAX_MARK, (bad, suggested)


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
    assert sf.display == f'boards/VCU/Sil/scenarios/{name}.yml'


@pytest.mark.parametrize('name', scenario_file.names())
def test_every_file_reads_the_same_to_yaml_safe_load(name):
    """A shipped file means the same to PyYAML's own YAML 1.1 safe_load as
    to this loader, so any YAML tool reads the drive that runs."""
    sf = scenario_file.load(name)
    assert as_safe_load_reads_it(sf) == sf.parameters()


def as_safe_load_reads_it(sf) -> dict:
    """yaml.safe_load's reading, with setup and allow as the loader has
    them when left out or emptied: setup: with its keys commented out is
    null to safe_load and no keys to the loader, the same drive."""
    safe = yaml.safe_load(sf.data)
    return {**safe, 'setup': safe.get('setup') or {}, 'allow': safe.get('allow') or {}}


def test_a_setup_with_its_keys_commented_out_reads_the_same_to_yaml_safe_load():
    sf = scenario_file.loads(edited(('  event_mode: UNASSIGNED', '  # event_mode: UNASSIGNED'),
                                    ('  soc_start: 0.9 ', '  # soc_start: 0.9 ')).encode(),
                             'launch')
    assert yaml.safe_load(sf.data)['setup'] is None
    assert as_safe_load_reads_it(sf) == sf.parameters()


def test_the_four_stage_3_files_are_there():
    # At least: README step 1 adds a file of your own.
    assert set(scenario_file.names()) >= {'brake-over-throttle', 'launch', 'part-throttle',
                                          'shutdown-loop-open'}


@pytest.mark.parametrize('name', scenario_file.names())
def test_every_sample_is_the_ramp_formula_to_the_bit(name):
    """Runs repeat bit for bit only if every sample is exactly
    a + (b - a) * ((t - T) / R), the README's formula: worked out here again
    from the file as yaml.safe_load reads it, and compared with ==."""
    sf = scenario_file.load(name)
    doc = yaml.safe_load(sf.data)
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
    head = f'schema: {scenario_file.SCHEMA}\ndescription: d\nduration_ms: {{}}\nat:\n'
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
                    blocks += f'  - t_ms: {t}\n    brake_psi: ' + (
                        f'{{to: {end}, ramp_ms: {r}}}\n' if r else f'{end}\n')
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
    text = edited(('accelerator: 1.0', 'accelerator: 1'), ('brake_psi: 0.0', 'brake_psi: -0.0'))
    sf = scenario_file.loads(text.encode(), 'launch')
    pedal = sf.blocks[5].values['accelerator']
    assert type(pedal) is float and repr(pedal) == '1.0'
    assert repr(sf.blocks[4].values['brake_psi']) == '0.0'
    at = sf.parameters()['at']
    assert repr(at[5]['accelerator']) == '1.0' and repr(at[4]['brake_psi']) == '0.0'
    assert repr(sf.timeline.at(2000)[0].accelerator) == '1.0'
    # A switch stays a bool, a name stays as written, a ramp keeps its parts.
    ramp = edited(('accelerator: 1.0', 'accelerator: {to: 1, ramp_ms: 300}'))
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
    tl = scenario_file.loads(edited(drop_blocks, append('at:\n  - t_ms: 0\n    mark: start\n'))
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
        text = edited(('hvc_state: ENERGIZED', f'hvc_state: {name}'))
        sf = scenario_file.loads(text.encode(), 'launch')
        assert sf.timeline.at(600)[1].hvc_state == number
        assert sf.parameters()['at'][2]['hvc_state'] == name


def test_names_match_the_spec(can_table_path):
    enums = CanTable(can_table_path).enums
    assert HVC_STATES == {name: int(number) for number, name in enums['HvcState'].items()}
    assert EVENT_MODES == {name: int(number) for number, name in enums['EventMode'].items()}


def test_setup():
    for name, number in EVENT_MODES.items():
        text = edited(('event_mode: UNASSIGNED', f'event_mode: {name}'))
        assert scenario_file.loads(text.encode(), 'launch').setup.event_mode == number
    sf = scenario_file.load('launch')
    assert sf.setup == Setup(event_mode=0, soc_start=0.9)
    assert sf.parameters()['setup'] == {'event_mode': 'UNASSIGNED', 'soc_start': 0.9}
    bare = scenario_file.loads(edited(('setup:\n', ''), ('event_mode: UNASSIGNED', ''),
                                      ('soc_start: 0.9', '')).encode(), 'launch')
    assert bare.setup == Setup(None, None) and bare.parameters()['setup'] == {}
    half = edited(('event_mode: UNASSIGNED', ''))
    sf = scenario_file.loads(half.encode(), 'launch')
    assert sf.setup == Setup(None, 0.9) and sf.parameters()['setup'] == {'soc_start': 0.9}


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
    assert scenario_file.resolve('launch') == SCENARIO_DIR / 'launch.yml'
    (tmp_path / 'sub').mkdir()
    (tmp_path / 'sub' / 'my-test.yml').write_text(LAUNCH)
    (tmp_path / 'here.yml').write_text(LAUNCH)
    monkeypatch.chdir(tmp_path)
    # A path is relative to the working directory; a .yml without a / is a path too.
    assert scenario_file.resolve('sub/my-test.yml') == Path('sub/my-test.yml')
    assert scenario_file.resolve('here.yml') == Path('here.yml')
    assert scenario_file.load('sub/my-test.yml').name == 'my-test'
    with pytest.raises(ScenarioError, match='did you mean launch'):
        scenario_file.resolve('lanch')
    with pytest.raises(ScenarioError, match=re.escape(', '.join(scenario_file.names()))):
        scenario_file.resolve('here')  # a name, not ./here.yml


def test_same_bytes_same_sha256_and_parameters(tmp_path):
    copy = tmp_path / 'launch.yml'
    copy.write_bytes((SCENARIO_DIR / 'launch.yml').read_bytes())
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
    assert inside.display == inside.repo_path == 'boards/VCU/Sil/scenarios/launch.yml'
    outside = tmp_path / 'mine.yml'
    outside.write_text(LAUNCH)
    sf = scenario_file.load(str(outside))
    assert sf.display == str(outside) and sf.repo_path is None
    assert sf.path == outside.resolve()
    # Another --repo: the same file is outside it.
    elsewhere = scenario_file.load('launch', repo=tmp_path)
    assert elsewhere.repo_path is None and elsewhere.display == str(SCENARIO_DIR / 'launch.yml')


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
