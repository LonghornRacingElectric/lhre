"""The run's verdict: judge() puts the invariants and the file's expectations
together, and the verdict is what meta.json, /events, summary.txt and exit 3
report. Synthetic rows and files only, no library or FMU: what each check
finds is pinned in test_check.py and test_expect.py; this pins how a verdict
counts, words and places it, so a reader, the viewer and CI see the same."""

import json

import pytest

from vcusil import __main__ as cli
from vcusil import cosim, report, scenario_file, verdict
from vcusil.cosim import STEP_MS
from vcusil.events import ERROR, FATAL, INFO, WARNING, Event
from vcusil.expectations import BOOT_ACCEPTED, SHUTDOWN_OPEN_BIT
from vcusil.firmware import HVC_ENERGIZED, VCU_FAULTS, VCU_STATES
from vcusil.scenario_file import INVARIANTS

NAME = 'verdict-test'
DISPLAY = f'{NAME}.yml'
DURATION_MS = 60  # 20 samples
END_MS = 57  # the last
CUT_MS = 30  # a run cut short: 10 samples, the last at 27 ms
STATE_NAMES = {number: name for name, number in VCU_STATES.items()}
APPS_IMPLAUSIBLE_BIT = 1 << (VCU_FAULTS['APPS_IMPLAUSIBLE'] - 1)

# Parked and ready: every invariant stays quiet on it.
PARKED = {'fw_state': VCU_STATES['IDLE'], 'fw_faults': 0, 'fw_inverter_enable': 0,
          'fw_torque_request_nm': 0.0, 'accelerator': 0.0, 'brake_psi': 350.0,
          'rtd_pressed': 0, 'hvc_state': HVC_ENERGIZED, 'contactors_closed': 1,
          'shutdown_loop_v': 24.0, 'fw_hard_braking': 0, 'dcBusVoltage': 550.0,
          'dcBusCurrent': 0.0, 'rearU': 0.0}
# The inverter enabled in IDLE with no torque: enable_only_in_drive alone.
ENABLED = {'fw_inverter_enable': 1}
# 5 N.m with it enabled in IDLE: no_torque_outside_drive and enable_only_in_drive.
DRIVEN = {'fw_inverter_enable': 1, 'fw_torque_request_nm': 5.0}
# Every run's boot: the shutdown loop reads UNKNOWN on ticks 0, 3, 6 and 9.
BOOT = (0, 10, {'fw_faults': SHUTDOWN_OPEN_BIT})


def rows(*spans: tuple[int, int, dict], until_ms: int = DURATION_MS) -> list[dict]:
    """PARKED samples from 0 to until_ms, each span (start_ms, stop_ms,
    changes) applied where start_ms <= t < stop_ms. A row has every trace
    column, as cosim's do, so summarize and write_trace take it."""
    out = []
    for t in range(0, until_ms, STEP_MS):
        row = {**dict.fromkeys(cosim.COLUMNS, 0), **PARKED, 't_ms': t}
        for start, stop, changes in spans:
            if start <= t < stop:
                row.update(changes)
        out.append(row)
    return out


def text(waive: dict[str, str], expect: tuple[str, ...]) -> str:
    """A parked file; waive as name: reason lines, expect as list items."""
    out = ('schema: lhre.vcu-sil-scenario.v1\n'
           'description: A parked car for the verdict tests.\n'
           f'duration_ms: {DURATION_MS}\n')
    if waive:
        out += '\nwaive:\n' + ''.join(f'  {k}: {v}\n' for k, v in waive.items())
    if expect:
        out += '\nexpect:\n' + ''.join(f'  - {item}\n' for item in expect)
    return out + '\nat:\n  - t_ms: 0\n    brake_psi: 350.0\n'


def load(waive: dict[str, str], *expect: str, **kw) -> scenario_file.ScenarioFile:
    return scenario_file.loads(text(waive, expect).encode(), NAME, **kw)


def line_of(source: str, anchor: str) -> int:
    found = [n for n, line in enumerate(source.split('\n'), 1) if anchor in line]
    assert len(found) == 1, (anchor, found)
    return found[0]


