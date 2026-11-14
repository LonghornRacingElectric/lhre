"""python -m vcusil end to end, with the toy car standing in for the FMU."""

import hashlib
import json
import os

import pytest
from fakes import ToyCar
from test_mcaplog import read_all
from test_scenario_file import without_expect

from vcusil import __main__ as cli
from vcusil import check, firmware, plant, report, scenario_file

LAUNCH = scenario_file.SCENARIO_DIR / 'launch.yml'


class FakePlant(ToyCar):
    made: list = []
    fail_at = None

    def __init__(self, fmu_path, soc_start=0.9):
        super().__init__(fail_at_step=FakePlant.fail_at)
        self.soc_start, self.peak_timer, self.guid = soc_start, True, 'fake'
        self.truth_units, self.truth_missing, self.slip_from_states = {}, [], False
        FakePlant.made.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


@pytest.fixture
def run_cli(lib, monkeypatch, tmp_path):
    modes = []

    class RecordingFirmware(firmware.Firmware):
        def __init__(self, lib, event_mode=0):
            modes.append(event_mode)
            super().__init__(lib, event_mode)

    monkeypatch.setattr(plant, 'Plant', FakePlant)
    monkeypatch.setattr(firmware, 'Firmware', RecordingFirmware)
    fmu = tmp_path / 'car.fmu'
    fmu.write_bytes(b'not an fmu')

    def run(*args, fail_at=None, lib_path=None, out_name='out'):
        FakePlant.fail_at, FakePlant.made = fail_at, []
        modes.clear()
        out = tmp_path / out_name
        status = cli.main(['--lib', lib_path or os.environ['VCU_SIL_LIB'], '--fmu', str(fmu),
                           '--out', str(out), '--allow-unverified-fmu', '--no-plot', *args])
        meta = json.loads((out / 'meta.json').read_text())
        return status, out, meta, FakePlant.made, list(modes)
    return run


def test_a_completed_run_exits_0_and_writes_everything(run_cli):
    status, out, meta, made, modes = run_cli()
    assert status == cli.EXIT_COMPLETED and meta['status'] == 'completed'
    assert meta['summary']['first_drive_ms'] is not None
    assert meta['fmu']['verified'] is False  # the fake has no manifest
    assert meta['library']['build']['git_sha']
    for name in ('trace.csv', 'summary.txt', 'console.log', 'run.mcap'):
        assert (out / name).stat().st_size > 0, name
    # Without --can-table the frames stay raw, and the run says so.
    assert meta['can_table'] is None
    assert meta['mcap']['per_topic']['/can/raw'] > 0
    assert modes == [0] and [p.soc_start for p in made] == [0.9]
    # The scenario is recorded by path, hash and content, and copied.
    assert meta['scenario'] == 'launch'
    assert meta['scenario_file'] == {'path': 'boards/VCU/Sil/scenarios/launch.yml',
                                     'sha256': hashlib.sha256(LAUNCH.read_bytes()).hexdigest(),
                                     'schema': 'lhre.vcu-sil-scenario.v1'}
    assert meta['scenario_parameters']['duration_ms'] == 7000
    assert meta['scenario_parameters']['at'][1] == {'t_ms': 200, 'mark': 'precharge',
                                                    'hvc_state': 'PRECHARGING'}
    assert meta['scenario_warnings'] == [] and meta['scenario_allowed'] == []
    assert meta['setup_from'] == {'event_mode': 'scenario', 'soc_start': 'scenario'}
    assert (out / 'scenario.yml').read_bytes() == LAUNCH.read_bytes()
    marks = [e for e in meta['events'] if e['name'] == 'scenario']
    assert marks[0] == {'t_ms': 201, 'level': 2, 'name': 'scenario',
                        'message': 'mark precharge (t_ms 200)'}
    summary = (out / 'summary.txt').read_text()
    assert ('  scenario file boards/VCU/Sil/scenarios/launch.yml (sha256 '
            f'{meta["scenario_file"]["sha256"][:12]})\n'
            '  scenario description: Power up, ready to drive on the brake, full pedal from rest '
            'for 4 s, coast.\n'
            '  scenario warnings: none\n') in summary


