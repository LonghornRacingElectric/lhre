"""Periodic uncertain fixes must correct position at measurement time, not delivery time."""

import math

from lhr_state_estimation.ekf import VehicleEkf
from lhr_state_estimation.gnss import LocalENU, PositionHistory
import numpy as np
import pytest


@pytest.mark.parametrize('origin', [(0, 0, 0), (30, -97, 150), (45, 179.999, 40)])
def test_geodetic_enu_round_trip(origin):
    converter = LocalENU(*origin)
    point = np.array([123.4, -87.6, 2.1])
    np.testing.assert_allclose(converter.to_enu(*converter.to_geodetic(*point)), point, atol=1e-7)
    np.testing.assert_allclose(converter.to_enu(*origin), 0, atol=1e-8)


def test_position_measurement_is_uncertain_and_corrects_correlated_speed():
    ekf = VehicleEkf()
    ekf.predict(1.)
    accepted, _ = ekf.update_position([1., 0.], np.eye(2)*.04)
    assert accepted and 0 < ekf.x[0] < 1
    assert ekf.twist[0] > 0
    assert ekf.P[0, 0] > 0


def test_antenna_lever_arm_is_not_treated_as_vehicle_position():
    ekf = VehicleEkf()
    ekf.reset(2, 3, math.pi/2)
    before = ekf.x.copy()
    accepted, nis = ekf.update_position([2, 4], np.eye(2)*.01, [1., 0.])
    assert accepted and nis < 1e-20
    np.testing.assert_allclose(ekf.x, before)


@pytest.mark.parametrize('covariance', [
    np.eye(2)*-1, np.zeros((2, 2)), np.array([[1, 2], [0, 1]]), np.eye(2)*np.nan])
def test_invalid_fix_covariance_is_rejected(covariance):
    with pytest.raises(ValueError):
        VehicleEkf().update_position([1, 2], covariance)


def run(delivery):
    ekf = VehicleEkf()
    history = PositionHistory(ekf)
    history.record(0, .2, .1, .0004)
    for k in range(1, 101):
        ekf.predict(.01, .2)
        ekf.update_yaw_rate(.1, .0004)
        history.record(k*10_000_000, .2, .1, .0004)
        if k in delivery:
            stamp = delivery[k]
            accepted, _, _ = history.correct(stamp, [.1, .02], np.eye(2)*.01, [0, 0], 25)
            assert accepted
    return ekf


def test_delayed_fix_replay_matches_same_measurement_delivered_on_time():
    ontime = run({21: 205_000_000, 41: 405_000_000})
    delayed = run({35: 205_000_000, 55: 405_000_000})
    np.testing.assert_allclose(delayed.x, ontime.x, atol=1e-12)
    np.testing.assert_allclose(delayed.P, ontime.P, atol=1e-12)


def test_rejected_delayed_outlier_restores_current_state():
    ekf = VehicleEkf()
    history = PositionHistory(ekf)
    history.record(0, 0., 0., .01)
    ekf.predict(.1)
    history.record(100_000_000, 0., 0., .01)
    before_x, before_p = ekf.x.copy(), ekf.P.copy()
    accepted, reason, _ = history.correct(50_000_000, [1000, 1000], np.eye(2), [0, 0], 25)
    assert not accepted and reason == 'innovation_gate'
    np.testing.assert_array_equal(ekf.x, before_x)
    np.testing.assert_array_equal(ekf.P, before_p)


def test_history_rejects_old_duplicate_and_future_fixes():
    ekf = VehicleEkf()
    history = PositionHistory(ekf, .1)
    for k in range(20):
        ekf.predict(.01)
        history.record(k*10_000_000, 0., 0., .01)
    assert not history.correct(0, [0, 0], np.eye(2), [0, 0], 25)[0]
    assert history.correct(150_000_000, [0, 0], np.eye(2), [0, 0], 25)[0]
    assert not history.correct(150_000_000, [0, 0], np.eye(2), [0, 0], 25)[0]
    assert history.correct(500_000_000, [0, 0], np.eye(2), [0, 0], 25)[1] == 'future'


def test_position_only_fix_changes_yaw_when_cross_covariance_exists_and_is_reported():
    ekf = VehicleEkf()
    ekf.P[2, 0] = ekf.P[0, 2] = .1
    history = PositionHistory(ekf)
    history.record(0, 0., 0., .0004)
    assert history.correct(0, [1., 0.], np.eye(2), [0, 0], 25)[0]
    assert ekf.pose[2] == pytest.approx(.05)
    assert history.last_correction['fix_yaw_delta_rad'] == pytest.approx(.05)
    assert history.last_correction['current_yaw_delta_rad'] == pytest.approx(.05)
    assert history.last_correction['fix_yaw_position_cross_covariance'] == [.1, 0.]


def test_duplicate_correction_does_not_reuse_a_previous_heading_jump_diagnostic():
    ekf = VehicleEkf()
    history = PositionHistory(ekf)
    history.record(0, 0., 0., .0004)
    assert history.correct(0, [0., 0.], np.eye(2), [0, 0], 25)[0]
    assert history.last_correction
    before = ekf.x.copy()
    assert not history.correct(0, [1., 0.], np.eye(2), [0, 0], 25)[0]
    assert history.last_correction == {}
    np.testing.assert_array_equal(ekf.x, before)