NEVER_FAULT = 'never: {fault: any}'
HOLD_IDLE = 'hold: {state: IDLE}'  # passes, on no one tick
NEVER_IDLE = 'never: {state: IDLE}'  # fails on the first sample, 0 ms
STATES_IDLE = 'states: [IDLE]'  # needs the whole run
# The window ends at 30 ms, so a run cut at CUT_MS still covers it: highest
# 5 at 24 ms under DRIVEN.
PEAK_TORQUE = 'peak: {signal: torque_nm, at_most: 10.0}\n    for_ms: 30'
WAIVED = {'enable_only_in_drive': 'the drive enables it in IDLE'}
UNUSED = {'torque_within_limit': 'kept from an older drive'}
MIXED_WAIVE = {**WAIVED, **UNUSED}
MIXED_EXPECT = (PEAK_TORQUE, NEVER_IDLE, STATES_IDLE)

# id: (waive, expect, spans, until_ms)
CASES = {
    'clean': ({}, (), (), DURATION_MS),
    'expectations pass': ({}, (HOLD_IDLE, NEVER_FAULT), (), DURATION_MS),
    'invariant broken': ({}, (), ((24, 30, ENABLED),), DURATION_MS),
    'invariant broken, run cut short': ({}, (), ((24, 30, ENABLED),), CUT_MS),
    'invariant waived': (WAIVED, (), ((24, 30, ENABLED),), DURATION_MS),
    'unused waiver': (UNUSED, (), (), DURATION_MS),
    'two unused waivers': ({**UNUSED, 'bot_detected': 'kept too'}, (), (), DURATION_MS),
    'expectation fails': ({}, (HOLD_IDLE, NEVER_IDLE), (), DURATION_MS),
    'expectation fails, run cut short': ({}, (NEVER_IDLE,), (), CUT_MS),
    'expectation not evaluated': ({}, (STATES_IDLE,), (), CUT_MS),
    'boot shutdown-open accepted': ({}, (NEVER_FAULT,), (BOOT,), DURATION_MS),
    'mixed': (MIXED_WAIVE, MIXED_EXPECT, ((24, 30, DRIVEN),), DURATION_MS),
    'mixed, run cut short': (MIXED_WAIVE, MIXED_EXPECT, ((24, 30, DRIVEN),), CUT_MS),
    'mixed, boot shutdown-open': (MIXED_WAIVE, (*MIXED_EXPECT, NEVER_FAULT),
                                  ((24, 30, DRIVEN), BOOT), DURATION_MS),
}


def case(name: str) -> tuple[scenario_file.ScenarioFile, verdict.Verdict]:
    waive, expect, spans, until_ms = CASES[name]
    sf = load(waive, *expect)
    return sf, verdict.judge(sf, rows(*spans, until_ms=until_ms))


def table(values: dict[str, object]) -> pytest.MarkDecorator:
    return pytest.mark.parametrize('name, want', list(values.items()), ids=list(values))


# ------------------------------------------------------------- failed, status


@table({'clean': False, 'expectations pass': False, 'invariant broken': True,
        'invariant broken, run cut short': True, 'invariant waived': False,
        'unused waiver': True, 'two unused waivers': True, 'expectation fails': True,
        'expectation fails, run cut short': True, 'expectation not evaluated': False,
        'boot shutdown-open accepted': False, 'mixed': True, 'mixed, run cut short': True})
def test_a_broken_check_or_unused_waiver_fails_the_run_and_a_waived_or_unevaluated_one_does_not(
        name, want):
    _, v = case(name)
    assert v.failed is want
    assert v.status == ('failed' if want else 'passed')
    assert v.meta()['status'] == v.status


def test_judge_holds_the_run_to_every_invariant_and_each_expectation_in_file_order():
    sf, v = case('mixed')
    assert [r.name for r in v.invariants] == list(INVARIANTS)
    assert [(r.index, r.result) for r in v.expectations] == [(0, 'pass'), (1, 'fail'),
                                                             (2, 'pass')]
    assert {r.name: r.result for r in v.invariants if r.result != 'pass'} == {
        'no_torque_outside_drive': 'fail', 'enable_only_in_drive': 'waived',
        'torque_within_limit': 'unused_waiver'}
    assert [r.line for r in v.expectations] == [e.line for e in sf.expectations]


