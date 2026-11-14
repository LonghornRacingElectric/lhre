"""The expect: and waive: sections of a scenario file: every rule the loader
holds them to, with its code, place and line, the windows it resolves to
the run's 3 ms samples, and every verb judged on a run at its boundaries.
The runs are synthetic rows, one per sample from 0, so a test can put the
change on the tick either side of an edge; no library or FMU is needed."""

import json
import math
import re

import pytest
from test_scenario_file import LAUNCH, as_safe_load_reads_it, line_of, problems_of

from vcusil import expectations, scenario_file
from vcusil.firmware import VCU_FAULTS, VCU_STATES
from vcusil.scenario_file import (DEFAULT_WITHIN_MS, INVARIANTS, NOT_MODELLED, VERBS, WARNINGS,
                                  WINDOW_KEYS, Condition)

DURATION = 7000  # launch.yml's: samples 0 .. 6999 ms
STEPS = 2334
# launch.yml's marks: rtd is at 1000 ms, first sampled at 1002.
MARKS = 'precharge, energized, rtd, brake-off, pedal-on, pedal-off'
END = LAUNCH.count('\n')  # its last line: waive: goes two below


def item(check: str, *window: str) -> str:
    """One expect: item: its check on the dash line, then its window keys."""
    return f'  - {check}\n' + ''.join(f'    {key}\n' for key in window)


def expecting(*items: str, blocks: str = '', base: str = LAUNCH) -> str:
    """launch.yml with blocks added to the end of its at: list, then an
    expect: section of items."""
    return base + blocks + '\nexpect:\n' + ''.join(items)


def block(t_ms: int, *keys: str) -> str:
    return f'\n  - t_ms: {t_ms}\n' + ''.join(f'    {key}\n' for key in keys)


def waiving(*lines: str) -> str:
    return LAUNCH + '\nwaive:\n' + ''.join(f'  {line}\n' for line in lines)


def loaded(text: str) -> scenario_file.ScenarioFile:
    return scenario_file.loads(text.encode(), 'launch')


def only(*items: str, blocks: str = '') -> scenario_file.Expectation:
    [e] = loaded(expecting(*items, blocks=blocks)).expectations
    return e


TORQUE = 'the torque the VCU sent, INVERTER_TORQUE_COMMAND torque_request (N.m)'
STATE_NAMES = 'INIT, IDLE, DRIVE, FAULT, PRECHARGE, TS_ACTIVE, RTD_WAIT, BSPD_TRIPPED'
BSPD = ('BSPD_TRIPPED never happens here: the rig does not model the BSPD latch (README, Not '
        'modelled); the bspd_not_tripped invariant stands in for it')
SIGNAL_TAKES = ('a signal takes one of at_most, at_least, between, such as {signal: torque_nm, '
                'at_most: 0.0}')
NOT_A_MARK = 'is not a mark; write the mark of a block under at:, or leave '
NO_DEFAULT = ('is missing, and the block of {} sets no shutdown_loop_v, rtd_pressed, hvc_state or '
              'contactors_closed, whose delay is known; give the deadline in ms after the mark '
              '(accelerator and brake_psi pass through filters, so their delay depends on the '
              'size of the change)')
AN_ITEM = 'an expectation is a check and its condition, such as - reach: {state: DRIVE}'
STATES_EXAMPLE = ("write the run's states in order, such as states: [IDLE, PRECHARGE, TS_ACTIVE, "
                  'RTD_WAIT, DRIVE]')
NO_MARKS = LAUNCH.replace('    mark: ', '    # mark: ')
REACH = 'reach: {state: DRIVE}'
WITHIN = 'within_ms: 30'

