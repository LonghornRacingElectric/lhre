"""The fault tools' scoring: whether a run tested its tier's speed (#207)."""

import csv
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS

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


def test_geofence_is_not_scored_against_its_tier(tmp_path, monkeypatch):
    """It trips where the car crosses the fence, so the summary must not call it slow."""
    out = tmp_path / 'results.csv'
    monkeypatch.setattr(analyze, 'read', lambda bag: {})
    monkeypatch.setattr(analyze, 'score', lambda data, case, fence: {
        'case': case, 'result': 'caught', 'expected': 'GEOFENCE',
        'tripped': 'GEOFENCE', 'v_at_fault': '6.18'})
    monkeypatch.setattr(sys, 'argv', [
        'analyze_run.py', str(tmp_path / 'bag'), '--id', 'geofence_v8_r1',
        '--case', 'geofence', '--v-max', '8', '--csv', str(out)])
    assert analyze.main() == 0
    with out.open(newline='') as f:
        (row,) = csv.DictReader(f)
    assert row['v_at_fault'] == '6.18'
    assert row['at_speed'] == ''


def _odom(x, v):
    return NS(pose=NS(pose=NS(position=NS(x=x, y=0.0))),
              twist=NS(twist=NS(linear=NS(x=v, y=0.0))))


@pytest.mark.parametrize(('latency', 'distance', 'within_time', 'within_pdr'), [
    (.05, 4., 'True', 'True'),
    (.46, 4., 'False', 'True'),
    (.05, 6., 'True', 'False'),
])
def test_fault_scores_deadline_and_stop_distance_independently(
        latency, distance, within_time, within_pdr):
    data = {
        '/lhr/mission/status': [(1., NS(data='DRIVING'))],
        '/lhr/vehicle/odom_truth': [
            (1., _odom(0., 5.)), (5., _odom(20., 5.)),
            (6., _odom(20.+distance, 0.)), (7., _odom(20.+distance, 0.))],
        '/lhr/sim/fault': [(5., NS(data='start ekf_kill ODOM_STALE'))],
        '/lhr/safety/status': [(5.+latency, NS(
            message='ODOM_STALE', values=[NS(key='latched', value='True')]))],
    }
    row = analyze.score(data, 'ekf_kill', 20.)
    assert row['trip_limit_s'] == '0.450'
    assert row['within_trip_limit'] == within_time
    assert row['pdr_dist_m'] == '5.33'
    assert row['within_pdr'] == within_pdr


@pytest.mark.parametrize(('lap', 'finished', 'trip', 'expected'), [
    (True, True, False, 'clean'),
    (False, True, False, 'incomplete_lap'),
    (True, False, False, 'incomplete_lap'),
    (True, True, True, 'false_trip'),
])
def test_clean_run_requires_a_completed_lap_and_no_safety_trip(lap, finished, trip, expected):
    data = {
        '/lhr/mission/status': [(1., NS(data='DRIVING'))],
        '/lhr/vehicle/odom_truth': [(1., _odom(0., 5.)), (5., _odom(20., 0.))],
        '/lhr/metrics/lap_complete': [(4., NS(data=lap))],
        '/lhr/safety/status': [(3., NS(message='NONE', values=[]))],
    }
    if finished:
        data['/lhr/mission/status'].append((5., NS(data='FINISHED')))
    if trip:
        data['/lhr/safety/status'] = [(4.5, NS(
            message='STEER_TRACKING', values=[NS(key='latched', value='True')]))]
    assert analyze.score(data, 'clean', 20.)['result'] == expected


@pytest.mark.parametrize(('rows', 'expected_code'), [
    (['clean_s1'], 0), ([], 1), (['clean_s1', 'clean_s1'], 1),
])
def test_campaign_acceptance_requires_exactly_one_result_per_planned_run(
        tmp_path, monkeypatch, rows, expected_code):
    path = tmp_path / 'results.csv'
    expected = tmp_path / 'expected.txt'
    expected.write_text('clean_s1\n')
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=analyze.FIELDS, restval='')
        writer.writeheader()
        for run in rows:
            writer.writerow({'id': run, 'case': 'clean', 'result': 'clean', 'passed': 'True'})
    monkeypatch.setattr(sys, 'argv', ['summarize.py', str(path), '--expected', str(expected)])
    assert summarize.main() == expected_code


@pytest.mark.parametrize(('overrides', 'expected'), [
    ({}, True),
    ({'v_at_fault': '7.49'}, False),
    ({'within_trip_limit': 'False'}, False),
    ({'within_pdr': 'False'}, False),
    ({'result': 'wrong_check'}, False),
])
def test_fault_acceptance_requires_speed_deadline_stop_and_expected_check(overrides, expected):
    row = {'case': 'lidar_drop', 'result': 'caught', 'at_speed': 'True',
           'v_max': '8', 'v_at_fault': '7.50', 'within_trip_limit': 'True',
           'within_pdr': 'True'}
    row.update(overrides)
    assert analyze.passed(row) is expected


def test_missed_fault_still_records_the_speed_it_hit():
    """A fault nothing caught still says which tier it tested."""
    times = [1.0 + 0.1 * i for i in range(100)]
    data = {
        '/lhr/mission/status': [(0.0, NS(data='READY')), (1.0, NS(data='DRIVING'))],
        '/lhr/vehicle/odom_truth': [(t, _odom(7.7 * (t - 1.0), 7.7)) for t in times],
        '/lhr/sim/fault': [(5.0, NS(data='start ekf_kill ODOM_STALE'))],
    }
    row = analyze.score(data, 'ekf_kill', 20.0)
    assert row['result'] == 'missed'
    assert row['v_at_fault'] == '7.70'


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