def test_the_same_run_writes_the_same_run_log(run_cli, tmp_path):
    from mcap.reader import make_reader
    _, out, meta, _, _ = run_cli()
    first = (out / 'run.mcap').read_bytes()
    _, out, _, _, _ = run_cli(out_name='elsewhere')  # nor where it is written
    assert (out / 'run.mcap').read_bytes() == first
    # The log carries the run's metadata, but no wall-clock time.
    with open(out / 'run.mcap', 'rb') as f:
        reader = make_reader(f)
        attached = {a.name: a.data for a in reader.iter_attachments()}
        tags = {m.name: m.metadata for m in reader.iter_metadata()}
    logged = json.loads(attached['meta.json'])
    assert 'wall_s' not in logged and logged['summary'] == meta['summary']
    assert ' simulated\n' in attached['summary.txt'].decode()
    assert tags['vcusil']['firmware_dirty'] in ('True', 'False')
    assert attached['scenario.yml'] == LAUNCH.read_bytes()
    assert tags['vcusil']['scenario_sha256'] == meta['scenario_file']['sha256']
    assert tags['vcusil']['scenario_path'] == 'boards/VCU/Sil/scenarios/launch.yml'


def test_a_failed_car_model_exits_1(run_cli):
    status, out, meta, _, _ = run_cli(fail_at=100)
    assert status == cli.EXIT_PLANT_FAILED
    assert meta['status'] == 'plant_failed' and 'step 100' in meta['failure']
    # The run stopped at 300 ms, before any of launch.yml's windows end: none
    # of them is judged, so the checks fail nothing.
    assert meta['checks']['status'] == 'passed'
    assert {e['result'] for e in meta['checks']['expectations']} == {'not_evaluated'}
    assert len(report.read_trace(out / 'trace.csv')) == 101


LATE_DRIVE = ('    not_before_ms: 18\n    within_ms: 24\n', '    within_ms: 10\n')


def _late(tmp_path, *more) -> str:
    """launch.yml with DRIVE due 10 ms after the RTD press's sample: the
    firmware's debounce takes 21, so the expectation fails."""
    text = LAUNCH.read_text().replace(*LATE_DRIVE)
    for old, new in more:
        text = text.replace(old, new)
    return _write(tmp_path / 'late.yml', text)


def test_a_failed_expectation_exits_3_and_says_where(run_cli, tmp_path, capsys):
    path = _late(tmp_path)
    status, out, meta, _, _ = run_cli('--scenario', path)
    assert status == cli.EXIT_CHECKS_FAILED == 3
    # The run itself completed: the firmware, not the harness, failed.
    assert meta['status'] == 'completed'
    checks = meta['checks']
    assert checks['status'] == 'failed'
    line = next(n for n, row in enumerate((tmp_path / 'late.yml').read_text().splitlines(), 1)
                if row == '  - reach: {state: DRIVE}')
    failure = (f'{path}:{line}: FAIL: expect[1] reach state DRIVE from rtd within 10 ms: first at '
               '1023 ms, 21 ms after rtd: 11 ms late [expect_failed]')
    assert checks['report'] == [failure]
    assert failure in capsys.readouterr().err
    summary = (out / 'summary.txt').read_text()
    assert ('  checks failed: 9/9 invariants passed, 3/4 expectations passed\n'
            f'    {failure}\n') in summary
    assert {'t_ms': 1023, 'level': 4, 'name': 'check.expect',
            'message': 'expect[1] fail: reach state DRIVE from rtd within 10 ms: first at 1023 '
                       'ms, 21 ms after rtd: 11 ms late'} in meta['events']
    # The run log says so too: its metadata, and /events in time order.
    messages, _, _, tags = read_all(out / 'run.mcap')
    assert tags['vcusil']['checks'] == 'failed'
    logged = [m['message'] for _, m in messages['/events']]
    assert logged[-2:] == ['checks failed: 9/9 invariants passed, 3/4 expectations passed',
                           'scenario late completed']