# (id, file, code, where, line anchor, message): each file has that one problem.
PROBLEMS = [
    # The section and its items.
    ('expect-a-mapping', LAUNCH + '\nexpect:\n  never: {fault: any}\n', 'wrong_type', 'expect',
     '  never: {fault: any}', 'is a single mapping; write each expectation as a list item, '
                              'such as - reach: {state: DRIVE}'),
    ('expect-a-flow-mapping', LAUNCH + '\nexpect: {never: {fault: any}}\n', 'wrong_type',
     'expect', 'expect: {', 'is a single mapping; write each expectation as a list item, such '
                           'as - reach: {state: DRIVE}'),
    ('expect-a-scalar', LAUNCH + '\nexpect: never\n', 'wrong_type', 'expect', 'expect: never',
     'never is not a list of expectations; write expect:, then each one as a list item, such '
     'as - reach: {state: DRIVE}'),
    ('item-a-number', expecting('  - 5\n'), 'wrong_type', 'expect[0]', '  - 5',
     f'5 is not an expectation; {AN_ITEM}'),
    ('item-glued-to-its-key', expecting('  - from:rtd\n'), 'wrong_type', 'expect[0]',
     '  - from:rtd', 'from:rtd is not an expectation; write a space after the :'),
    ('item-a-list', expecting('  - [never, {fault: any}]\n'), 'wrong_type', 'expect[0]',
     '  - [never', f'[never, {{fault: any}}] is not an expectation; {AN_ITEM}'),
    ('no-verb', expecting(item('from: rtd', WITHIN)), 'missing_key', 'expect[0]', '  - from: rtd',
     'has no check; give it one of reach, hold, never, peak, low, states'),
    ('two-verbs', expecting(item(REACH, 'hold: {state: DRIVE}', WITHIN)), 'bad_value',
     'expect[0]', '    hold:', 'has both reach and hold; give each check its own item'),
    ('item-key-typo', expecting(item('never: {fault: any}', 'frm: rtd')), 'unknown_key',
     'expect[0].frm', 'frm: rtd', 'is not an expectation key; did you mean from?'),
    ('within-typo', expecting(item(REACH, 'within: 30')), 'unknown_key', 'expect[0].within',
     'within: 30', 'is not an expectation key; did you mean within_ms?'),
    ('condition-key-on-the-item', expecting(item('never: {fault: any}', 'state: DRIVE')),
     'unknown_key', 'expect[0].state', '    state: DRIVE', 'is not an expectation key; it goes '
                                                         'inside the condition, such as never: '
                                                         '{state: DRIVE}'),
    ('block-key-on-the-item', expecting(item('never: {fault: any}', 't_ms: 5')), 'unknown_key',
     'expect[0].t_ms', 't_ms: 5', 'is not an expectation key; a signal or a mark goes in a '
                                  'block under at:'),
    ('top-level-key-indented-into-an-item', expecting(item('never: {fault: any}', 'waive: x')),
     'unknown_key', 'expect[0].waive', 'waive: x', 'is not an expectation key; top-level keys '
                                                   'are not indented, so this one counts as part '
                                                   'of expect[0]'),
    # Conditions.
    ('condition-a-state-name', expecting(item('never: DRIVE')), 'wrong_type', 'expect[0].never',
     '  - never: DRIVE', 'DRIVE is not a condition; write never: {state: DRIVE}'),
    ('condition-text-for-peak', expecting(item('peak: torque_nm')), 'wrong_type',
     'expect[0].peak', '  - peak:', 'torque_nm is not a condition; write peak: {signal: '
                                    'speed_m_s, at_least: 25.0}'),
    ('condition-empty', expecting(item('never: {}')), 'missing_key', 'expect[0].never',
     '  - never: {}', 'names nothing to look for; give it state:, fault: or signal:, such as '
                      '{state: DRIVE}'),
    ('state-and-fault', expecting(item('never: {state: DRIVE, fault: any}')), 'bad_value',
     'expect[0].never', '  - never:', 'has both state and fault; give each its own item'),
    ('condition-unknown-key', expecting(item('never: {state: DRIVE, ms: 3}')), 'unknown_key',
     'expect[0].never.ms', '  - never:', 'is not a condition key'),
    ('state-lower-case', expecting(item('reach: {state: drive}', WITHIN)), 'bad_value',
     'expect[0].reach.state', '  - reach:', 'drive is not an lhre VcuStateMachine name; did you '
                                            'mean DRIVE? names are upper case'),
    ('state-typo', expecting(item('reach: {state: DRVE}', WITHIN)), 'bad_value',
     'expect[0].reach.state', '  - reach:', 'DRVE is not an lhre VcuStateMachine name; did you '
                                            'mean DRIVE?'),
    ('state-a-number', expecting(item('never: {state: 2}')), 'wrong_type',
     'expect[0].never.state', '  - never:', '2 is not an lhre VcuStateMachine name; write the '
                                            'name: state: DRIVE'),
    ('state-a-bool', expecting(item('never: {state: true}')), 'wrong_type',
     'expect[0].never.state', '  - never:', 'true is not an lhre VcuStateMachine name; write the '
                                            f'name, one of {STATE_NAMES}'),
    ('state-any', expecting(item('reach: {state: any}', WITHIN)), 'bad_value',
     'expect[0].reach.state', '  - reach:', 'any is not an lhre VcuStateMachine name; one of '
                                            f'{STATE_NAMES}'),
    ('state-a-fault', expecting(item('never: {state: SHUTDOWN_OPEN}')), 'bad_value',
     'expect[0].never.state', '  - never:', 'SHUTDOWN_OPEN is not an lhre VcuStateMachine name; '
                                            f'one of {STATE_NAMES}'),
    ('state-bspd', expecting(item('reach: {state: BSPD_TRIPPED}', WITHIN)), 'not_modelled',
     'expect[0].reach.state', '  - reach:', BSPD),
    ('fault-bspd', expecting(item('never: {fault: BSPD_TRIPPED}')), 'not_modelled',
     'expect[0].never.fault', '  - never:', BSPD),
    ('fault-none', expecting(item('reach: {fault: NONE}', WITHIN)), 'bad_value',
     'expect[0].reach.fault', '  - reach:', 'NONE is no fault; for no faults write never: '
                                            '{fault: any}'),
    ('fault-lower-case', expecting(item('never: {fault: Shutdown_Open}')), 'bad_value',
     'expect[0].never.fault', '  - never:', 'Shutdown_Open is not an lhre VcuFaultCode name; '
                                            'did you mean SHUTDOWN_OPEN? names are upper case'),
    ('fault-typo', expecting(item('never: {fault: SHUTDOWN_OPN}')), 'bad_value',
     'expect[0].never.fault', '  - never:', 'SHUTDOWN_OPN is not an lhre VcuFaultCode name; '
                                            'did you mean SHUTDOWN_OPEN?'),
    ('fault-a-number', expecting(item('never: {fault: 9}')), 'wrong_type',
     'expect[0].never.fault', '  - never:', '9 is not an lhre VcuFaultCode name; write the name: '
                                            'fault: SHUTDOWN_OPEN'),
    ('fault-a-state', expecting(item('never: {fault: DRIVE}')), 'bad_value',
     'expect[0].never.fault', '  - never:', 'DRIVE is not an lhre VcuFaultCode name; one of '
                                            f'{", ".join(VCU_FAULTS)}'),
    ('comparator-with-a-state', expecting(item('never: {state: DRIVE, at_most: 3}')),
     'bad_value', 'expect[0].never.at_most', '  - never:', 'a state is there or not: at_most '
                                                           'goes with a signal'),
    ('comparator-with-any-fault', expecting(item('never: {fault: any, at_least: 1}')),
     'bad_value', 'expect[0].never.at_least', '  - never:', 'a fault is there or not: at_least '
                                                            'goes with a signal'),
    ('signal-without-a-comparator', expecting(item('never: {signal: torque_nm}')),
     'missing_key', 'expect[0].never', '  - never:', SIGNAL_TAKES),
    ('signal-with-two-comparators',
     expecting(item('never: {signal: torque_nm, at_most: 1, at_least: 0}')), 'bad_value',
     'expect[0].never', '  - never:', SIGNAL_TAKES),
    ('signal-typo', expecting(item('never: {signal: torque, at_most: 1}')), 'bad_value',
     'expect[0].never.signal', '  - never:', 'torque is not a signal an expectation can judge, '
                                             'which are torque_nm, speed_m_s, dc_power_kw; did '
                                             'you mean torque_nm?'),
    ('signal-a-number', expecting(item('never: {signal: 5, at_most: 1}')), 'bad_value',
     'expect[0].never.signal', '  - never:', '5 is not a signal an expectation can judge, which '
                                             'are torque_nm, speed_m_s, dc_power_kw'),
    ('between-backwards', expecting(item('never: {signal: torque_nm, between: [2, 1]}')),
     'bad_value', 'expect[0].never.between', '  - never:', '2 is above 1; write the low end '
                                                           'first'),
    ('between-one-number', expecting(item('never: {signal: torque_nm, between: [0]}')),
     'wrong_type', 'expect[0].never.between', '  - never:', '[0] is not two numbers; write '
                                                            'between: [low, high]'),
    ('between-three-numbers', expecting(item('never: {signal: torque_nm, between: [1, 2, 3]}')),
     'wrong_type', 'expect[0].never.between', '  - never:', '[1, 2, 3] is not two numbers; '
                                                            'write between: [low, high]'),
    ('between-a-number', expecting(item('never: {signal: torque_nm, between: 5}')),
     'wrong_type', 'expect[0].never.between', '  - never:', '5 is not two numbers; write '
                                                            'between: [low, high]'),
    ('between-nan', expecting(item('never: {signal: torque_nm, between: [0, .nan]}')),
     'wrong_type', 'expect[0].never.between[1]', '  - never:', '.nan is not a finite number '
                                                               f'({TORQUE})'),
    ('between-text', expecting(item('never: {signal: torque_nm, between: [a, 1]}')),
     'wrong_type', 'expect[0].never.between[0]', '  - never:', f'a is not a finite number '
                                                               f'({TORQUE})'),
    ('at-most-inf', expecting(item('never: {signal: torque_nm, at_most: .inf}')), 'wrong_type',
     'expect[0].never.at_most', '  - never:', f'.inf is not a finite number ({TORQUE})'),
    ('at-least-minus-inf', expecting(item('never: {signal: torque_nm, at_least: -.inf}')),
     'wrong_type', 'expect[0].never.at_least', '  - never:', '-.inf is not a finite number '
                                                             f'({TORQUE})'),
    ('at-most-a-bool', expecting(item('never: {signal: torque_nm, at_most: true}')),
     'wrong_type', 'expect[0].never.at_most', '  - never:', f'true is not a finite number '
                                                            f'({TORQUE})'),
    ('at-most-quoted', expecting(item('never: {signal: torque_nm, at_most: "5"}')),
     'wrong_type', 'expect[0].never.at_most', '  - never:', f'"5" is not a finite number '
                                                            f'({TORQUE}); write it without '
                                                            'quotes'),
    ('at-most-hex', expecting(item('never: {signal: torque_nm, at_most: 0x10}')), 'wrong_type',
     'expect[0].never.at_most', '  - never:', f'0x10 is not a finite number ({TORQUE}); write '
                                              '16'),
    ('peak-of-a-state', expecting(item('peak: {state: DRIVE}')), 'bad_value', 'expect[0].peak',
     '  - peak:', "peak judges a signal's highest value; write peak: {signal: speed_m_s, "
                  'at_least: 25.0}'),
    ('low-of-a-fault', expecting(item('low: {fault: any}')), 'bad_value', 'expect[0].low',
     '  - low:', "low judges a signal's lowest value; write low: {signal: speed_m_s, at_least: "
                 '25.0}'),
    # A condition on its own lines: each problem at its own line.
    ('block-condition-name', expecting('  - never:\n      state: drive\n'), 'bad_value',
     'expect[0].never.state', '      state: drive', 'drive is not an lhre VcuStateMachine name; '
                                                    'did you mean DRIVE? names are upper case'),
    ('block-condition-comparator', expecting('  - never:\n      state: DRIVE\n'
                                             '      at_least: 1\n'),
     'bad_value', 'expect[0].never.at_least', '      at_least: 1',
     'a state is there or not: at_least goes with a signal'),
    ('block-condition-bound', expecting('  - never:\n      signal: torque_nm\n'
                                        '      at_most: true\n'),
     'wrong_type', 'expect[0].never.at_most', '      at_most: true',
     f'true is not a finite number ({TORQUE})'),
    # states
    ('states-empty', expecting(item('states: []')), 'wrong_type', 'expect[0].states',
     '  - states: []', f'[] is not a list of states; {STATES_EXAMPLE}'),
    ('states-a-name', expecting(item('states: IDLE')), 'wrong_type', 'expect[0].states',
     '  - states: IDLE', f'IDLE is not a list of states; {STATES_EXAMPLE}'),
    ('states-twice-in-a-row', expecting(item('states: [IDLE, PRECHARGE, PRECHARGE]')),
     'bad_value', 'expect[0].states[2]', '  - states:', 'PRECHARGE twice in a row; states lists '
                                                        'each stretch once, as the summary does'),
    ('states-a-fault', expecting(item('states: [IDLE, SHUTDOWN_OPEN]')), 'bad_value',
     'expect[0].states[1]', '  - states:', 'SHUTDOWN_OPEN is not an lhre VcuStateMachine name; '
                                           f'one of {STATE_NAMES}'),
    ('states-bspd', expecting(item('states: [IDLE, BSPD_TRIPPED]')), 'not_modelled',
     'expect[0].states[1]', '  - states:', BSPD),
    ('states-lower-case', expecting(item('states: [idle]')), 'bad_value', 'expect[0].states[0]',
     '  - states:', 'idle is not an lhre VcuStateMachine name; did you mean IDLE? names are '
                    'upper case'),
    ('states-any', expecting(item('states: [any]')), 'bad_value', 'expect[0].states[0]',
     '  - states:', f'any is not an lhre VcuStateMachine name; one of {STATE_NAMES}'),
    # from and until name marks; there are no window words.
    ('from-start', expecting(item('never: {fault: any}', 'from: start')), 'bad_value',
     'expect[0].from', '    from: start', f'start is not a mark; the marks are {MARKS}'),
    ('until-end', expecting(item('never: {fault: any}', 'until: end')), 'bad_value',
     'expect[0].until', '    until: end', f'end is not a mark; the marks are {MARKS}'),
    ('from-typo', expecting(item('never: {fault: any}', 'from: rtdd')), 'bad_value',
     'expect[0].from', 'from: rtdd', 'rtdd is not a mark; did you mean rtd?'),
    ('until-typo', expecting(item('never: {fault: any}', 'until: pedal-of')), 'bad_value',
     'expect[0].until', 'until: pedal-of', 'pedal-of is not a mark; did you mean pedal-off?'),
    ('from-not-a-mark-name', expecting(item('never: {fault: any}', 'from: Rtd')), 'bad_value',
     'expect[0].from', 'from: Rtd', f'Rtd {NOT_A_MARK}from out to count from the start of the '
                                    'run'),
    ('from-empty-text', expecting(item('never: {fault: any}', "from: ''")), 'bad_value',
     'expect[0].from', "from: ''", f'"" {NOT_A_MARK}from out to count from the start of the run'),
    ('from-null', expecting(item('never: {fault: any}', 'from:')), 'wrong_type',
     'expect[0].from', '    from:', f'null {NOT_A_MARK}from out to count from the start of the '
                                    'run'),
    ('until-a-number', expecting(item('never: {fault: any}', 'until: 5')), 'wrong_type',
     'expect[0].until', 'until: 5', f'5 {NOT_A_MARK}until out to run to the end'),
    ('a-file-with-no-marks', expecting(item('never: {fault: any}', 'from: rtd'), base=NO_MARKS),
     'bad_value', 'expect[0].from', '    from: rtd', 'rtd is not a mark; no block has a mark: '
                                                     'yet; give the block one, such as mark: '
                                                     'loop-open'),
    # Window numbers.
    ('until-and-for', expecting(item('never: {fault: any}', 'until: rtd', 'for_ms: 5')),
     'bad_value', 'expect[0].for_ms', 'for_ms: 5', 'cannot go with until; give the window one '
                                                   'end'),
    ('not-before-after-within',
     expecting(item(REACH, 'from: rtd', 'not_before_ms: 25', 'within_ms: 24')), 'bad_value',
     'expect[0].not_before_ms', 'not_before_ms: 25', '25 is after within_ms (24), so nothing '
                                                     'could pass'),
    ('not-before-after-the-default', expecting(item(REACH, 'from: rtd', 'not_before_ms: 25')),
     'bad_value', 'expect[0].not_before_ms', 'not_before_ms: 25', '25 is after within_ms (24), '
                                                                  'so nothing could pass'),
    ('for-ms-0', expecting(item('never: {fault: any}', 'for_ms: 0')), 'out_of_range',
     'expect[0].for_ms', 'for_ms: 0', '0 is outside 1..600000 ms'),
    ('after-ms-negative', expecting(item('never: {fault: any}', 'after_ms: -1')),
     'out_of_range', 'expect[0].after_ms', 'after_ms: -1', '-1 is outside 0..600000 ms'),
    ('after-ms-past-ten-minutes', expecting(item('never: {fault: any}', 'after_ms: 600001')),
     'out_of_range', 'expect[0].after_ms', 'after_ms: 600001', '600001 is outside 0..600000 ms'),
    ('within-ms-past-ten-minutes', expecting(item(REACH, 'within_ms: 600001')), 'out_of_range',
     'expect[0].within_ms', 'within_ms: 600001', '600001 is outside 0..600000 ms'),
    ('not-before-negative', expecting(item(REACH, WITHIN, 'not_before_ms: -3')), 'out_of_range',
     'expect[0].not_before_ms', 'not_before_ms: -3', '-3 is outside 0..600000 ms'),
    ('after-ms-a-fraction', expecting(item('never: {fault: any}', 'after_ms: 1.5')),
     'wrong_type', 'expect[0].after_ms', 'after_ms: 1.5', '1.5 is not whole milliseconds; write '
                                                          'an integer, such as 1500'),
    ('for-ms-a-bool', expecting(item('never: {fault: any}', 'for_ms: true')), 'wrong_type',
     'expect[0].for_ms', 'for_ms: true', 'true is not whole milliseconds; write an integer, '
                                         'such as 1500'),
    ('within-ms-hex', expecting(item(REACH, 'within_ms: 0x10')), 'wrong_type',
     'expect[0].within_ms', 'within_ms: 0x10', '0x10 is not whole milliseconds; write 16'),
    # Windows resolved to samples (pass 2). launch's last sample is at 6999 ms.
    ('for-ms-past-the-end', expecting(item('never: {fault: any}', 'from: pedal-off',
                                           'for_ms: 1003')),
     'out_of_range', 'expect[0].for_ms', 'for_ms: 1003', 'the window runs until 7003 ms, past '
                                                         'the last sample (6999 ms); shorten '
                                                         'it, or leave for_ms out to run to the '
                                                         'end'),
    ('starts-after-the-last-sample', expecting(item('never: {fault: any}', 'after_ms: 7000')),
     'out_of_range', 'expect[0]', '  - never:', 'its window starts at 7000 ms, after the last '
                                                'sample (6999 ms)'),
    ('starts-after-the-last-sample-from-a-mark',
     expecting(item('hold: {fault: any}', 'from: pedal-off', 'after_ms: 1000')), 'out_of_range',
     'expect[0]', '  - hold:', 'its window starts at 7000 ms, after the last sample (6999 ms)'),
    ('a-window-between-two-samples',
     expecting(item('never: {fault: any}', 'from: rtd', 'after_ms: 1', 'for_ms: 2')),
     'bad_value', 'expect[0]', '  - never:', 'its window, from 1003 ms to 1005 ms, holds no '
                                             'sample; the host samples every 3 ms'),
    ('until-before-from', expecting(item('never: {fault: any}', 'from: brake-off',
                                         'until: rtd')),
     'bad_value', 'expect[0]', '  - never:', 'its window, from 1500 ms to 1002 ms, holds no '
                                             'sample; the host samples every 3 ms'),
    ('until-its-own-from', expecting(item('never: {fault: any}', 'from: rtd', 'until: rtd')),
     'bad_value', 'expect[0]', '  - never:', 'its window, from 1002 ms to 1002 ms, holds no '
                                             'sample; the host samples every 3 ms'),
    ('after-ms-reaches-until', expecting(item('never: {fault: any}', 'from: rtd',
                                              'after_ms: 498', 'until: brake-off')),
     'bad_value', 'expect[0]', '  - never:', 'its window, from 1500 ms to 1500 ms, holds no '
                                             'sample; the host samples every 3 ms'),
    ('reach-due-after-the-last-sample',
     expecting(item(REACH, 'from: pedal-off', 'within_ms: 2000', 'not_before_ms: 1000')),
     'out_of_range', 'expect[0]', '  - reach:', 'it can pass only from 7000 ms, after the last '
                                                'sample (6999 ms)'),
    # reach's default within_ms, from what the anchor's block changes.
    ('no-default-from-the-start', expecting(item(REACH)), 'missing_key', 'expect[0].within_ms',
     '  - reach:', 'is missing, and it counts from the start of the run; give the deadline in '
                   'ms from 0'),
    ('no-default-for-the-brake', expecting(item(REACH, 'from: brake-off')), 'missing_key',
     'expect[0].within_ms', '  - reach:', NO_DEFAULT.format('brake-off')),
    ('no-default-for-the-pedal', expecting(item(REACH, 'from: pedal-on')), 'missing_key',
     'expect[0].within_ms', '  - reach:', NO_DEFAULT.format('pedal-on')),
    ('no-default-for-a-mark-alone',
     expecting(item(REACH, 'from: lap', 'not_before_ms: 3'), blocks=block(6500, 'mark: lap')),
     'missing_key', 'expect[0].within_ms', '  - reach:', NO_DEFAULT.format('lap')),
    # waive
    ('waive-a-scalar', LAUNCH + '\nwaive: x\n', 'wrong_type', 'waive', 'waive: x',
     'x is not a mapping; write waive: on its own line, then one invariant: reason line per '
     'invariant, indented'),
    ('waive-a-list', waiving('- bot_detected'), 'wrong_type', 'waive', '  - bot_detected',
     '[bot_detected] is not a mapping; write waive: on its own line, then one invariant: reason '
     'line per invariant, indented'),
    ('waive-typo', waiving('bot_detect: why'), 'unknown_key', 'waive.bot_detect', 'bot_detect:',
     'is not an invariant; did you mean bot_detected?'),
    ('waive-a-warning-code', waiving('brake_high: why'), 'unknown_key', 'waive.brake_high',
     'brake_high:', 'is not an invariant; brake_high is a warning code: it goes under allow:'),
    ('allow-an-invariant', LAUNCH + '\nallow:\n  bot_detected: why\n', 'unknown_key',
     'allow.bot_detected', 'bot_detected:', 'is not a warning code; bot_detected is an '
                                            'invariant: it goes under waive:'),
    ('waive-a-top-level-key', waiving('expect: why'), 'unknown_key', 'waive.expect',
     '  expect: why', 'is not an invariant; top-level keys are not indented, so this one counts '
                      'as part of waive'),
    ('waive-no-reason', waiving('bot_detected:'), 'bad_value', 'waive.bot_detected',
     'bot_detected:', 'is empty; say why the drive breaks it'),
    ('waive-empty-reason', waiving('bot_detected: ""'), 'bad_value', 'waive.bot_detected',
     'bot_detected:', 'is empty; say why the drive breaks it'),
    ('waive-blank-reason', waiving('bot_detected: "   "'), 'bad_value', 'waive.bot_detected',
     'bot_detected:', 'is empty; say why the drive breaks it'),
    ('waive-a-number', waiving('bot_detected: 5'), 'wrong_type', 'waive.bot_detected',
     'bot_detected:', '5 is not a string; write it in quotes'),
    ('waive-a-bool', waiving('bot_detected: true'), 'wrong_type', 'waive.bot_detected',
     'bot_detected:', 'true is not a string; write it in quotes'),
    ('waive-two-lines', waiving('bot_detected: "one\\ntwo"'), 'bad_value', 'waive.bot_detected',
     'bot_detected:', 'is more than one line; keep it to one and put the rest in # comments'),
    ('waive-a-literal-block', waiving('bot_detected: |-', '  one', '  two'), 'bad_value',
     'waive.bot_detected', 'bot_detected:', 'is more than one line; keep it to one and put the '
                                            'rest in # comments'),
    ('waive-an-escape', waiving('bot_detected: "\\e[2J"'), 'bad_value', 'waive.bot_detected',
     'bot_detected:', 'holds U+001B, which does not print; delete it'),
    ('waive-colon-unquoted', waiving('bot_detected: note: on purpose'), 'yaml_syntax', '',
     'bot_detected:', "YAML: mapping values are not allowed here at column 21; a ': ' inside "
                      'text starts a key: put the text in quotes'),
    ('waive-hash-unspaced', waiving('bot_detected: why #2 of them'), 'yaml_syntax',
     'waive.bot_detected', 'bot_detected:', 'why ends at a " #", which starts a comment; put '
                                            'the whole text in quotes'),
    ('waive-folded-keeps-its-break', waiving('bot_detected: >', '  why'), 'yaml_syntax',
     'waive.bot_detected', 'bot_detected:', '> keeps the line break at the end of the text; '
                                            'write >- to drop it, or put the text on the line '
                                            'of its key'),
    ('waive-twice', waiving('bot_detected: why', 'bot_detected: again'), 'yaml_syntax',
     'waive.bot_detected', 'bot_detected: again',
     f'is written twice, at lines {END + 3} and {END + 4}; keep one'),
]