def test_an_empty_run_breaks_no_invariant_and_evaluates_no_expectation():
    v = verdict.judge(load({}, NEVER_FAULT, STATES_IDLE), [])
    assert [r.result for r in v.invariants] == ['pass'] * len(INVARIANTS)
    assert [r.result for r in v.expectations] == ['not_evaluated', 'not_evaluated']
    assert (v.status, v.accepted) == ('passed', ())
    assert v.headline() == ('checks passed: 9/9 invariants passed, 0/2 expectations passed, '
                            '2 not evaluated')


def test_a_waiver_a_run_cut_short_never_reached_does_not_fail_it():
    # The drive breaks it at 45 ms, on purpose; the car model failed at 30.
    v = verdict.judge(load(WAIVED), rows((45, 51, ENABLED), until_ms=CUT_MS))
    assert v.meta()['counts']['unused_waivers'] == 0
    assert v.failed is False


# ------------------------------------------------------------------ accepted

SHUTDOWN_OPEN_AT = {'fw_faults': SHUTDOWN_OPEN_BIT}


@pytest.mark.parametrize('expect, spans, accepted, results', [
    ((NEVER_FAULT,), (BOOT,), True, ['pass']),
    # The last tick before ShutdownParams::debounce_ms (10), and the first after.
    ((NEVER_FAULT,), ((9, 10, SHUTDOWN_OPEN_AT),), True, ['pass']),
    ((NEVER_FAULT,), ((12, 13, SHUTDOWN_OPEN_AT),), False, ['fail']),
    (('never: {fault: SHUTDOWN_OPEN}',), (BOOT,), True, ['pass']),
    # Any fault expectation, even of another fault, sees the boot masked.
    (('never: {fault: APPS_IMPLAUSIBLE}',), (BOOT,), True, ['pass']),
    ((NEVER_FAULT,), (), False, ['pass']),
    ((NEVER_FAULT,), ((0, 10, {'fw_faults': APPS_IMPLAUSIBLE_BIT}),), False, ['fail']),
    ((HOLD_IDLE, STATES_IDLE), (BOOT,), False, ['pass', 'pass']),
    ((), (BOOT,), False, []),
], ids=['boot ticks', 'tick 9', 'tick 12', 'named fault', 'another fault expected',
        'no shutdown-open', 'another fault at boot', 'no fault expectation', 'no expectations'])
def test_boot_shutdown_open_is_accepted_only_where_a_fault_expectation_skipped_it(
        expect, spans, accepted, results):
    v = verdict.judge(load({}, *expect), rows(*spans))
    assert v.accepted == ((BOOT_ACCEPTED,) if accepted else ())
    assert v.meta()['accepted'] == list(v.accepted)
    assert [r.result for r in v.expectations] == results
    # SHUTDOWN_OPEN with no torque and no enable breaks no invariant.
    assert all(r.result == 'pass' for r in v.invariants)


def test_the_accepted_line_names_the_debounce_it_stands_on():
    assert BOOT_ACCEPTED == ('SHUTDOWN_OPEN on the ticks before 10 ms: the shutdown loop reads '
                             'UNKNOWN until both taps have had their debounce '
                             '(shutdown_loop.cpp)')


# ---------------------------------------------------------------------- meta

COUNT_KEYS = ('invariants_failed', 'invariants_waived', 'unused_waivers', 'expectations_passed',
              'expectations_failed', 'expectations_not_evaluated')


@table({'clean': (0, 0, 0, 0, 0, 0), 'expectations pass': (0, 0, 0, 2, 0, 0),
        'invariant broken': (1, 0, 0, 0, 0, 0), 'invariant waived': (0, 1, 0, 0, 0, 0),
        'unused waiver': (0, 0, 1, 0, 0, 0), 'two unused waivers': (0, 0, 2, 0, 0, 0),
        'expectation fails': (0, 0, 0, 1, 1, 0), 'expectation not evaluated': (0, 0, 0, 0, 0, 1),
        'mixed': (1, 1, 1, 2, 1, 0), 'mixed, run cut short': (1, 1, 0, 1, 1, 1)})  # a cut run judges no waiver unused
def test_meta_counts_each_kind_of_result(name, want):
    _, v = case(name)
    assert v.meta()['counts'] == dict(zip(COUNT_KEYS, want, strict=True))


