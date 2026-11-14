"""Keep heading-recovery controls tied to known motion and sustained accuracy."""

import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest


def analysis():
    scripts = Path(__file__).parents[1] / 'scripts'
    sys.path.insert(0, str(scripts))
    try:
        spec = importlib.util.spec_from_file_location(
            'heading_policies', scripts / 'compare_heading_policies.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def test_synthetic_straight_control_has_declared_acceleration_and_no_rotation():
    module = analysis()
    data, _, truth, meta = module.synthetic('straight', 3, end=12.)
    assert truth[-1, 1] == pytest.approx(10.5, abs=.02)
    np.testing.assert_allclose(truth[:, 2], 0.)
    np.testing.assert_allclose(truth[:, 6], 0.)
    imu = data['imu']
    quiet = imu[(imu[:, 0] >= 2e9) & (imu[:, 0] < 5e9)]
    assert quiet[:, 3].mean() == pytest.approx(meta['gyro_bias_rad_s'], abs=.0002)


def test_synthetic_slow_turn_rotates_after_the_declared_motion_onset():
    module = analysis()
    _, _, truth, _ = module.synthetic('slow_turn', 7, end=15.)
    yaw = 2*np.arctan2(truth[:, 6], truth[:, 7])
    assert max(abs(yaw[truth[:, 0] <= 11])) == 0.
    assert yaw[-1] == pytest.approx(.32, abs=1e-10)


def test_brief_heading_error_zero_crossing_is_not_sustained_recovery():
    module = analysis()
    rows = [{'stamp_s': t, 'signed_heading_error_deg': 0. if t < 9 else 5.}
            for t in np.arange(8., 12., .2)]
    assert module.recovery(rows) is None
    for row in rows:
        row['signed_heading_error_deg'] = 1.
    assert module.recovery(rows) == pytest.approx(1.)