@pytest.mark.parametrize('text, code, where, anchor, message', [c[1:] for c in PROBLEMS],
                         ids=[c[0] for c in PROBLEMS])
def test_a_problem_is_reported_with_its_code_place_and_line(text, code, where, anchor, message):
    [p] = problems_of(text)
    assert (p.level, p.code, p.where, p.line) == ('error', code, where, line_of(text, anchor))
    assert p.message == message


def test_the_table_has_a_case_for_every_code_expect_and_waive_raise():
    assert {c[2] for c in PROBLEMS} == {'wrong_type', 'missing_key', 'bad_value', 'unknown_key',
                                        'not_modelled', 'out_of_range', 'yaml_syntax'}


# What each verb takes, written out rather than read from WINDOW_KEYS.
TAKES = {'reach': 'from, within_ms, not_before_ms', 'hold': 'from, after_ms, until, for_ms',
         'never': 'from, after_ms, until, for_ms', 'peak': 'from, after_ms, until, for_ms',
         'low': 'from, after_ms, until, for_ms', 'states': ''}
CHECKS = {'reach': REACH, 'hold': 'hold: {state: DRIVE}', 'never': 'never: {fault: any}',
          'peak': 'peak: {signal: torque_nm, at_most: 0.0}',
          'low': 'low: {signal: speed_m_s, at_least: 0.0}', 'states': 'states: [IDLE]'}