def test_an_unused_waiver_exits_3(run_cli, tmp_path, capsys):
    text = LAUNCH.read_text() + ('\nwaive:\n  bspd_not_tripped: "a stop that would trip the '
                                 'BSPD"\n')
    path = _write(tmp_path / 'waived.yml', text)
    status, _, meta, _, _ = run_cli('--scenario', path)
    assert status == cli.EXIT_CHECKS_FAILED
    result = next(r for r in meta['checks']['invariants'] if r['name'] == 'bspd_not_tripped')
    assert result['result'] == 'unused_waiver'
    assert result['reason'] == 'a stop that would trip the BSPD'
    assert '[unused_waiver]' in capsys.readouterr().err


def test_a_check_that_failed_before_the_car_model_did_exits_3(run_cli, tmp_path):
    # The run stops at 1500 ms, after the DRIVE expectation failed at 1023:
    # what the firmware did still counts, so 3 wins over 1.
    status, _, meta, _, _ = run_cli('--scenario', _late(tmp_path), fail_at=500)
    assert status == cli.EXIT_CHECKS_FAILED
    assert meta['status'] == 'plant_failed'
    results = [e['result'] for e in meta['checks']['expectations']]
    assert results == ['not_evaluated', 'fail', 'not_evaluated', 'not_evaluated']


def test_options_reach_the_run(run_cli, capsys):
    status, out, meta, made, modes = run_cli('--brake-psi-full', '500', '--soc', '0.5',
                                             '--event-mode', '2')
    # SKIDPAD's pedal map caps torque at 70 %, so launch.yml's 210 N.m peak
    # is not reached: the mode reached the firmware, and the check says so.
    assert status == cli.EXIT_CHECKS_FAILED and meta['status'] == 'completed'
    failed = [e for e in meta['checks']['expectations'] if e['result'] == 'fail']
    assert [(e['index'], e['measured']) for e in failed] == [(3, 'highest 147 at 2001 ms')]
    assert {r['u_brake'] for r in report.read_trace(out / 'trace.csv')} == {350.0 / 500.0, 0.0}
    assert [p.soc_start for p in made] == [0.5] and modes == [2]
    assert meta['brake_psi_at_full_plant_brake'] == 500.0 and meta['event_mode'] == 2
    assert meta['setup_from'] == {'event_mode': 'cli', 'soc_start': 'cli'}
    # The run's warnings use its --brake-psi-full: launch's 350 psi is over
    # 2/3 of 500, though not of the default 1500.
    assert [(w['code'], w['t_ms']) for w in meta['scenario_warnings']] == [('brake_high', 0)]
    assert ('brake_psi holds 350.0, over 2/3 of --brake-psi-full (333 psi at 500)'
            in meta['scenario_warnings'][0]['message'])
    assert '[brake_high]' in capsys.readouterr().err


def _write(path, text):
    path.write_text(text)
    return str(path)


def test_a_scenario_file_outside_the_repo(run_cli, tmp_path, capsys):
    # RTD is refused on purpose, which launch.yml's expectations would fail.
    text = without_expect(LAUNCH.read_text())
    text = text[:text.index('  # Off the brake')]  # stop before 1500 ms
    text = text.replace('duration_ms: 7000', 'duration_ms: 1500')
    text = text.replace('brake_psi: 350.0', 'brake_psi: 0.0')  # RTD refused
    path = tmp_path / 'no-brake.yml'
    status, out, meta, _, _ = run_cli('--scenario', _write(path, text))
    assert status == cli.EXIT_COMPLETED
    assert meta['scenario'] == 'no-brake'
    assert meta['scenario_file']['path'] == str(path.resolve())
    # The warning is recorded whole, at the line that presses RTD, and
    # printed in the same words when the run starts.
    line = next(n for n, row in enumerate(text.splitlines(), 1) if row == '    rtd_pressed: true')
    message = ('the filtered brake at the debounced edge (1023 ms) is about 0 psi; RTD needs 30 '
               'psi (brake_light_psi), so the firmware refuses it')
    assert meta['scenario_warnings'] == [{'code': 'rtd_without_brake', 't_ms': 1002, 'line': line,
                                          'message': message}]
    assert (f'{path}:{line}: warning: at 1002 ms: {message} [rtd_without_brake]\n'
            in capsys.readouterr().err)
    assert len(report.read_trace(out / 'trace.csv')) == 500
    assert meta['summary']['first_drive_ms'] is None
    assert '  scenario warnings: rtd_without_brake\n' in (out / 'summary.txt').read_text()


