"""Verify stationary calibration against motion, missing evidence and known offsets."""

from lhr_state_estimation.gyro_calibration import GyroCalibration, scan_motion
import numpy as np
import pytest


def scene():
    rng = np.random.default_rng(2)
    return rng.uniform([-8., -5.], [10., 7.], (100, 2))


def config():
    return {'gyro_calibration_duration_s': .5, 'gyro_calibration_settle_s': 0.,
            'gyro_calibration_timeout_s': 2., 'gyro_calibration_min_samples': 40}


def feed(cal, moving=False, noisy=False, clouds=True):
    points = scene()
    for k in range(81):
        t = 1.+k*.01
        gyro = [.001, -.002, .008]
        if noisy:
            gyro[2] += .1*(-1)**k
        cal.add_imu(round(t*1e9), gyro, [0., 0., 9.80665])
        if clouds and k % 10 == 0:
            cal.add_cloud(round(t*1e9), points+[.2 if moving and k >= 10 else 0., 0.])
    return cal


def test_quiet_stationary_window_learns_offset_and_then_freezes_it():
    cal = feed(GyroCalibration(config()))
    assert cal.ready
    assert cal.bias_z == pytest.approx(.008)
    assert cal.result['samples'] >= 40
    cal.add_imu(2_000_000_000, [0, 0, .05], [0, 0, 9.8])
    assert cal.bias_z == pytest.approx(.008)


def test_translation_rejects_even_if_imu_is_quiet():
    cal = feed(GyroCalibration(config()), moving=True)
    assert cal.state == 'failed' and cal.reason == 'lidar_motion'


def test_small_constant_rotation_is_detected_by_lidar_not_mistaken_for_bias():
    cal = GyroCalibration(config())
    points = scene()
    cal.add_imu(1_000_000_000, [0, 0, .008], [0, 0, 9.8])
    cal.add_cloud(1_000_000_000, points)
    yaw = .01
    rotation = np.array([[np.cos(yaw), -np.sin(yaw)], [np.sin(yaw), np.cos(yaw)]])
    cal.add_cloud(1_100_000_000, points @ rotation.T)
    assert cal.state == 'failed' and cal.reason == 'lidar_motion'


def test_noisy_gyro_is_not_calibrated_even_if_scene_is_fixed():
    cal = feed(GyroCalibration(config()), noisy=True)
    assert cal.state == 'failed' and cal.reason == 'gyro_not_quiet'


def test_missing_lidar_times_out_and_late_data_cannot_reopen_failure():
    cal = feed(GyroCalibration(config()), clouds=False)
    assert not cal.ready
    cal.tick(3_100_000_000)
    assert cal.state == 'failed' and cal.reason == 'timeout'
    cal.add_cloud(3_200_000_000, scene())
    assert cal.state == 'failed'


def test_registration_rejects_unobservable_or_changed_scenes():
    line = np.column_stack([np.arange(100), np.zeros(100)])
    assert scan_motion(line, line) is None
    assert scan_motion(scene(), scene()+100.) is None


def test_known_rigid_motion_magnitude_is_recovered():
    points = scene()
    yaw, translation = .01, np.array([.02, -.01])
    rotation = np.array([[np.cos(yaw), -np.sin(yaw)], [np.sin(yaw), np.cos(yaw)]])
    distance, angle = scan_motion(points, points @ rotation.T+translation)
    assert distance == pytest.approx(np.linalg.norm(translation), abs=1e-10)
    assert angle == pytest.approx(yaw, abs=1e-10)


def test_lidar_dropout_restarts_evidence_window_without_weakening_motion_limit():
    cal = GyroCalibration(config())
    for k in range(111):
        stamp = round((1+k*.01)*1e9)
        cal.add_imu(stamp, [0, 0, .008], [0, 0, 9.8])
        if k % 10 == 0 and (k < 20 or k >= 50):
            cal.add_cloud(stamp, scene())
        if k == 70:
            assert not cal.ready
    assert cal.ready and cal.restarts == 1
    assert cal.result['window_start_s'] >= 1.5
    assert cal.result['window_end_s'] >= 2.