WINDOW = {'from': 'rtd', 'within_ms': 30, 'not_before_ms': 3, 'after_ms': 3, 'until': 'pedal-off',
          'for_ms': 300}


def test_the_verbs_and_their_window_keys():
    assert VERBS == tuple(TAKES)
    assert {verb: ', '.join(keys) for verb, keys in WINDOW_KEYS.items()} == TAKES


@pytest.mark.parametrize('verb, key', [(v, k) for v in TAKES for k in WINDOW],
                         ids=[f'{v}-{k}' for v in TAKES for k in WINDOW])
def test_each_verb_takes_only_its_own_window_keys(verb, key):
    window = [f'{key}: {WINDOW[key]}']
    if verb == 'reach' and key not in ('from', 'within_ms'):
        window.append(WITHIN)  # a reach from the start has no default deadline
    text = expecting(item(CHECKS[verb], *window))
    if key in TAKES[verb].split(', '):
        [entry] = loaded(text).parameters()['expect']
        assert entry[key] == WINDOW[key]
        return
    hint = ('states checks the whole run, so it takes no window' if verb == 'states'
            else f'{verb} takes {TAKES[verb]}')
    [p] = problems_of(text)
    assert (p.code, p.where, p.line, p.message) == (
        'unknown_key', f'expect[0].{key}', line_of(text, f'    {key}:'),
        f'does not go with {verb}; {hint}')


@pytest.mark.parametrize('text', [LAUNCH, LAUNCH + '\nexpect: []\n', LAUNCH + '\nexpect:\n',
                                  LAUNCH + '\nexpect:\n  # - never: {fault: any}\n',
                                  LAUNCH + '\nwaive:\n'],
                         ids=['left-out', 'empty-list', 'empty', 'all-commented-out',
                              'empty-waive'])
def test_an_empty_expect_or_waive_is_allowed(text):
    sf = loaded(text)
    assert (sf.expectations, sf.waive) == ((), {})
    assert (sf.parameters()['expect'], sf.parameters()['waive']) == ([], {})


def test_a_verb_written_twice_in_an_item_names_both_lines():
    text = expecting(item('never: {fault: any}', 'never: {state: FAULT}'))
    [p] = problems_of(text)
    first, second = line_of(text, '  - never: {fault'), line_of(text, '    never: {state')
    assert (p.code, p.where, p.line) == ('yaml_syntax', 'expect[0].never', second)
    assert p.message == f'is written twice, at lines {first} and {second}; merge them'


def test_an_unknown_condition_key_also_leaves_the_condition_naming_nothing():
    text = expecting(item('never: {stat: DRIVE}'))
    assert [(p.code, p.where, p.message) for p in problems_of(text)] == [
        ('unknown_key', 'expect[0].never.stat', 'is not a condition key; did you mean state?'),
        ('missing_key', 'expect[0].never', 'names nothing to look for; give it state:, fault: '
                                           'or signal:, such as {state: DRIVE}')]


def test_every_item_is_checked_and_reported_at_once():
    text = expecting(item('never: {state: FAUL}'), item('hold: {fault: any}', 'within_ms: 3'),
                     item('states: [IDLE, IDLE]'))
    assert [(p.where, p.line) for p in problems_of(text)] == [
        ('expect[0].never.state', line_of(text, 'FAUL}')),
        ('expect[1].within_ms', line_of(text, 'within_ms: 3')),
        ('expect[2].states[1]', line_of(text, '[IDLE, IDLE]'))]


@pytest.mark.parametrize('name', NOT_MODELLED)
@pytest.mark.parametrize('check', ['reach: {{state: {}}}', 'never: {{fault: {}}}',
                                   'states: [IDLE, {}]'])
def test_a_condition_the_rig_does_not_model_is_refused_wherever_it_is_named(check, name):
    window = [WITHIN] if check.startswith('reach') else []
    [p] = problems_of(expecting(item(check.format(name), *window)))
    assert p.code == 'not_modelled'


def test_bounds_are_floats_and_minus_zero_is_zero():
    e = only(item('peak: {signal: torque_nm, at_most: -0.0}'))
    assert type(e.condition.hi) is float and math.copysign(1.0, e.condition.hi) == 1.0
    assert e.condition == Condition('signal', 'torque_nm', hi=0.0)
    assert only(item('never: {signal: torque_nm, between: [1, 1]}')).condition == Condition(
        'signal', 'torque_nm', lo=1.0, hi=1.0)  # a band of one value is a band


TYPO = expecting(item('never: {fault: any}', 'from: rtdd'))


@pytest.mark.parametrize('edit, where', [
    (lambda t: t.replace('accelerator: 1.0', 'accelerator: 1.5'), 'at[5].accelerator'),
    (lambda t: t + item('never: {state: FAUL}'), 'expect[1].never.state'),
    (lambda t: t + '\nwaive:\n  bot_detect: why\n', 'waive.bot_detect'),
    (lambda t: t.replace('from: rtdd', 'from: rtdd\n    within_ms: 3'), 'expect[0].within_ms'),
], ids=['a-block', 'another-item', 'waive', 'the-same-item'])
def test_mark_and_window_problems_wait_for_a_clean_pass_1(edit, where):
    assert [p.where for p in problems_of(TYPO)] == ['expect[0].from']
    assert [p.where for p in problems_of(edit(TYPO))] == [where]


def test_a_reach_judged_impossible_in_pass_1_hides_its_mark():
    text = expecting(item(REACH, 'from: rtdd', 'within_ms: 30', 'not_before_ms: 31'))
    assert [p.where for p in problems_of(text)] == ['expect[0].not_before_ms']


def test_every_pass_2_problem_is_reported_at_once():
    text = expecting(item('never: {fault: any}', 'from: rtdd'),
                     item('hold: {fault: any}', 'until: pedal-of'), item(REACH))
    assert [(p.code, p.where) for p in problems_of(text)] == [
        ('bad_value', 'expect[0].from'), ('bad_value', 'expect[1].until'),
        ('missing_key', 'expect[2].within_ms')]


# (id, item, anchor, anchor_ms, start_ms, stop_ms, span). Each counts from
# its mark's sample: rtd's block is at 1000 ms, its sample at 1002.
WINDOWS = [
    ('the-whole-run', item('never: {fault: any}'), 'the start', 0, 0, None, 'until the end'),
    ('from-a-mark-on-the-grid', item('never: {fault: any}', 'from: energized'), 'energized',
     600, 600, None, 'until the end'),
    ('from-a-mark-off-the-grid', item('never: {fault: any}', 'from: rtd'), 'rtd', 1002, 1002,
     None, 'until the end'),
    ('from-precharge', item('never: {fault: any}', 'from: precharge'), 'precharge', 201, 201,
     None, 'until the end'),
    ('after-ms', item('hold: {fault: any}', 'from: rtd', 'after_ms: 120'), 'rtd', 1002, 1122,
     None, 'until the end'),
    ('after-ms-zero', item('hold: {fault: any}', 'from: rtd', 'after_ms: 0'), 'rtd', 1002, 1002,
     None, 'until the end'),
    ('until-a-mark-at-its-sample', item('never: {fault: any}', 'until: rtd'), 'the start', 0, 0,
     1002, 'until rtd'),
    ('after-ms-off-the-grid', item('never: {fault: any}', 'from: rtd', 'after_ms: 1',
                                   'until: brake-off'), 'rtd', 1002, 1003, 1500,
     'until brake-off'),
    ('for-ms', item('never: {fault: any}', 'from: rtd', 'for_ms: 21'), 'rtd', 1002, 1002, 1023,
     'for 21 ms'),
    ('for-ms-one-sample', item('never: {fault: any}', 'from: rtd', 'after_ms: 1', 'for_ms: 3'),
     'rtd', 1002, 1003, 1006, 'for 3 ms'),
    ('for-ms-to-the-end', item('never: {fault: any}', 'from: pedal-off', 'for_ms: 1002'),
     'pedal-off', 6000, 6000, 7002, 'for 1002 ms'),
    ('the-last-sample-alone', item('never: {fault: any}', 'after_ms: 6999'), 'the start', 0,
     6999, None, 'until the end'),
    ('a-start-between-samples', item('never: {fault: any}', 'after_ms: 6997'), 'the start', 0,
     6997, None, 'until the end'),
]


