"""The fault tools' scoring: whether a run tested its tier's speed (#207)."""

import csv
import importlib.util
from pathlib import Path
import sys

import pytest

TOOLS = Path(__file__).parents[4] / 'testing/tools/fault_injection'


def _load(name):
    spec = importlib.util.spec_from_file_location(f'fault_{name}', TOOLS / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


analyze = _load('analyze_run')
summarize = _load('summarize')


@pytest.mark.parametrize('v, v_max, expected', [
    ('7.70', '8', 'True'),
    ('7.25', '8', 'True'),   # just over 90%
    ('7.15', '8', 'False'),  # just under
    ('5.30', '8', 'False'),  # the 11/1 oval runs labelled 8 m/s
    ('3.01', '3', 'True'),
    ('nan', '8', ''),        # no ground truth before the fault
    ('', '8', ''),
    ('5.0', '', ''),
    ('5.0', '0', ''),
])
def test_at_speed(v, v_max, expected):
    assert analyze.at_speed(v, v_max) == expected


def test_analysis_records_at_speed_against_the_runs_tier(tmp_path, monkeypatch):
    """The same fault speed passes the 5 m/s tier and fails the 8 m/s one."""
    out = tmp_path / 'results.csv'
    monkeypatch.setattr(analyze, 'read', lambda bag: {})
    monkeypatch.setattr(analyze, 'score', lambda data, case, fence: {
        'case': case, 'result': 'caught', 'expected': 'ODOM_STALE',
        'tripped': 'ODOM_STALE', 'v_at_fault': '5.30'})
    for run_id, v_max in (('ekf_kill_v5_r1', '5'), ('ekf_kill_v8_r1', '8')):
        monkeypatch.setattr(sys, 'argv', [
            'analyze_run.py', str(tmp_path / 'bag'), '--id', run_id,
            '--case', 'ekf_kill', '--v-max', v_max, '--csv', str(out)])
        assert analyze.main() == 0
    with out.open(newline='') as f:
        rows = {r['id']: r for r in csv.DictReader(f)}
    assert rows['ekf_kill_v5_r1']['at_speed'] == 'True'
    assert rows['ekf_kill_v8_r1']['at_speed'] == 'False'


def test_summary_shows_speed_at_fault_and_names_slow_runs(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'results.csv'
    common = {'case': 'ekf_kill', 'v_max': '8', 'result': 'caught', 'expected': 'ODOM_STALE',
              'tripped': 'ODOM_STALE', 'trip_latency_s': '0.28', 'stop_dist_m': '6.00',
              'pdr_dist_m': '9.00', 'within_pdr': 'True'}
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=analyze.FIELDS, restval='')
        writer.writeheader()
        writer.writerow({**common, 'id': 'ekf_kill_v8_r1',
                         'v_at_fault': '7.70', 'at_speed': 'True'})
        writer.writerow({**common, 'id': 'ekf_kill_v8_r2',
                         'v_at_fault': '5.30', 'at_speed': 'False'})
    monkeypatch.setattr(sys, 'argv', ['summarize.py', str(path)])
    summarize.main()
    out = capsys.readouterr().out
    assert '| `ekf_kill` | 8 | 6.50 | 2 | 2/2 |' in out
    slow = out.split('do not test their tier: ')[1].splitlines()[0]
    assert slow == 'ekf_kill_v8_r2 (5.30 m/s)'
