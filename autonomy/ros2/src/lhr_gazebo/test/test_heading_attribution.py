"""Test diagnostic unknowns and known gyro offsets without simulator truth fitting."""

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
            'heading_audit', scripts / 'audit_gnss_heading.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def test_old_status_messages_without_update_instrumentation_are_unknown():
    module = analysis()
    assert module.jump_metrics([{'accepted': True, 'state_stamp_s': 10.}]) is None


def test_gated_fix_is_not_counted_as_an_applied_heading_jump():
    module = analysis()
    assert module.jump_metrics([{
        'accepted': False, 'state_stamp_s': 10., 'current_yaw_delta_rad': 1.}]) is None


def test_known_constant_gyro_offset_is_removed_without_position_measurements():
    module = analysis()
    parameters = {
        'sigma_accel': 1., 'sigma_yaw_accel': 1., 'gnss_max_delay_s': 1.,
        'gnss_origin_latitude_deg': 0., 'gnss_origin_longitude_deg': 0.,
        'gnss_origin_altitude_m': 0., 'yaw_rate_variance': .0004, 'gnss_gate_chi2': 25.}
    data = {'truth': [[0., 0., 0., 0., 0., 0., 0., 1.]], 'decisions': [], 'fixes': {},
            'imu': np.array([[round(t*1e9), 0., 0., .02, 0., 0., 9.8]
                             for t in np.arange(5., 8., .001)])}
    raw, _ = module.replay(data, parameters, 5., use_gnss=False)
    corrected, _ = module.replay(data, parameters, 5., gyro_bias=.02, use_gnss=False)
    assert raw[-1, 3] == pytest.approx(.06, abs=3e-5)
    assert corrected[-1, 3] == pytest.approx(0., abs=1e-12)