@pytest.mark.parametrize('text, anchor, anchor_ms, start_ms, stop_ms, span',
                         [c[1:] for c in WINDOWS], ids=[c[0] for c in WINDOWS])
def test_a_window_resolves_to_samples(text, anchor, anchor_ms, start_ms, stop_ms, span):
    e = only(text)
    assert (e.anchor, e.anchor_ms, e.start_ms, e.stop_ms, e.span) == (
        anchor, anchor_ms, start_ms, stop_ms, span)


@pytest.mark.parametrize('window, anchor, anchor_ms, within, not_before', [
    (('from: rtd', 'within_ms: 24', 'not_before_ms: 18'), 'rtd', 1002, 24, 18),
    (('within_ms: 0',), 'the start', 0, 0, 0),
    (('within_ms: 600000',), 'the start', 0, 600000, 0),  # a deadline past the end is no end
    (('from: pedal-off', 'within_ms: 2000', 'not_before_ms: 999'), 'pedal-off', 6000, 2000,
     999),  # due from 6999, the last sample
], ids=['launch', 'zero', 'ten-minutes', 'due-at-the-last-sample'])
def test_a_reach_resolves_its_anchor_and_deadline(window, anchor, anchor_ms, within,
                                                  not_before):
    e = only(item(REACH, *window))
    assert (e.anchor, e.anchor_ms, e.within_ms, e.not_before_ms, e.within_default) == (
        anchor, anchor_ms, within, not_before, False)


def test_default_within_ms_is_the_firmwares_delay_plus_slack():
    # The loop and RTD debounces land on the grid at 12 and 21 ms, plus a
    # tick; a CONTACTOR_STATUS frame comes 0 to 9 ms after the sample.
    assert DEFAULT_WITHIN_MS == {'shutdown_loop_v': 15, 'rtd_pressed': 24, 'hvc_state': 12,
                                 'contactors_closed': 12}


# (id, blocks added, mark, the default)
DEFAULTS = [
    ('rtd', '', 'rtd', 24),
    ('hvc-state', '', 'precharge', 12),
    ('hvc-state-and-contactors', '', 'energized', 12),
    ('contactors', block(6500, 'mark: airs-open', 'contactors_closed: false'), 'airs-open', 12),
    ('loop', block(6500, 'mark: loop-open', 'shutdown_loop_v: 0.0'), 'loop-open', 15),
    ('a-loop-ramp', block(6500, 'mark: sag', 'shutdown_loop_v: {to: 12.0, ramp_ms: 30}'), 'sag',
     15),
    ('loop-and-rtd', block(6500, 'mark: both', 'shutdown_loop_v: 0.0', 'rtd_pressed: false'),
     'both', 24),
    ('loop-and-hvc', block(6500, 'mark: both', 'shutdown_loop_v: 0.0',
                           'hvc_state: NOT_ENERGIZED'), 'both', 15),
    ('a-filtered-signal-beside-a-known-one', block(6500, 'mark: both', 'accelerator: 0.5',
                                                   'rtd_pressed: false'), 'both', 24),
]


@pytest.mark.parametrize('blocks, mark, within', [c[1:] for c in DEFAULTS],
                         ids=[c[0] for c in DEFAULTS])
def test_a_reach_with_no_within_ms_takes_the_largest_default(blocks, mark, within):
    sf = loaded(expecting(item('reach: {state: FAULT}', f'from: {mark}'), blocks=blocks))
    [e] = sf.expectations
    assert (e.within_ms, e.within_default) == (within, True)
    assert e.text() == f'reach state FAULT from {mark} within {within} ms (default)'
    assert sf.parameters()['expect'] == [{'reach': {'state': 'FAULT'}, 'from': mark}]


def test_a_within_ms_written_out_is_not_a_default_even_when_it_is_the_same():
    e = only(item(REACH, 'from: rtd', 'within_ms: 24'))
    assert (e.within_ms, e.within_default, e.text()) == (
        24, False, 'reach state DRIVE from rtd within 24 ms')


TEXTS = [
    (item('states: [IDLE, PRECHARGE, TS_ACTIVE, RTD_WAIT, DRIVE]'),
     'states IDLE -> PRECHARGE -> TS_ACTIVE -> RTD_WAIT -> DRIVE'),
    (item('states: [IDLE]'), 'states IDLE'),
    (item(REACH, 'from: rtd', 'not_before_ms: 18', 'within_ms: 24'),
     'reach state DRIVE from rtd within 24 ms, not before 18 ms'),
    (item(REACH, 'from: rtd', 'not_before_ms: 18'),
     'reach state DRIVE from rtd within 24 ms (default), not before 18 ms'),
    (item('reach: {fault: any}', 'within_ms: 30'), 'reach any fault from the start within 30 ms'),
    (item('reach: {signal: torque_nm, at_least: 50}', 'from: pedal-on', 'within_ms: 300'),
     'reach torque_nm at least 50 from pedal-on within 300 ms'),
    (item('hold: {fault: BRAKE_OVER_THROTTLE}', 'from: rtd', 'after_ms: 120', 'until: pedal-off'),
     'hold fault BRAKE_OVER_THROTTLE from rtd + 120 ms until pedal-off'),
    (item('hold: {state: DRIVE}', 'from: rtd', 'after_ms: 0'),
     'hold state DRIVE from rtd until the end'),
    (item('never: {state: DRIVE}', 'for_ms: 600'), 'never state DRIVE from the start for 600 ms'),
    (item('never: {signal: torque_nm, at_least: 0.1}', 'until: rtd'),
     'never torque_nm at least 0.1 from the start until rtd'),
    (item('peak: {signal: torque_nm, between: [209.95, 210.05]}'),
     'peak torque_nm between 209.95 and 210.05 from the start until the end'),
    (item('low: {signal: speed_m_s, at_least: 0}', 'from: pedal-off', 'after_ms: 100'),
     'low speed_m_s at least 0 from pedal-off + 100 ms until the end'),
    (item('hold: {signal: dc_power_kw, at_most: 80}', 'from: pedal-on', 'for_ms: 300'),
     'hold dc_power_kw at most 80 from pedal-on for 300 ms'),
]


@pytest.mark.parametrize('text, says', TEXTS, ids=[t[1] for t in TEXTS])
def test_an_expectation_reads_as_one_line(text, says):
    assert only(text).text() == says


# (condition, text, as parameters() records it)
CONDITIONS = [
    (Condition('state', 'DRIVE'), 'state DRIVE', {'state': 'DRIVE'}),
    (Condition('fault', 'SHUTDOWN_OPEN'), 'fault SHUTDOWN_OPEN', {'fault': 'SHUTDOWN_OPEN'}),
    (Condition('fault', 'any'), 'any fault', {'fault': 'any'}),
    (Condition('signal', 'torque_nm', hi=0.0), 'torque_nm at most 0',
     {'signal': 'torque_nm', 'at_most': 0.0}),
    (Condition('signal', 'speed_m_s', lo=25.0), 'speed_m_s at least 25',
     {'signal': 'speed_m_s', 'at_least': 25.0}),
    (Condition('signal', 'torque_nm', lo=209.95, hi=210.05),
     'torque_nm between 209.95 and 210.05', {'signal': 'torque_nm', 'between': [209.95, 210.05]}),
    (Condition('signal', 'dc_power_kw', lo=-1.5, hi=-1.5), 'dc_power_kw between -1.5 and -1.5',
     {'signal': 'dc_power_kw', 'between': [-1.5, -1.5]}),
]


@pytest.mark.parametrize('condition, text, written', CONDITIONS, ids=[c[1] for c in CONDITIONS])
def test_a_condition_reads_as_written(condition, text, written):
    assert (condition.text(), condition.written()) == (text, written)


EVERY_SHAPE = expecting(
    item('states: [IDLE, PRECHARGE, TS_ACTIVE, RTD_WAIT, DRIVE]'),
    item(REACH, 'from: rtd', 'not_before_ms: 18'),
    item('reach: {fault: any}', 'within_ms: 30'),
    item('hold: {fault: SHUTDOWN_OPEN}', 'from: pedal-on', 'after_ms: 12', 'until: pedal-off'),
    item('never: {signal: torque_nm, at_least: 1}', 'until: rtd'),
    item('peak: {signal: torque_nm, between: [209.95, 210.05]}', 'for_ms: 6000'),
    '  - low:\n      signal: speed_m_s\n      at_least: 0\n    from: pedal-off\n',
) + '\nwaive:\n  bot_detected: "the brake is off: on purpose"\n  power_within_80kw: >-\n' \
    '    a long\n    reason\n'