def test_meta_has_each_result_and_round_trips_through_strict_json():
    _, v = case('mixed, run cut short')
    meta = v.meta()
    assert list(meta) == ['status', 'invariants', 'expectations', 'counts', 'accepted']
    assert meta['invariants'] == [r.meta() for r in v.invariants]
    assert meta['expectations'] == [r.meta() for r in v.expectations]
    assert meta['invariants'][1] == {
        'name': 'enable_only_in_drive', 'basis': 'design',
        'rule': 'control_loop.cpp ArbitrateTorque', 'result': 'waived', 'ticks': 2,
        'first_ms': 24, 'message': 'inverter_enable set and 5 N.m sent in IDLE at 24 ms',
        'reason': 'the drive enables it in IDLE'}
    assert meta['expectations'][2] == {
        'index': 2, 'line': 13, 'text': 'states IDLE', 'result': 'not_evaluated', 'at_ms': None,
        'measured': 'the run stopped at 27 ms, before the window ends'}
    assert json.loads(json.dumps(meta, allow_nan=False)) == meta


def test_a_trace_read_back_gets_the_verdict_of_the_rows_it_was_written_from(tmp_path):
    # read_trace gives every cell as a float: the checks must judge it the same.
    sf, want = case('mixed, boot shutdown-open')
    _, _, spans, until_ms = CASES['mixed, boot shutdown-open']
    written = rows(*spans, until_ms=until_ms)
    report.write_trace(cosim.Result(scenario=NAME, rows=written), tmp_path / 'trace.csv')
    back = report.read_trace(tmp_path / 'trace.csv')
    assert isinstance(back[0]['t_ms'], float) and isinstance(back[0]['fw_faults'], float)
    got = verdict.judge(sf, back)
    assert want.accepted == (BOOT_ACCEPTED,)
    assert got.meta() == want.meta()
    assert got.lines(sf) == want.lines(sf)
    assert got.events(END_MS) == want.events(END_MS)


# ------------------------------------------------------------------ headline


@table({
    'clean': 'checks passed: 9/9 invariants passed',
    'expectations pass': 'checks passed: 9/9 invariants passed, 2/2 expectations passed',
    'invariant broken': 'checks failed: 8/9 invariants passed',
    'invariant waived': 'checks passed: 8/9 invariants passed, 1 waived',
    'unused waiver': 'checks failed: 8/9 invariants passed, 1 unused waiver',
    'two unused waivers': 'checks failed: 7/9 invariants passed, 2 unused waivers',
    'expectation fails': 'checks failed: 9/9 invariants passed, 1/2 expectations passed',
    'expectation not evaluated': ('checks passed: 9/9 invariants passed, 0/1 expectations '
                                  'passed, 1 not evaluated'),
    'mixed': ('checks failed: 6/9 invariants passed, 1 waived, 1 unused waiver, 2/3 '
              'expectations passed'),
    # Cut short, the run may stop before the tick a waiver is for: not judged.
    'mixed, run cut short': ('checks failed: 7/9 invariants passed, 1 waived, 1/3 expectations '
                             'passed, 1 not evaluated'),
})
def test_the_headline_counts_what_passed_and_names_the_rest(name, want):
    _, v = case(name)
    assert v.headline() == want


# --------------------------------------------------------------------- lines


def test_each_check_that_did_not_pass_gets_one_line_led_by_its_place_in_the_file():
    sf, v = case('mixed, run cut short')
    source = text(MIXED_WAIVE, MIXED_EXPECT)
    waived, unused = line_of(source, 'enable_only_in_drive:'), line_of(source, 'torque_within')
    never, states = line_of(source, f'- {NEVER_IDLE}'), line_of(source, f'- {STATES_IDLE}')
    assert (waived, unused, never, states) == (6, 7, 12, 13)
    assert v.lines(sf) == [
        # An invariant is the run's, not a line's: the file alone.
        f'{DISPLAY}: FAIL: 5 N.m with the inverter enabled in IDLE at 24 ms '
        '(EV.9.6.1 (2027: EV.10.6.1)) [no_torque_outside_drive]',
        # A waiver at its key, so the editor opens on the name.
        f'{DISPLAY}:{waived}: waived: inverter_enable set and 5 N.m sent in IDLE at 24 ms; the '
        'file says: the drive enables it in IDLE [enable_only_in_drive]',
        # The run was cut short, so torque_within_limit's waiver (line {unused}) is not
        # judged and gets no line.
        # An expectation at its item's dash; the passing peak has none.
        f'{DISPLAY}:{never}: FAIL: expect[1] never state IDLE from the start until the end: at '
        '0 ms: IDLE [expect_failed]',
        f'{DISPLAY}:{states}: not evaluated: expect[2] states IDLE: the run stopped at 27 ms, '
        'before the window ends [not_evaluated]',
    ]


