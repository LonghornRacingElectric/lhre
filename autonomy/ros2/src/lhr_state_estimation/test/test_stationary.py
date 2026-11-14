"""Do not convert quiet IMUs or absent motion evidence into a speed clamp."""

import math

from lhr_state_estimation.ekf import VehicleEkf
from lhr_state_estimation.gnss import PositionHistory
from lhr_state_estimation.stationary import StationaryEvidence
import numpy as np
import pytest


def scene():
    return np.random.default_rng(13).uniform([-5, -5], [5, 5], (150, 2))


def feed(detector, speed=0., rate=0., start=1., end=5.):
    points = scene()
    for k in range(round((end-start)*100)+1):
        t = start+k*.01
        detector.add_imu(round(t*1e9), [0, 0, rate], [0, 0, 9.81])
        if k % 10 == 0:
            angle = rate*(t-1)
            rotation = np.array([[math.cos(angle), -math.sin(angle)],
                                 [math.sin(angle), math.cos(angle)]])
            detector.add_cloud(round(t*1e9), (points-[speed*(t-1), 0]) @ rotation)
    return detector


def test_still_scene_confirms_then_retains_anchor():
    detector = feed(StationaryEvidence({}))
    assert detector.ready
    anchor = detector.anchor.copy()
    feed(detector, start=5.01, end=6.01)
    assert detector.ready
    np.testing.assert_array_equal(anchor, detector.anchor)


@pytest.mark.parametrize('speed,rate', [(.02, 0), (.2, 0), (0, .003), (0, .1)])
def test_slow_translation_coasting_and_rotation_are_not_stationary(speed, rate):
    assert not feed(StationaryEvidence({}), speed, rate).ready


def test_missing_clouds_impact_and_imu_gap_revoke_evidence():
    detector = feed(StationaryEvidence({}))
    detector.add_imu(5_010_000_000, [0, 0, 0], [2, 0, 9.81])
    assert not detector.ready
    detector = feed(StationaryEvidence({}))
    assert not detector.fresh(5_300_000_000)
    detector = feed(StationaryEvidence({}))
    detector.add_imu(5_400_000_000, [0, 0, 0], [0, 0, 9.81])
    assert not detector.ready


@pytest.mark.parametrize('points', [np.zeros((10, 2)), np.zeros((100, 2)),
                                    np.column_stack((np.arange(100), np.zeros(100)))])
def test_sparse_or_degenerate_scene_revokes_evidence(points):
    detector = feed(StationaryEvidence({}))
    assert not detector.add_cloud(5_100_000_000, points)
    assert not detector.ready


def test_motion_requires_full_new_window_before_reentry():
    detector = feed(StationaryEvidence({}))
    assert not detector.add_cloud(5_100_000_000, scene()-[.2, 0])
    feed(detector, start=5.2, end=7.2)
    assert not detector.ready
    feed(detector, start=7.21, end=9.21)
    assert detector.ready


@pytest.mark.parametrize('value', [0, -1, float('nan'), float('inf')])
def test_invalid_stationary_configuration(value):
    with pytest.raises(ValueError):
        StationaryEvidence({'stationary_speed_variance': value})


def test_zero_speed_preserves_heading_and_covariance_is_positive():
    ekf = VehicleEkf()
    ekf.x[3] = 1.
    ekf.P[2, 3] = ekf.P[3, 2] = .05
    ekf.update_stationary_speed(.0025)
    assert 0 < ekf.x[3] < .03
    assert ekf.x[2] == 0
    assert np.linalg.eigvalsh(ekf.P).min() > 0


def replay(delay):
    ekf = VehicleEkf()
    history = PositionHistory(ekf)
    history.record(0, .1, 0., .0004)
    for k in range(1, 101):
        ekf.predict(.01, .1)
        ekf.update_yaw_rate(0., .0004)
        history.record(k*10_000_000, .1, 0., .0004)
        if k == 40+delay:
            assert history.correct(400_000_000, [.1, .02], np.eye(2)*.64,
                                   [0, 0], 25, 0)[0]
        if k == 50:
            assert history.correct_stationary_speed(500_000_000, .0025)
        if k == 80:
            assert history.correct(750_000_000, [.01, .01], np.eye(2)*.64,
                                   [0, 0], 25, 0)[0]
    return ekf


def test_delayed_gps_preserves_intervening_stationary_correction():
    ontime, delayed = replay(0), replay(20)
    np.testing.assert_allclose(delayed.x, ontime.x, atol=1e-12)
    np.testing.assert_allclose(delayed.P, ontime.P, atol=1e-12)


def test_old_duplicate_future_scan_corrections_do_not_mutate_filter():
    ekf = VehicleEkf()
    history = PositionHistory(ekf)
    history.record(100_000_000, 0., 0., .0004)
    ekf.predict(.1)
    history.record(200_000_000, 0., 0., .0004)
    assert history.correct(150_000_000, [0, 0], np.eye(2), [0, 0], 25)[0]
    assert history.correct_stationary_speed(180_000_000, .0025)
    before_x, before_p = ekf.x.copy(), ekf.P.copy()
    for stamp in [0, 140_000_000, 180_000_000, 300_000_000]:
        assert not history.correct_stationary_speed(stamp, .0025)
        np.testing.assert_array_equal(ekf.x, before_x)
        np.testing.assert_array_equal(ekf.P, before_p)


def test_identical_noisy_fixes_and_biased_imu_improve_with_verified_zero_speed():
    rng = np.random.default_rng(8)
    filters = [VehicleEkf(), VehicleEkf()]
    rows = [[], []]
    for k in range(1, 1001):
        position = rng.normal(0, .8, 2)
        for i, ekf in enumerate(filters):
            ekf.predict(.01, -.03)
            ekf.update_yaw_rate(0., .0004)
            if k % 2 == 0:
                ekf.update_position(position, np.eye(2)*.64, heading_gain=0.)
            if i == 1 and k % 10 == 0:
                ekf.update_stationary_speed(.0025)
            if k > 500:
                rows[i].append(ekf.x.copy())
    baseline, corrected = map(np.asarray, rows)
    assert np.std(corrected[:, 0]) < .4*np.std(baseline[:, 0])
    assert np.max(abs(corrected[:, 3])) < .1*np.max(abs(baseline[:, 3]))