def test_parameters_record_expect_and_waive_as_parsed():
    sf = loaded(EVERY_SHAPE)
    params = sf.parameters()
    assert params['expect'] == [
        {'states': ['IDLE', 'PRECHARGE', 'TS_ACTIVE', 'RTD_WAIT', 'DRIVE']},
        {'reach': {'state': 'DRIVE'}, 'from': 'rtd', 'not_before_ms': 18},
        {'reach': {'fault': 'any'}, 'within_ms': 30},
        {'hold': {'fault': 'SHUTDOWN_OPEN'}, 'from': 'pedal-on', 'after_ms': 12,
         'until': 'pedal-off'},
        {'never': {'signal': 'torque_nm', 'at_least': 1.0}, 'until': 'rtd'},
        {'peak': {'signal': 'torque_nm', 'between': [209.95, 210.05]}, 'for_ms': 6000},
        {'low': {'signal': 'speed_m_s', 'at_least': 0.0}, 'from': 'pedal-off'}]
    # Bounds are floats, so 1 and 1.0 record alike; milliseconds stay whole.
    assert type(params['expect'][4]['never']['at_least']) is float
    assert type(params['expect'][2]['within_ms']) is int
    waived = {'bot_detected': 'the brake is off: on purpose', 'power_within_80kw': 'a long reason'}
    assert params['waive'] == sf.waive == waived
    assert json.loads(json.dumps(params, allow_nan=False)) == params
    assert as_safe_load_reads_it(sf) == params


def test_parameters_are_a_copy():
    sf = loaded(EVERY_SHAPE)
    sf.parameters()['expect'][1]['from'] = 'pedal-on'
    sf.parameters()['waive'].clear()
    assert sf.parameters()['expect'][1]['from'] == 'rtd' and sf.parameters()['waive']


@pytest.mark.parametrize('name', INVARIANTS)
def test_every_invariant_may_be_waived_and_not_allowed(name):
    assert loaded(waiving(f'{name}: on purpose')).waive == {name: 'on purpose'}
    [p] = problems_of(LAUNCH + f'\nallow:\n  {name}: on purpose\n')
    assert p.message == f'is not a warning code; {name} is an invariant: it goes under waive:'


@pytest.mark.parametrize('code', [c for c in WARNINGS if c != 'unused_allow'])
def test_every_warning_code_under_waive_is_sent_to_allow(code):
    [p] = problems_of(waiving(f'{code}: why'))
    assert p.message == f'is not an invariant; {code} is a warning code: it goes under allow:'


def test_unused_allow_under_waive_is_not_sent_to_allow_which_refuses_it():
    [allowed] = problems_of(LAUNCH + '\nallow:\n  unused_allow: why\n')
    assert allowed.message.startswith('cannot be allowed')
    [p] = problems_of(waiving('unused_allow: why'))
    assert 'it goes under allow:' not in p.message


def test_invariant_names_and_warning_codes_are_disjoint():
    assert not set(INVARIANTS) & set(WARNINGS)


def test_a_number_in_a_states_list_is_told_to_write_the_bare_name():
    [p] = problems_of(expecting(item('states: [IDLE, 4]')))
    assert p.message == '4 is not an lhre VcuStateMachine name; write the name: PRECHARGE'


# A run parked and ready: every row is this unless a span says otherwise.
PARKED = {'fw_state': VCU_STATES['IDLE'], 'fw_faults': 0, 'fw_inverter_enable': 0,
          'fw_torque_request_nm': 0.0, 'accelerator': 0.0, 'brake_psi': 350.0,
          'rtd_pressed': False, 'hvc_state': 2, 'contactors_closed': 1, 'shutdown_loop_v': 24.0,
          'fw_hard_braking': 0, 'dcBusVoltage': 550.0, 'dcBusCurrent': 0.0, 'rearU': 0.0}


def run(*spans, end_ms: int = DURATION) -> list[dict]:
    """A row per 3 ms sample before end_ms, parked, where each span (key,
    from_ms, to_ms, value) sets key on from_ms <= t < to_ms (to_ms None: to
    the end). A later span wins."""
    rows = []
    for t in range(0, end_ms, 3):
        row = {**PARKED, 't_ms': t}
        for key, lo, hi, value in spans:
            if lo <= t and (hi is None or t < hi):
                row[key] = value
        rows.append(row)
    return rows


def state(name: str, lo: int, hi: int | None = None):
    return ('fw_state', lo, hi, VCU_STATES[name])


def faults(lo: int, hi: int | None, *names: str):
    return ('fw_faults', lo, hi, sum(1 << (VCU_FAULTS[n] - 1) for n in names))


def torque(nm: float, lo: int, hi: int | None = None):
    return ('fw_torque_request_nm', lo, hi, nm)


def judged(text: str, rows: list[dict], blocks: str = '') -> expectations.Result:
    [r] = expectations.evaluate(loaded(expecting(text, blocks=blocks)), rows)
    return r


def verdict(r: expectations.Result) -> tuple:
    return (r.result, r.at_ms, r.measured)


def test_the_run_helper_takes_a_row_per_sample():
    rows = run()
    assert (len(rows), rows[0]['t_ms'], rows[-1]['t_ms']) == (STEPS, 0, 6999)
    assert len(run(end_ms=1027)) == 343 and run(end_ms=1027)[-1]['t_ms'] == 1026


POWER_UP = (state('PRECHARGE', 201, 600), state('TS_ACTIVE', 600, 603),
            state('RTD_WAIT', 603, 1023))
LAUNCH_STATES = (*POWER_UP, state('DRIVE', 1023))
# hvc-drops-in-drive: one tick of FAULT, then IDLE.
HVC_DROP = (*POWER_UP, state('DRIVE', 1023, 4011), state('FAULT', 4011, 4014),
            state('IDLE', 4014))
LAUNCH_REACH = item(REACH, 'from: rtd', 'not_before_ms: 18', 'within_ms: 24')
STOPPED = 'the run stopped at {} ms, before the window ends'

# (id, spans, end_ms, verdict) for launch's reach: rtd's sample is at 1002,
# so DRIVE is due from 1020 to 1026.
REACHES = [
    ('as-measured', (state('DRIVE', 1023),), DURATION,
     ('pass', 1023, 'first at 1023 ms, 21 ms after rtd')),
    ('on-the-deadline', (state('DRIVE', 1026),), DURATION,
     ('pass', 1026, 'first at 1026 ms, 24 ms after rtd')),
    ('a-tick-late', (state('DRIVE', 1029),), DURATION,
     ('fail', 1029, 'first at 1029 ms, 27 ms after rtd: 3 ms late')),
    ('on-not-before', (state('DRIVE', 1020),), DURATION,
     ('pass', 1020, 'first at 1020 ms, 18 ms after rtd')),
    ('a-tick-too-soon', (state('DRIVE', 1017),), DURATION,
     ('fail', 1017, 'first at 1017 ms, 15 ms after rtd: 3 ms too soon')),
    ('too-soon-at-the-anchor', (state('DRIVE', 900),), DURATION,
     ('fail', 1002, 'first at 1002 ms, 0 ms after rtd: 18 ms too soon')),
    ('a-stretch-before-the-anchor-does-not-count', (state('DRIVE', 900, 1002),
                                                    state('DRIVE', 1023)), DURATION,
     ('pass', 1023, 'first at 1023 ms, 21 ms after rtd')),
    ('only-the-first-counts', (state('DRIVE', 1017, 1020), state('DRIVE', 1023)), DURATION,
     ('fail', 1017, 'first at 1017 ms, 15 ms after rtd: 3 ms too soon')),
    ('never', (), DURATION, ('fail', None, 'state DRIVE never came after rtd (1002 ms)')),
    ('cut-short-before-the-deadline', (), 1026, ('not_evaluated', None, STOPPED.format(1023))),
    ('cut-short-at-the-deadline', (), 1027,
     ('fail', None, 'state DRIVE never came after rtd (1002 ms)')),
    ('cut-short-after-a-pass', (state('DRIVE', 1023),), 1024,
     ('pass', 1023, 'first at 1023 ms, 21 ms after rtd')),
    ('cut-short-after-a-late-first', (state('DRIVE', 1029),), 1030,
     ('fail', 1029, 'first at 1029 ms, 27 ms after rtd: 3 ms late')),
    ('cut-short-after-a-first-too-soon', (state('DRIVE', 1017),), 1018,
     ('fail', 1017, 'first at 1017 ms, 15 ms after rtd: 3 ms too soon')),
    ('cut-short-before-the-anchor', (), 900, ('not_evaluated', None, STOPPED.format(897))),
]


@pytest.mark.parametrize('spans, end_ms, expected', [c[1:] for c in REACHES],
                         ids=[c[0] for c in REACHES])
def test_reach(spans, end_ms, expected):
    assert verdict(judged(LAUNCH_REACH, run(*spans, end_ms=end_ms))) == expected


def test_a_reach_already_true_at_its_anchor_passes_with_no_latency():
    r = judged(item(REACH, 'from: rtd', 'within_ms: 24'), run(state('DRIVE', 0)))
    assert verdict(r) == ('pass', 1002, 'first at 1002 ms, 0 ms after rtd')


def test_a_reach_from_the_start_counts_from_0():
    r = judged(item('reach: {state: PRECHARGE}', 'within_ms: 201'), run(*LAUNCH_STATES))
    assert verdict(r) == ('pass', 201, 'first at 201 ms, 201 ms after the start')
    r = judged(item('reach: {state: PRECHARGE}', 'within_ms: 200'), run(*LAUNCH_STATES))
    assert verdict(r) == ('fail', 201, 'first at 201 ms, 201 ms after the start: 1 ms late')


def test_a_default_deadline_judges_like_a_written_one():
    # The loop opens at 6500 ms, sampled at 6501; its debounce passes 12 ms
    # later, and the default deadline is 15 ms.
    sf = loaded(expecting(item('reach: {state: FAULT}', 'from: loop-open'),
                          blocks=block(6500, 'mark: loop-open', 'shutdown_loop_v: 0.0')))
    assert [verdict(expectations.evaluate(sf, run(state('FAULT', t)))[0])
            for t in (6513, 6516, 6519)] == [
        ('pass', 6513, 'first at 6513 ms, 12 ms after loop-open'),
        ('pass', 6516, 'first at 6516 ms, 15 ms after loop-open'),
        ('fail', 6519, 'first at 6519 ms, 18 ms after loop-open: 3 ms late')]


