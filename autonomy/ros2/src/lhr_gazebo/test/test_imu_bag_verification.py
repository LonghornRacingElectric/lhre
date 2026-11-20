"""Incomplete captures fail a row while later bag cross-checks still run."""

import csv
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from test_imu_comparison import write_csv


def verification_module():
    """Load the independent verifier without starting ROS or a simulator."""
    path = Path(__file__).parents[1] / 'scripts/verify_imu_bags.py'
    spec = importlib.util.spec_from_file_location('verify_imu_bags', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def metrics(run, **overrides):
    """Write a measured EKF run whose errors match the synthetic poses."""
    write_csv(run / 'metrics.csv', [{
        'estimator': 'ekf', 'lap_start_stamp_s': '10', 'lap_time_s': '1',
        'duration_s': '2', 'mean_pos_error': '0.1', 'max_pos_error': '0.1',
        'mean_yaw_error': '0', **overrides,
    }])


def bracketed_poses():
    """Use exact timestamps so the scoring window and interpolation are pinned."""
    truth = np.array([[9., 0, 0, 0], [10., 1, 0, 0], [11., 2, 0, 0], [12., 3, 0, 0]])
    estimate = truth.copy()
    estimate[:, 1] += 0.1
    # Out-of-window errors must not affect the successful cross-check.
    estimate[0, 1] += 20
    estimate[-1, 1] += 20
    return estimate, truth


@pytest.mark.parametrize('problem', [
    'no_start', 'no_truth', 'one_truth', 'no_estimate',
    'no_brackets', 'bad_bag', 'nonfinite', 'empty_metrics',
])
def test_incomplete_run_is_a_failed_row(tmp_path, monkeypatch, problem):
    module = verification_module()
    run = tmp_path / 'early'
    metrics(run)
    estimate, truth = bracketed_poses()
    if problem == 'no_start':
        metrics(run, lap_start_stamp_s='', lap_time_s='')
    elif problem == 'no_truth':
        truth = np.empty((0, 4))
    elif problem == 'one_truth':
        truth = truth[:1]
    elif problem == 'no_estimate':
        estimate = np.empty((0, 4))
    elif problem == 'no_brackets':
        estimate[:, 0] += 100
    elif problem == 'nonfinite':
        truth[0, 1] = float('nan')
    elif problem == 'empty_metrics':
        (run / 'metrics.csv').write_text('estimator,max_pos_error\n')

    def poses(_):
        if problem == 'bad_bag':
            raise RuntimeError('unreadable MCAP')
        return estimate, truth

    monkeypatch.setattr(module, 'poses', poses)
    row = module.verify(run, 'native')
    assert row['crosscheck_passed'] is False
    assert row['campaign'] == 'native'
    assert row['id'] == 'early'
    assert row['error']
    assert not row.get('max_pos_error_bag')


def test_campaign_continues_after_failures_and_writes_a_stable_schema(tmp_path, monkeypatch):
    module = verification_module()
    runs = tmp_path / 'runs'
    metrics(runs / 'a-early', lap_start_stamp_s='', lap_time_s='')
    metrics(runs / 'b-measured')
    metrics(runs / 'c-mismatch', max_pos_error='2')
    metrics(runs / 'd-truth', estimator='truth')
    monkeypatch.setattr(module, 'poses', lambda _: bracketed_poses())
    out = tmp_path / 'crosscheck.csv'
    assert module.main([str(out), '--campaign', f'native={tmp_path}']) == 1
    with out.open() as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == list(module.FIELDS)
        rows = list(reader)
    assert [row['id'] for row in rows] == ['a-early', 'b-measured', 'c-mismatch']
    assert [row['crosscheck_passed'] for row in rows] == ['False', 'True', 'False']
    assert rows[0]['max_pos_error_bag'] == ''
    assert rows[1]['samples'] == '2'
    assert float(rows[1]['max_pos_error_bag']) == pytest.approx(0.1)
    assert rows[2]['error'] == 'reported and bag errors differ beyond tolerance'


def test_empty_bag_poses_have_a_stable_shape(tmp_path, monkeypatch):
    module = verification_module()

    reader = SimpleNamespace(open=lambda *_: None, get_all_topics_and_types=lambda: [],
                             has_next=lambda: False)
    monkeypatch.setattr(module, 'SequentialReader', lambda: reader)
    assert [array.shape for array in module.poses(tmp_path / 'empty')] == [(0, 4), (0, 4)]