@pytest.mark.parametrize('name', ['clean', 'expectations pass', 'boot shutdown-open accepted'])
def test_a_run_that_passes_every_check_has_no_lines(name):
    sf, v = case(name)
    assert v.lines(sf) == []


def test_the_lines_lead_with_the_files_repo_path(tmp_path):
    sf = load(UNUSED, NEVER_IDLE, path=tmp_path / 'scenarios' / DISPLAY, repo=tmp_path)
    assert sf.display == f'scenarios/{DISPLAY}'
    assert verdict.judge(sf, rows()).lines(sf) == [
        f'scenarios/{DISPLAY}:6: FAIL: waive.torque_within_limit: waived, but the run never broke '
        'it: the drive no longer tests what its waiver says (or delete the waiver) '
        '[unused_waiver]',
        f'scenarios/{DISPLAY}:9: FAIL: expect[0] never state IDLE from the start until the end: '
        'at 0 ms: IDLE [expect_failed]']


def test_a_waivers_line_is_its_key_even_with_the_reason_on_the_next_line():
    waive = {name: f'\n    {reason}' for name, reason in MIXED_WAIVE.items()}
    source = text(waive, ())
    sf = scenario_file.loads(source.encode(), NAME)
    assert sf.waive == MIXED_WAIVE
    keys = [line_of(source, f'{name}:') for name in MIXED_WAIVE]
    assert keys == [6, 8]
    lines = verdict.judge(sf, rows((24, 30, ENABLED))).lines(sf)
    assert [line.split(': ', 1)[0] for line in lines] == [f'{DISPLAY}:{n}' for n in keys]


# -------------------------------------------------------------------- events


def test_each_failure_is_an_event_at_its_first_tick_and_the_verdict_one_at_the_end():
    _, v = case('mixed')
    inv = {r.name: r for r in v.invariants}
    ex = v.expectations
    assert v.events(END_MS) == [
        Event(24, ERROR, 'check.invariant',
              f'no_torque_outside_drive broken: {inv["no_torque_outside_drive"].message}'),
        Event(24, WARNING, 'check.invariant',
              f'enable_only_in_drive broken, waived: {inv["enable_only_in_drive"].message}'),
        # An unused waiver has no tick of its own: the run's end.
        Event(END_MS, ERROR, 'check.invariant',
              f'torque_within_limit: {inv["torque_within_limit"].message}'),
        Event(24, INFO, 'check.expect', f'expect[0] pass: {ex[0].text}: highest 5 at 24 ms'),
        # At 0 ms, not moved to the end: 0 is a tick.
        Event(0, ERROR, 'check.expect', f'expect[1] fail: {ex[1].text}: at 0 ms: IDLE'),
        # states rests on no one tick: the end.
        Event(END_MS, INFO, 'check.expect', f'expect[2] pass: {ex[2].text}: IDLE'),
        Event(END_MS, ERROR, 'check', v.headline()),
    ]


def test_a_not_evaluated_expectation_is_a_warning_at_the_end_of_the_run_it_had():
    _, v = case('expectation not evaluated')
    assert v.events(27) == [
        Event(27, WARNING, 'check.expect', 'expect[0] not_evaluated: states IDLE: the run '
              'stopped at 27 ms, before the window ends'),
        Event(27, INFO, 'check', 'checks passed: 9/9 invariants passed, 0/1 expectations '
              'passed, 1 not evaluated')]


def test_a_passing_run_has_only_its_expectations_and_the_verdict_as_events():
    _, v = case('expectations pass')
    assert [(e.t_ms, e.level, e.name) for e in v.events(END_MS)] == [
        (END_MS, INFO, 'check.expect'), (END_MS, INFO, 'check.expect'),
        (END_MS, INFO, 'check')]
    _, clean = case('clean')
    assert clean.events(END_MS) == [
        Event(END_MS, INFO, 'check', 'checks passed: 9/9 invariants passed')]


# --------------------------------------------------------------- with_events

