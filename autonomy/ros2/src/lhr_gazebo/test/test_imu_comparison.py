"""Gate evidence must retain failures and unknown measurements in both modes."""

import csv
import importlib.util
from pathlib import Path


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
