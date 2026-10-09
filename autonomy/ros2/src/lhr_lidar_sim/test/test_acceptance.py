"""Check acceptance comparisons include missed frames and obey gravity leveling."""

from lhr_lidar_sim.acceptance import availability, capture_counts
import numpy as np
import pytest


def test_availability_counts_empty_frames_and_sparse_stack_warmup():
    result = availability([1, 0, 1, 0, 1, 0, 0], 5, 3)
    assert result['points_per_frame'] == pytest.approx(3 / 7)
    assert result['frames_hit_pct'] == pytest.approx(300 / 7)
    assert result['stack_available_pct'] == pytest.approx(100 / 7)


def test_capture_roi_excludes_ground_and_reports_height_loss(tmp_path):
    capture = tmp_path / 'capture.npz'
    np.savez(capture, xyz=np.array([[5., 0., -.5], [5., 0., -.4],
                                   [5., 0., -.58], [9., 0., -.4]]),
             t=np.array([0., .1, .2, .3]), imu=np.array([[0., 0., 0., 0., 0., 9.81]]))
    row = {'sensor_height_m': '.58', 'bearing_deg': '0', 'range_center_m': '5'}
    counts, heights = capture_counts(capture, row)
    assert counts.sum() == 2
    assert heights == {'roi_points': 2, 'kept_above_5cm': 2, 'kept_above_15cm': 1}


def test_overcast_fit_holds_out_repeat_runs_and_caps_probability():
    from lhr_lidar_sim.acceptance import fit_overcast
    runs = []
    for name, measured in [('small_out_x_run1', 1.), ('small_out_x_run3', 100.)]:
        runs.append({'run': name, 'range_m': 10., 'capture_sha256': 'fixture',
                     'simulated': {'points_per_frame': 2.},
                     'raw_roi': {'points_per_frame': measured}})
    profile = fit_overcast(runs)
    assert profile['survival_probability'] == [.5]
    assert len(profile['training']) == len(profile['held_out']) == 1
    assert profile['held_out'][0]['expected_points_per_frame'] == 1.
