"""Test selective angular attenuation with consistent state and covariance updates."""

from lhr_state_estimation.ekf import VehicleEkf
from lhr_state_estimation.gnss import PositionHistory
import numpy as np
import pytest


def coupled_filter():
    ekf = VehicleEkf()
    ekf.P[2, 0] = ekf.P[0, 2] = .1
    ekf.P[4, 1] = ekf.P[1, 4] = .1
    return ekf


@pytest.mark.parametrize('scale', [0., .1, .5, 1.])
def test_attenuation_keeps_instant_position_speed_gain_and_preserves_psd(scale):
    baseline, reduced = coupled_filter(), coupled_filter()
    initial = reduced.P.copy()
    assert baseline.update_position([1., 1.], np.eye(2))[0]
    assert reduced.update_position([1., 1.], np.eye(2), heading_gain=scale)[0]
    np.testing.assert_allclose(reduced.x[[0, 1, 3]], baseline.x[[0, 1, 3]])
    np.testing.assert_allclose(reduced.x[[2, 4]], baseline.x[[2, 4]]*scale, atol=1e-12)
    assert np.linalg.eigvalsh(reduced.P).min() > 0
    np.testing.assert_allclose(reduced.P, reduced.P.T, atol=1e-12)
    if scale == 0:
        np.testing.assert_allclose(reduced.P[np.ix_([2, 4], [2, 4])],
                                   initial[np.ix_([2, 4], [2, 4])])


@pytest.mark.parametrize('scale', [-.1, 1.1, float('nan'), float('inf')])
def test_invalid_gain_cannot_mutate_delayed_filter(scale):
    ekf = coupled_filter()
    history = PositionHistory(ekf)
    history.record(0, 0., 0., .0004)
    x, p = ekf.x.copy(), ekf.P.copy()
    with pytest.raises(ValueError, match='heading_gain'):
        history.correct(0, [1., 1.], np.eye(2), [0, 0], 25., scale)
    np.testing.assert_array_equal(ekf.x, x)
    np.testing.assert_array_equal(ekf.P, p)


def test_default_gain_preserves_existing_update_exactly():
    implicit, explicit = coupled_filter(), coupled_filter()
    implicit.update_position([1., 1.], np.eye(2))
    explicit.update_position([1., 1.], np.eye(2), heading_gain=1.)
    np.testing.assert_array_equal(implicit.x, explicit.x)
    np.testing.assert_array_equal(implicit.P, explicit.P)


def test_zero_gps_angular_gain_still_tracks_a_real_gyro_turn():
    baseline, restricted = VehicleEkf(), VehicleEkf()
    for k in range(200):
        for ekf in [baseline, restricted]:
            ekf.predict(.01, .1)
            ekf.update_yaw_rate(.08, .0004)
        if k % 2 == 0:
            restricted.update_position([.5, -.5], np.eye(2), heading_gain=0.)
    assert restricted.pose[2] > .15
    assert restricted.pose[2] == pytest.approx(baseline.pose[2], abs=1e-12)