# (id, spans, end_ms, verdict) for a hold through [1023, 6000): from rtd's
# sample, 21 ms on, until pedal-off's sample, which is outside it.
HOLD = item('hold: {state: DRIVE}', 'from: rtd', 'after_ms: 21', 'until: pedal-off')
HOLDS = [
    ('held-from-the-first-tick', (state('DRIVE', 1023),), DURATION,
     ('pass', None, '1659 samples, 1023..5997 ms')),
    ('the-until-marks-sample-is-outside', (state('DRIVE', 1023, 6000),), DURATION,
     ('pass', None, '1659 samples, 1023..5997 ms')),
    ('the-first-tick-breaks-it', (state('DRIVE', 1026),), DURATION,
     ('fail', 1023, 'at 1023 ms: IDLE')),
    ('the-last-tick-breaks-it', (state('DRIVE', 1023, 5997),), DURATION,
     ('fail', 5997, 'at 5997 ms: IDLE')),
    ('the-first-break-is-reported', (state('DRIVE', 1023), state('FAULT', 3000, 3003),
                                     state('IDLE', 4000)), DURATION,
     ('fail', 3000, 'at 3000 ms: FAULT')),
    ('an-unknown-state-number', (state('DRIVE', 1023), ('fw_state', 3000, 3003, 9)), DURATION,
     ('fail', 3000, 'at 3000 ms: state 9')),
    ('cut-short', (state('DRIVE', 1023),), 3000, ('not_evaluated', None, STOPPED.format(2997))),
    ('cut-short-after-a-break', (state('DRIVE', 1023, 2001),), 3000,
     ('fail', 2001, 'at 2001 ms: IDLE')),
    ('cut-short-a-tick-before-the-end', (state('DRIVE', 1023),), 5997,
     ('not_evaluated', None, STOPPED.format(5994))),
    ('cut-short-after-the-window', (state('DRIVE', 1023),), 6000,
     ('pass', None, '1659 samples, 1023..5997 ms')),
]


@pytest.mark.parametrize('spans, end_ms, expected', [c[1:] for c in HOLDS],
                         ids=[c[0] for c in HOLDS])
def test_hold(spans, end_ms, expected):
    assert verdict(judged(HOLD, run(*spans, end_ms=end_ms))) == expected


NEVER_A_FAULT = item('never: {fault: any}')
# (id, spans, end_ms, verdict). SHUTDOWN_OPEN is set on 0, 3, 6 and 9 ms of
# every run, before the loop's debounce: a fault condition does not see it.
NEVERS = [
    ('no-faults', (), DURATION, ('pass', None, '2334 samples, 0..6999 ms')),
    ('the-boot-shutdown-open', (faults(0, 10, 'SHUTDOWN_OPEN'),), DURATION,
     ('pass', None, '2334 samples, 0..6999 ms')),
    ('shutdown-open-at-12-ms', (faults(0, 13, 'SHUTDOWN_OPEN'),), DURATION,
     ('fail', 12, 'at 12 ms: SHUTDOWN_OPEN')),
    ('another-fault-at-boot', (faults(0, 10, 'SHUTDOWN_OPEN'),
                               faults(3, 4, 'SHUTDOWN_OPEN', 'HVC_TIMEOUT')), DURATION,
     ('fail', 3, 'at 3 ms: HVC_TIMEOUT')),
    ('two-faults-in-code-order', (faults(4104, 4107, 'SHUTDOWN_OPEN', 'BRAKE_OVER_THROTTLE'),),
     DURATION, ('fail', 4104, 'at 4104 ms: BRAKE_OVER_THROTTLE, SHUTDOWN_OPEN')),
    ('on-the-last-sample', (faults(6999, None, 'CONTACTORS_OPEN'),), DURATION,
     ('fail', 6999, 'at 6999 ms: CONTACTORS_OPEN')),
    ('cut-short', (), 3000, ('not_evaluated', None, STOPPED.format(2997))),
    ('cut-short-after-a-fault', (faults(2001, 2004, 'INVERTER_TIMEOUT'),), 3000,
     ('fail', 2001, 'at 2001 ms: INVERTER_TIMEOUT')),
]


@pytest.mark.parametrize('spans, end_ms, expected', [c[1:] for c in NEVERS],
                         ids=[c[0] for c in NEVERS])
def test_never(spans, end_ms, expected):
    assert verdict(judged(NEVER_A_FAULT, run(*spans, end_ms=end_ms))) == expected


@pytest.mark.parametrize('window, expected', [
    (('for_ms: 30',), ('fail', 0, 'at 0 ms: no faults')),
    (('after_ms: 9',), ('fail', 9, 'at 9 ms: no faults')),
    (('after_ms: 10',), ('pass', None, '2330 samples, 12..6999 ms')),
    (('after_ms: 12',), ('pass', None, '2330 samples, 12..6999 ms')),
], ids=['from-0', 'from-9-ms', 'from-10-ms', 'from-12-ms'])
def test_a_fault_condition_sees_shutdown_open_from_the_first_tick_after_10_ms(window, expected):
    rows = run(faults(0, None, 'SHUTDOWN_OPEN'))
    assert verdict(judged(item('hold: {fault: SHUTDOWN_OPEN}', *window), rows)) == expected


def test_a_reach_of_shutdown_open_comes_on_the_first_tick_it_is_seen():
    r = judged(item('reach: {fault: SHUTDOWN_OPEN}', 'within_ms: 12'),
               run(faults(0, None, 'SHUTDOWN_OPEN')))
    assert verdict(r) == ('pass', 12, 'first at 12 ms, 12 ms after the start')


@pytest.mark.parametrize('t_ms, seen', [(0, False), (9, False), (12, True), (15, True)])
def test_holds_masks_only_shutdown_open_and_only_before_10_ms(t_ms, seen):
    loop = 1 << (VCU_FAULTS['SHUTDOWN_OPEN'] - 1)
    hvc = 1 << (VCU_FAULTS['HVC_TIMEOUT'] - 1)
    row = {**PARKED, 't_ms': float(t_ms), 'fw_faults': float(loop | hvc)}  # as trace.csv reads
    assert expectations.holds(Condition('fault', 'SHUTDOWN_OPEN'), row) is seen
    assert expectations.holds(Condition('fault', 'HVC_TIMEOUT'), row) is True
    assert expectations.holds(Condition('fault', 'any'), {**row, 'fw_faults': float(loop)}) is seen


def test_until_a_mark_leaves_out_the_marks_own_sample():
    text = item('never: {signal: torque_nm, at_least: 0.1}', 'until: rtd')
    assert verdict(judged(text, run(torque(0.1, 1002)))) == (
        'pass', None, '334 samples, 0..999 ms')
    assert verdict(judged(text, run(torque(0.1, 999)))) == ('fail', 999, 'at 999 ms: torque_nm 0.1')
    assert judged(text, run(torque(0.09, 999))).result == 'pass'


# Each signal from its own column: (id, condition, spans, verdict) for a
# never through [6000, 6003): one sample, so the value there decides.
SIGNALS = [
    ('torque', 'torque_nm, at_least: 0.1', (torque(0.1, 6000, 6003),),
     ('fail', 6000, 'at 6000 ms: torque_nm 0.1')),
    ('torque-under', 'torque_nm, at_least: 0.1', (torque(0.09, 6000, 6003),),
     ('pass', None, '1 samples, 6000..6000 ms')),
    ('speed', 'speed_m_s, at_most: 25.0', (('rearU', 6000, 6003, 25.0),),
     ('fail', 6000, 'at 6000 ms: speed_m_s 25')),
    ('speed-over', 'speed_m_s, at_most: 25.0', (('rearU', 6000, 6003, 25.01),),
     ('pass', None, '1 samples, 6000..6000 ms')),
    # dc_power_kw is V x I in kW: 400 V at 200 A is 80 kW.
    ('dc-power', 'dc_power_kw, at_least: 80.0', (('dcBusVoltage', 6000, 6003, 400.0),
                                                 ('dcBusCurrent', 6000, 6003, 200.0)),
     ('fail', 6000, 'at 6000 ms: dc_power_kw 80')),
    ('dc-power-under', 'dc_power_kw, at_least: 80.0', (('dcBusVoltage', 6000, 6003, 400.0),
                                                       ('dcBusCurrent', 6000, 6003, 199.9)),
     ('pass', None, '1 samples, 6000..6000 ms')),
    ('regen-is-negative-power', 'dc_power_kw, at_most: -10.0',
     (('dcBusCurrent', 6000, 6003, -20.0),), ('fail', 6000, 'at 6000 ms: dc_power_kw -11')),
    ('between-the-low-end', 'torque_nm, between: [1.0, 2.0]', (torque(1.0, 6000, 6003),),
     ('fail', 6000, 'at 6000 ms: torque_nm 1')),
    ('between-the-high-end', 'torque_nm, between: [1.0, 2.0]', (torque(2.0, 6000, 6003),),
     ('fail', 6000, 'at 6000 ms: torque_nm 2')),
    ('between-past-the-high-end', 'torque_nm, between: [1.0, 2.0]',
     (torque(2.0001, 6000, 6003),), ('pass', None, '1 samples, 6000..6000 ms')),
]


@pytest.mark.parametrize('condition, spans, expected', [c[1:] for c in SIGNALS],
                         ids=[c[0] for c in SIGNALS])
