"""python -m vcusil end to end, with the toy car standing in for the FMU."""

import json
import os

import pytest
from fakes import ToyCar

from vcusil import __main__ as cli
from vcusil import firmware, plant, report


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


def test_a_failed_car_model_exits_1(run_cli):
    status, out, meta, _, _ = run_cli(fail_at=100)
    assert status == cli.EXIT_PLANT_FAILED
    assert meta['status'] == 'plant_failed' and 'step 100' in meta['failure']
    assert len(report.read_trace(out / 'trace.csv')) == 101


def test_options_reach_the_run(run_cli):
    status, out, meta, made, modes = run_cli('--brake-psi-full', '700', '--soc', '0.5',
                                             '--event-mode', '2')
    assert status == cli.EXIT_COMPLETED
    assert {r['u_brake'] for r in report.read_trace(out / 'trace.csv')} == {350.0 / 700.0, 0.0}
    assert [p.soc_start for p in made] == [0.5] and modes == [2]
    assert meta['brake_psi_at_full_plant_brake'] == 700.0 and meta['event_mode'] == 2


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


def test_an_unexpected_error_is_a_harness_error_with_meta(run_cli, monkeypatch):
    def boom(*_args, **_kw):
        raise RuntimeError('disk full')
    monkeypatch.setattr(cli.cosim, 'summarize', boom)
    status, _, meta, _, _ = run_cli()
    assert status == cli.EXIT_HARNESS
    assert meta['status'] == 'harness_error' and 'disk full' in meta['failure']
