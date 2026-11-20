"""Gate evidence must retain failures and unknown measurements in both modes."""

import csv
import importlib.util
from pathlib import Path

import pytest


def comparison_module():
    """Load the report helper without starting ROS or a simulator."""
    path = Path(__file__).parents[1] / 'scripts' / 'compare_imu.py'
    spec = importlib.util.spec_from_file_location('compare_imu', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_csv(path, rows):
    """Write a synthetic gate or metrics table under the test directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_comparison_preserves_failed_and_missing_errors_and_omits_truth_baseline(tmp_path):
    module = comparison_module()
    checks = {name: 'pass' for name in module.CHECKS}
    failed = {**checks, 'ekf': 'fail'}
    write_csv(tmp_path / 'results.csv', [
        {**failed, 'id': 'noisy', 'kind': 'gate', 'passed': 'False', 'max_pos_error': '1.87'},
        {**checks, 'id': 'early', 'kind': 'gate', 'passed': 'False', 'max_pos_error': ''},
        {**checks, 'id': 'truth', 'kind': 'baseline', 'passed': 'True', 'max_pos_error': ''},
    ])
    write_csv(tmp_path / 'runs/noisy/metrics.csv', [
        {'imu_noise': 'True', 'gyro_bias': '-0.03', 'vehicle_sha256': 'configuration',
         'mean_pos_error': '0.4', 'max_pos_error': '2.0', 'outcome': 'invalid_lap'},
    ])
    rows = list(module.read_campaign('native', tmp_path))
    assert [row['id'] for row in rows] == ['noisy', 'early']
    assert rows[0]['imu_noise'] == 'True'
    assert rows[0]['gyro_bias'] == '-0.03'
    assert rows[0]['vehicle_sha256'] == 'configuration'
    assert rows[0]['failed_checks'] == 'ekf'
    assert rows[0]['max_pos_error'] == '1.87'
    assert rows[0]['mean_pos_error'] == '0.4'
    assert rows[0]['outcome'] == 'invalid_lap'
    assert rows[1]['max_pos_error'] == ''
    assert rows[1]['mean_pos_error'] == ''
    assert rows[1]['imu_noise'] == ''


def test_baseline_only_campaign_writes_empty_csv_and_explanatory_plot(tmp_path):
    module = comparison_module()
    write_csv(tmp_path / 'results.csv', [{'id': 'truth', 'kind': 'baseline'}])
    out = tmp_path / 'comparison'
    module.main([str(out), '--campaign', f'baseline={tmp_path}'])
    with (out / 'comparison.csv').open() as stream:
        assert list(csv.DictReader(stream)) == []
    assert (out / 'position-error.png').stat().st_size > 0


@pytest.mark.parametrize('outcome', ['lap', 'mission_finished'])
def test_completed_runs_with_failed_checks_are_hatched(tmp_path, monkeypatch, outcome):
    module = comparison_module()
    checks = {name: 'pass' for name in module.CHECKS}
    write_csv(tmp_path / 'results.csv', [
        {**checks, 'id': 'failed', 'kind': 'gate', 'passed': 'False', 'ekf': 'FAIL',
         'outcome': outcome, 'mean_pos_error': '1', 'max_pos_error': '2'},
        {**checks, 'id': 'passed', 'kind': 'gate', 'passed': 'True',
         'outcome': outcome, 'mean_pos_error': '0.01', 'max_pos_error': '0.05'},
    ])
    from matplotlib.figure import Figure
    hatches = []
    save = Figure.savefig

    def capture(figure, *args, **kwargs):
        hatches.extend([[bar.get_hatch() for bar in axis.patches] for axis in figure.axes])
        return save(figure, *args, **kwargs)

    monkeypatch.setattr(Figure, 'savefig', capture)
    module.main([str(tmp_path / 'comparison'), '--campaign', f'native={tmp_path}'])
    assert hatches == [['///', None], ['///', None]]