def test_setup_reaches_the_run(run_cli, tmp_path):
    text = LAUNCH.read_text().replace('event_mode: UNASSIGNED', 'event_mode: SKIDPAD')
    path = _write(tmp_path / 'skidpad.yml', text.replace('soc_start: 0.9', 'soc_start: 0.5'))
    _, _, meta, made, modes = run_cli('--scenario', path)
    assert modes == [2] and [p.soc_start for p in made] == [0.5]
    assert meta['setup_from'] == {'event_mode': 'scenario', 'soc_start': 'scenario'}
    assert meta['scenario_parameters']['setup'] == {'event_mode': 'SKIDPAD', 'soc_start': 0.5}
    _, _, meta, made, modes = run_cli('--scenario', path, '--event-mode', '3')
    assert modes == [3] and [p.soc_start for p in made] == [0.5] and meta['event_mode'] == 3
    assert meta['setup_from'] == {'event_mode': 'cli', 'soc_start': 'scenario'}
    # A file without setup gets the defaults.
    _, _, meta, made, modes = run_cli('--scenario', 'part-throttle')
    assert modes == [0] and [p.soc_start for p in made] == [0.9]
    assert meta['setup_from'] == {'event_mode': 'default', 'soc_start': 'default'}


def test_bad_scenarios_exit_2_before_running(tmp_path, capsys):
    def main(scenario):
        return cli.main(['--lib', 'x.so', '--fmu', 'x.fmu', '--out', str(tmp_path / 'o'),
                         '--scenario', scenario])
    assert main('no-such') == cli.EXIT_HARNESS
    err = capsys.readouterr().err
    assert "no scenario named 'no-such'" in err
    assert f'scenarios/ has: {", ".join(scenario_file.names())}. A path' in err
    bad = tmp_path / 'bad.yml'
    bad.write_text(LAUNCH.read_text().replace('t_ms: 200\n', 't_ms: 200\n    brake_psi: 4000.0\n'))
    assert main(str(bad)) == cli.EXIT_HARNESS
    err = capsys.readouterr().err
    line = next(n for n, text in enumerate(bad.read_text().splitlines(), 1) if '4000' in text)
    assert f'{bad}:{line}: error: at[1].brake_psi: 4000.0 is outside 0..3000' in err
    assert '[out_of_range]' in err and err.rstrip().endswith('1 error. Nothing ran.')
    assert not (tmp_path / 'o').exists()


def test_a_comment_only_change_keeps_the_trace(run_cli, tmp_path):
    """One path, first without the comment and then with it, so the comment
    is the only difference between the two runs."""
    from test_mcaplog import read_all
    copy = tmp_path / 'copy' / 'launch.yml'
    copy.parent.mkdir()
    plain = LAUNCH.read_bytes()
    commented = plain + b'# A comment changes the hash, not the drive.\n'
    runs = []
    for out_name, data in (('first', plain), ('second', commented)):
        copy.write_bytes(data)
        _, out, meta, _, _ = run_cli('--scenario', str(copy), out_name=out_name)
        runs.append((out, meta['scenario_file']['sha256']))
    (first, sha_plain), (second, sha_commented) = runs
    assert sha_plain == hashlib.sha256(plain).hexdigest()
    assert sha_commented == hashlib.sha256(commented).hexdigest()
    assert (first / 'trace.csv').read_bytes() == (second / 'trace.csv').read_bytes()
    assert (first / 'run.mcap').read_bytes() != (second / 'run.mcap').read_bytes()
    # run.mcap differs in the file and its hash, and nowhere else.
    messages, _, attached, tags = read_all(first / 'run.mcap')
    messages_c, _, attached_c, tags_c = read_all(second / 'run.mcap')
    assert messages == messages_c
    assert (attached['scenario.yml'], attached_c['scenario.yml']) == (plain, commented)
    assert tags_c['vcusil'] == {**tags['vcusil'], 'scenario_sha256': sha_commented}
    logged, logged_c = json.loads(attached['meta.json']), json.loads(attached_c['meta.json'])
    logged['scenario_file']['sha256'] = sha_commented
    assert logged == logged_c
    assert (attached['summary.txt'].replace(sha_plain[:12].encode(), sha_commented[:12].encode())
            == attached_c['summary.txt'])