def test_a_signal_condition_reads_its_column_in_its_unit(condition, spans, expected):
    text = item(f'never: {{signal: {condition}}}', 'from: pedal-off', 'for_ms: 3')
    assert verdict(judged(text, run(*spans))) == expected


PEAK = item('peak: {signal: torque_nm, between: [209.95, 210.05]}', 'from: pedal-on',
            'until: pedal-off')
# (id, spans, end_ms, verdict) for launch's peak through [2001, 6000).
PEAKS = [
    ('a-plateau-from-its-first-tick', (torque(210.0, 2100, 5000),), DURATION,
     ('pass', 2100, 'highest 210 at 2100 ms')),
    ('the-high-end', (torque(100.0, 2100, 5000), torque(210.05, 3000, 3003)), DURATION,
     ('pass', 3000, 'highest 210.05 at 3000 ms')),
    ('past-the-high-end', (torque(100.0, 2100, 5000), torque(210.06, 3000, 3003)), DURATION,
     ('fail', 3000, 'highest 210.06 at 3000 ms')),
    ('the-low-end', (torque(209.95, 3000, 3003),), DURATION,
     ('pass', 3000, 'highest 209.95 at 3000 ms')),
    ('under-the-low-end', (torque(209.94, 3000, 3003),), DURATION,
     ('fail', 3000, 'highest 209.94 at 3000 ms')),
    ('nothing-in-the-window', (), DURATION, ('fail', 2001, 'highest 0 at 2001 ms')),
    ('a-spike-on-the-until-marks-sample-is-outside', (torque(210.0, 2100, 5000),
                                                      torque(300.0, 6000, 6003)), DURATION,
     ('pass', 2100, 'highest 210 at 2100 ms')),
    ('a-spike-before-from-is-outside', (torque(210.0, 2100, 5000), torque(300.0, 1998, 2001)),
     DURATION, ('pass', 2100, 'highest 210 at 2100 ms')),
    ('a-spike-on-the-first-sample', (torque(210.0, 2100, 5000), torque(250.0, 2001, 2004)),
     DURATION, ('fail', 2001, 'highest 250 at 2001 ms')),
    ('a-spike-on-the-last-sample', (torque(210.0, 2100, 5000), torque(250.0, 5997, 6000)),
     DURATION, ('fail', 5997, 'highest 250 at 5997 ms')),
    ('cut-short', (torque(210.0, 2100),), 5997, ('not_evaluated', None, STOPPED.format(5994))),
    ('cut-short-after-the-window', (torque(210.0, 2100),), 6000,
     ('pass', 2100, 'highest 210 at 2100 ms')),
]


@pytest.mark.parametrize('spans, end_ms, expected', [c[1:] for c in PEAKS],
                         ids=[c[0] for c in PEAKS])
def test_peak(spans, end_ms, expected):
    assert verdict(judged(PEAK, run(*spans, end_ms=end_ms))) == expected


LOW = item('low: {signal: speed_m_s, at_least: 0.0}', 'from: pedal-off')
LOWS = [
    ('steady', (('rearU', 0, None, 30.0),), ('pass', 6000, 'lowest 30 at 6000 ms')),
    ('on-the-bound', (('rearU', 0, None, 30.0), ('rearU', 6501, 6504, 0.0)),
     ('pass', 6501, 'lowest 0 at 6501 ms')),
    ('under-it', (('rearU', 0, None, 30.0), ('rearU', 6501, 6504, -0.5)),
     ('fail', 6501, 'lowest -0.5 at 6501 ms')),
    ('under-it-before-the-window', (('rearU', 0, None, 30.0), ('rearU', 5997, 6000, -0.5)),
     ('pass', 6000, 'lowest 30 at 6000 ms')),
]


@pytest.mark.parametrize('spans, expected', [c[1:] for c in LOWS], ids=[c[0] for c in LOWS])
def test_low(spans, expected):
    assert verdict(judged(LOW, run(*spans))) == expected


LAUNCH_LIST = item('states: [IDLE, PRECHARGE, TS_ACTIVE, RTD_WAIT, DRIVE]')
DROP_LIST = item('states: [IDLE, PRECHARGE, TS_ACTIVE, RTD_WAIT, DRIVE, FAULT, IDLE]')
STATES = [
    ('launch', LAUNCH_LIST, LAUNCH_STATES, DURATION,
     ('pass', None, 'IDLE -> PRECHARGE -> TS_ACTIVE -> RTD_WAIT -> DRIVE')),
    ('one-tick-of-fault-is-a-stretch', DROP_LIST, HVC_DROP, DURATION,
     ('pass', None, 'IDLE -> PRECHARGE -> TS_ACTIVE -> RTD_WAIT -> DRIVE -> FAULT -> IDLE')),
    ('a-stretch-the-list-leaves-out', LAUNCH_LIST, HVC_DROP, DURATION,
     ('fail', None, 'IDLE -> PRECHARGE -> TS_ACTIVE -> RTD_WAIT -> DRIVE -> FAULT -> IDLE')),
    ('a-stretch-the-run-leaves-out', DROP_LIST, LAUNCH_STATES, DURATION,
     ('fail', None, 'IDLE -> PRECHARGE -> TS_ACTIVE -> RTD_WAIT -> DRIVE')),
    ('parked', item('states: [IDLE]'), (), DURATION, ('pass', None, 'IDLE')),
    ('an-unknown-state-number', item('states: [IDLE]'), (('fw_state', 3000, 3003, 9),),
     DURATION, ('fail', None, 'IDLE -> state 9 -> IDLE')),
    ('cut-short', LAUNCH_LIST, LAUNCH_STATES, 6999, ('not_evaluated', None, STOPPED.format(6996))),
]


@pytest.mark.parametrize('text, spans, end_ms, expected', [c[1:] for c in STATES],
                         ids=[c[0] for c in STATES])
def test_states(text, spans, end_ms, expected):
    assert verdict(judged(text, run(*spans, end_ms=end_ms))) == expected


def test_a_result_carries_its_expectations_index_line_and_text():
    text = expecting(NEVER_A_FAULT, LAUNCH_REACH)
    sf = loaded(text)
    results = expectations.evaluate(sf, run(*LAUNCH_STATES))
    assert [(r.index, r.line, r.text) for r in results] == [
        (0, line_of(text, '  - never: {fault'), 'never any fault from the start until the end'),
        (1, line_of(text, '  - reach: {state: DRIVE}'),
         'reach state DRIVE from rtd within 24 ms, not before 18 ms')]
    assert [r.meta() for r in results][1] == {
        'index': 1, 'line': results[1].line, 'text': results[1].text, 'result': 'pass',
        'at_ms': 1023, 'measured': 'first at 1023 ms, 21 ms after rtd'}
    assert [r.failed for r in results] == [False, False]


ALL_VERBS = (LAUNCH_LIST, LAUNCH_REACH, NEVER_A_FAULT, HOLD, PEAK, LOW)


def test_rows_read_back_from_trace_csv_as_floats_judge_the_same():
    rows = run(*HVC_DROP, faults(0, 10, 'SHUTDOWN_OPEN'), torque(210.0, 2100, 4011),
               ('rearU', 6000, None, -0.5))
    floats = [{key: float(value) for key, value in row.items()} for row in rows]
    sf = loaded(expecting(DROP_LIST, *ALL_VERBS))
    results = expectations.evaluate(sf, rows)
    assert expectations.evaluate(sf, floats) == results
    assert [r.result for r in results] == ['pass', 'fail', 'pass', 'pass', 'fail', 'pass',
                                           'fail']


def test_an_empty_run_judges_nothing():
    results = expectations.evaluate(loaded(expecting(*ALL_VERBS)), [])
    assert [(r.result, r.at_ms) for r in results] == [('not_evaluated', None)] * 6


def test_an_empty_run_names_no_negative_time():
    results = expectations.evaluate(loaded(expecting(*ALL_VERBS)), [])
    assert not [r.measured for r in results if '-3 ms' in r.measured]


def test_a_truncated_run_where_nothing_has_broken_yet_is_not_evaluated():
    # States so far a prefix of the list; the torque so far inside the band.
    rows = run(*LAUNCH_STATES, torque(210.0, 2100), end_ms=3000)
    results = expectations.evaluate(loaded(expecting(LAUNCH_LIST, PEAK)), rows)
    assert [r.result for r in results] == ['not_evaluated', 'not_evaluated']


def test_a_truncated_run_whose_states_already_left_the_list_fails():
    rows = run(state('FAULT', 300), end_ms=3000)  # IDLE -> FAULT where PRECHARGE was due
    assert judged(LAUNCH_LIST, rows).result == 'fail'


def test_a_truncated_run_already_past_a_peaks_bound_fails():
    rows = run(torque(250.0, 2502, 2505), end_ms=3000)
    assert verdict(judged(PEAK, rows)) == ('fail', 2502, 'highest 250 at 2502 ms')


@pytest.mark.parametrize('name', scenario_file.names())
def test_every_shipped_file_loads_its_expectations(name):
    sf = scenario_file.load(name)
    assert sf.expectations
    assert [e.index for e in sf.expectations] == list(range(len(sf.parameters()['expect'])))
    assert all('\n' not in e.text() for e in sf.expectations)


@pytest.mark.parametrize('name', scenario_file.names())
def test_every_shipped_expectations_line_is_its_items_dash(name):
    sf = scenario_file.load(name)
    lines = sf.data.decode().split('\n')
    start = lines.index('expect:')
    end = next(n for n in range(start + 1, len(lines)) if re.match(r'[a-z]', lines[n]))
    dashes = [n + 1 for n in range(start, end) if lines[n].startswith('  - ')]
    assert [e.line for e in sf.expectations] == dashes
    assert all(lines[e.line - 1].startswith(f'  - {e.verb}:') for e in sf.expectations)