START = Event(0, INFO, 'run', f'scenario {NAME} starts')
IDLE_AT_0 = Event(0, INFO, 'vcu.state', 'start -> IDLE')
MARK_24 = Event(24, INFO, 'scenario', 'mark enable (t_ms 24)')
MARK_57 = Event(END_MS, INFO, 'scenario', 'mark late (t_ms 56)')
END = Event(END_MS, INFO, 'run', f'scenario {NAME} completed')


def test_the_runs_end_stays_last_and_the_rest_keep_time_order_and_their_order_at_a_tie():
    _, v = case('mixed')
    inv_fail, waived, unused, peak, never, states, headline = v.events(END_MS)
    assert verdict.with_events([START, IDLE_AT_0, MARK_24, MARK_57, END], v) == [
        START, IDLE_AT_0, never,
        MARK_24, inv_fail, waived, peak,
        # The verdict's events at the end come after the run's own, before its end.
        MARK_57, unused, states, headline,
        END]


def test_with_events_takes_the_end_time_from_the_runs_last_event():
    _, v = case('expectation not evaluated')
    end = Event(27, FATAL, 'model.failure', 'car model failed: step 10')
    out = verdict.with_events([START, end], v)
    assert out == [START, *v.events(27), end]
    assert out[-2] == Event(27, INFO, 'check', v.headline())


def test_with_events_leaves_an_empty_run_log_empty():
    _, v = case('mixed')
    assert verdict.with_events([], v) == []


# --------------------------------------------------------------- summary.txt


def summary_lines(name: str, checks: bool = True) -> list[str]:
    """summary.txt's lines for a case, with meta['checks'] as __main__
    builds it (or none, as for a harness error)."""
    sf, v = case(name)
    result = cosim.Result(scenario=NAME, rows=rows(*CASES[name][2], until_ms=CASES[name][3]))
    summary = cosim.summarize(result, STATE_NAMES.__getitem__, lambda mask: [
        fault for fault, code in VCU_FAULTS.items() if mask >> (code - 1) & 1])
    meta: dict = {'scenario_file': {'path': f'boards/VCU/Sil/scenarios/{DISPLAY}',
                                    'sha256': sf.sha256},
                  'scenario_warnings': [], 'scenario_allowed': []}
    if checks:
        meta['checks'] = {**v.meta(), 'headline': v.headline(), 'report': v.lines(sf)}
    return report.summary_text(summary, result, meta, wall_time=False).splitlines()


def test_the_summary_has_the_headline_then_each_report_line_then_what_was_accepted():
    sf, v = case('mixed, boot shutdown-open')
    lines = summary_lines('mixed, boot shutdown-open')
    assert lines[0] == f'scenario {NAME}: completed after 0.060 s simulated'
    assert '  faults seen: SHUTDOWN_OPEN' in lines
    assert len(v.lines(sf)) == 4
    assert lines[lines.index('  scenario warnings: none') + 1:] == [
        '  checks failed: 6/9 invariants passed, 1 waived, 1 unused waiver, 3/4 expectations '
        'passed',
        *(f'    {line}' for line in v.lines(sf)),
        f'    accepted: {BOOT_ACCEPTED}']


def test_a_clean_run_has_only_the_headline_after_the_scenario_file():
    assert summary_lines('clean')[-2:] == ['  scenario warnings: none',
                                           '  checks passed: 9/9 invariants passed']


def test_a_run_judged_by_no_checks_has_no_checks_line():
    lines = summary_lines('mixed, boot shutdown-open', checks=False)
    assert lines[-1] == '  scenario warnings: none'
    assert not any('checks' in line or 'accepted' in line for line in lines)


# ---------------------------------------------------------------- exit status


def test_a_failed_check_has_its_own_exit_status_3():
    codes = (cli.EXIT_COMPLETED, cli.EXIT_PLANT_FAILED, cli.EXIT_HARNESS, cli.EXIT_CHECKS_FAILED)
    assert cli.EXIT_CHECKS_FAILED == 3
    assert len(set(codes)) == len(codes)
    # No run status maps to it: only a verdict can give 3.
    assert cli.EXIT_CHECKS_FAILED not in cli.EXIT_FOR_STATUS.values()


def test_the_usage_documents_exit_3_and_that_it_wins_over_a_car_model_failure():
    doc = ' '.join(cli.__doc__.split())
    assert '3 a check failed: an invariant or one of the file\'s expectations' in doc
    assert '3 wins over 1' in doc