def test_an_allowed_warning_is_recorded_but_not_printed(run_cli, capsys):
    status, out, meta, _, _ = run_cli('--scenario', 'brake-over-throttle')
    assert status == cli.EXIT_COMPLETED
    sf = scenario_file.load('brake-over-throttle')
    findings = check.lint(sf)
    assert meta['scenario_warnings'] == [] and not findings.active
    assert meta['scenario_allowed'] == [
        {'code': p.code, 'reason': reason, 't_ms': p.t_ms, 'line': p.line, 'message': p.message}
        for p, reason in findings.allowed]
    [allowed] = meta['scenario_allowed']
    assert allowed['code'] == 'brake_over_throttle' and isinstance(allowed['line'], int)
    assert allowed['reason'] == 'the point of this drive: brake-over-throttle must cut torque'
    assert '[brake_over_throttle]' not in capsys.readouterr().err
    assert ('  scenario warnings: none; allowed: brake_over_throttle\n'
            in (out / 'summary.txt').read_text())


def test_a_scenario_that_cannot_run_leaves_a_reused_out_as_it_was(run_cli, tmp_path):
    """It is refused before anything is written, so the previous run stays
    whole (and run.sh, seeing no new meta.json, does not claim it)."""
    _, out, _, _, _ = run_cli()
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    bad = tmp_path / 'bad.yml'
    bad.write_text(LAUNCH.read_text().replace('duration_ms: 7000', 'duration_ms: 0'))
    status, _, _, made, _ = run_cli('--scenario', str(bad))
    assert status == cli.EXIT_HARNESS and made == []
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before


def test_a_reused_out_directory_loses_the_old_run(run_cli, tmp_path):
    run_cli()
    stale = tmp_path / 'out' / 'trace.csv'
    assert stale.exists()
    status, out, meta, _, _ = run_cli(lib_path=str(tmp_path / 'no-such-lib.so'))
    assert status == cli.EXIT_HARNESS and meta['status'] == 'harness_error'
    assert not (out / 'trace.csv').exists()


def test_a_wrong_library_is_a_harness_error(run_cli, tmp_path):
    other = tmp_path / 'libother.so'
    other.write_bytes(b'\x7fELF not really')
    status, _, meta, _, _ = run_cli(lib_path=str(other))
    assert status == cli.EXIT_HARNESS and meta['status'] == 'harness_error'


@pytest.mark.parametrize('args', [['--soc', '90'], ['--soc', 'nan'], ['--brake-psi-full', '0'],
                                  ['--brake-psi-full', '-5'], ['--event-mode', '7']])
def test_bad_options_exit_2_before_running(args, tmp_path):
    with pytest.raises(SystemExit) as stop:
        cli.main(['--lib', 'x.so', '--fmu', 'x.fmu', '--out', str(tmp_path / 'o'), *args])
    assert stop.value.code == cli.EXIT_HARNESS
    assert not (tmp_path / 'o').exists()


@pytest.mark.parametrize('args, message', [
    (['--brake-psi-full', 'abc'], 'argument --brake-psi-full: abc is not a positive number'),
    (['--soc', 'abc'], 'argument --soc: abc is not a fraction in [0, 1] (0.9 means 90%)'),
])
def test_a_bad_number_names_no_private_function(args, message, tmp_path, capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main(['--lib', 'x.so', '--fmu', 'x.fmu', '--out', str(tmp_path / 'o'), *args])
    assert stop.value.code == cli.EXIT_HARNESS
    err = capsys.readouterr().err
    assert err.endswith(f'vcusil: error: {message}\n')
    assert '_positive' not in err and '_fraction' not in err
    assert not (tmp_path / 'o').exists()


def test_an_unexpected_error_is_a_harness_error_with_meta(run_cli, monkeypatch):
    def boom(*_args, **_kw):
        raise RuntimeError('disk full')
    monkeypatch.setattr(cli.cosim, 'summarize', boom)
    status, _, meta, _, _ = run_cli()
    assert status == cli.EXIT_HARNESS
    assert meta['status'] == 'harness_error' and 'disk full' in meta['failure']
