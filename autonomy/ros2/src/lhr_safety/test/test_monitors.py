"""Each safety check trips on its fault and stays quiet on a healthy run."""

import math

from lhr_safety.monitors import Fault, SafetyConfig, SafetyMonitor
import pytest

WHEELBASE = 1.5494
CFG = SafetyConfig(wheelbase_m=WHEELBASE, max_speed_mps=15.0)


def _healthy(monitor, t, x=0.0, y=0.0, speed=5.0, steer=0.0):
    """Feed one consistent tick: fresh LiDAR, odom and a matching command."""
    monitor.on_lidar(t)
    monitor.on_command(t, steer)
    yaw_rate = speed * math.tan(steer) / WHEELBASE
    monitor.on_odom(t, x, y, speed, 0.0, yaw_rate, 0.01, 0.01)


def _drive(monitor, start, end, dt=0.05, **kwargs):
    t = start
    while t <= end + 1e-9:
        _healthy(monitor, t, **kwargs)
        t += dt
    return t - dt


def test_healthy_run_reports_no_fault():
    m = SafetyMonitor(CFG, now=0.0)
    last = _drive(m, 0.0, 10.0, steer=0.1)
    assert m.check(last) == Fault.NONE


def test_odom_that_never_arrives_is_stale():
    m = SafetyMonitor(CFG, now=0.0)
    m.on_lidar(0.2)
    assert m.check(0.2) == Fault.NONE
    m.on_lidar(0.3)
    assert m.check(0.3) == Fault.ODOM_STALE


def test_odom_that_stops_is_stale():
    m = SafetyMonitor(CFG, now=0.0)
    last = _drive(m, 0.0, 1.0)
    before = last + CFG.odom_timeout_s - 0.01
    m.on_lidar(before)
    assert m.check(before) == Fault.NONE
    after = last + CFG.odom_timeout_s + 0.01
    m.on_lidar(after)
    assert m.check(after) == Fault.ODOM_STALE


def test_lidar_that_stops_is_stale():
    m = SafetyMonitor(CFG, now=0.0)
    last = _drive(m, 0.0, 1.0)
    # Odom keeps coming, the cloud does not.
    t = last
    while t < last + 0.5:
        t += 0.05
        m.on_command(t, 0.0)
        m.on_odom(t, 0.0, 0.0, 5.0, 0.0, 0.0, 0.01, 0.01)
    assert m.check(t) == Fault.LIDAR_STALE


def test_lidar_check_can_be_off():
    cfg = SafetyConfig(wheelbase_m=WHEELBASE, max_speed_mps=15.0,
                       monitor_lidar=False)
    m = SafetyMonitor(cfg, now=0.0)
    m.on_command(1.0, 0.0)
    m.on_odom(1.0, 0.0, 0.0, 5.0, 0.0, 0.0, 0.01, 0.01)
    assert m.check(1.0) == Fault.NONE


@pytest.mark.parametrize('sample', [
    {'vx': 410.0},                             # the 10/18 EKF spike
    {'x': math.nan},                           # non-finite pose
    {'yaw_rate': math.inf},                    # non-finite twist
])
def test_ekf_divergence(sample):
    m = SafetyMonitor(CFG, now=0.0)
    _drive(m, 0.0, 1.0)
    odom = {'x': 0.0, 'y': 0.0, 'vx': 5.0, 'vy': 0.0, 'yaw_rate': 0.0,
            'var_x': 0.01, 'var_y': 0.01}
    odom.update(sample)
    m.on_lidar(1.05)
    m.on_odom(1.05, **odom)
    assert m.check(1.05) == Fault.EKF_DIVERGED


def test_uncertainty_bound_is_off_by_default():
    # Dead-reckoning variance only grows without GNSS; 10 m std is normal.
    m = SafetyMonitor(CFG, now=0.0)
    m.on_lidar(0.0)
    m.on_command(0.0, 0.0)
    m.on_odom(0.0, 0.0, 0.0, 5.0, 0.0, 0.0, 100.0, 100.0)
    assert m.check(0.0) == Fault.NONE


def test_uncertainty_bound_when_enabled():
    cfg = SafetyConfig(wheelbase_m=WHEELBASE, max_speed_mps=15.0,
                       max_position_std_m=1.0)
    m = SafetyMonitor(cfg, now=0.0)
    m.on_lidar(0.0)
    m.on_command(0.0, 0.0)
    m.on_odom(0.0, 0.0, 0.0, 5.0, 0.0, 0.0, 0.9 ** 2, 0.01)
    assert m.check(0.0) == Fault.NONE
    m.on_odom(0.05, 0.0, 0.0, 5.0, 0.0, 0.0, 1.1 ** 2, 0.01)
    assert m.check(0.05) == Fault.EKF_DIVERGED


def test_divergence_clears_when_the_estimate_recovers():
    m = SafetyMonitor(CFG, now=0.0)
    m.on_lidar(0.1)
    m.on_odom(0.1, 0.0, 0.0, 400.0, 0.0, 0.0, 0.01, 0.01)
    assert m.check(0.1) == Fault.EKF_DIVERGED
    _healthy(m, 0.15)
    assert m.check(0.15) == Fault.NONE


def test_geofence_anchors_where_the_car_arms():
    m = SafetyMonitor(CFG, now=0.0)
    # Booted far from the start box; no fence until the car arms.
    _healthy(m, 0.0, x=0.0, y=0.0)
    _healthy(m, 0.02, x=500.0, y=500.0)
    assert m.check(0.02) == Fault.NONE
    m.anchor_geofence()
    _healthy(m, 0.05, x=500.0 + CFG.geofence_radius_m - 1.0, y=500.0)
    assert m.check(0.05) == Fault.NONE
    _healthy(m, 0.1, x=500.0 + CFG.geofence_radius_m + 1.0, y=500.0)
    assert m.check(0.1) == Fault.GEOFENCE


def test_steering_that_does_not_track_trips_after_the_window():
    m = SafetyMonitor(CFG, now=0.0)
    # Commanding a hard turn while the car drives dead straight.
    t = 0.0
    while t < CFG.steer_window_s - 0.1:
        m.on_lidar(t)
        m.on_command(t, 0.3)
        m.on_odom(t, 5.0 * t, 0.0, 5.0, 0.0, 0.0, 0.01, 0.01)
        assert m.check(t) == Fault.NONE
        t += 0.05
    while t < CFG.steer_window_s + 0.1:
        m.on_lidar(t)
        m.on_command(t, 0.3)
        m.on_odom(t, 5.0 * t, 0.0, 5.0, 0.0, 0.0, 0.01, 0.01)
        t += 0.05
    assert m.check(t - 0.05) == Fault.STEER_TRACKING


def test_brief_steering_lag_does_not_trip():
    m = SafetyMonitor(CFG, now=0.0)
    m.on_lidar(0.0)
    m.on_command(0.0, 0.3)
    m.on_odom(0.0, 0.0, 0.0, 5.0, 0.0, 0.0, 0.01, 0.01)
    # The car catches up well inside the window.
    _drive(m, 0.05, 3.0, steer=0.3)
    assert m.check(3.0) == Fault.NONE


def test_steering_is_ignored_at_low_speed():
    m = SafetyMonitor(CFG, now=0.0)
    t = 0.0
    while t < 3.0:
        m.on_lidar(t)
        m.on_command(t, 0.5)
        m.on_odom(t, 0.0, 0.0, 1.0, 0.0, 0.0, 0.01, 0.01)
        t += 0.05
    assert m.check(t - 0.05) == Fault.NONE


def test_priority_reports_the_root_cause_first():
    m = SafetyMonitor(CFG, now=0.0)
    m.on_command(0.0, 0.0)
    m.on_odom(0.0, 1000.0, 0.0, 400.0, 0.0, 0.0, 0.01, 0.01)
    # Divergence outranks the command, LiDAR, fence and steering checks: a
    # bad pose explains everything downstream of it.
    assert m.check(0.0) == Fault.EKF_DIVERGED


def test_command_that_never_arrives_is_stale():
    m = SafetyMonitor(CFG, now=0.0)
    t = 0.0
    while t < 0.5:
        m.on_lidar(t)
        m.on_odom(t, 0.0, 0.0, 0.0, 0.0, 0.0, 0.01, 0.01)
        t += 0.05
    assert m.check(t - 0.05) == Fault.CMD_STALE


def test_command_that_stops_is_stale():
    # The controller dies; odom and the cloud keep coming.
    m = SafetyMonitor(CFG, now=0.0)
    last = _drive(m, 0.0, 1.0)
    t = last
    while t < last + CFG.cmd_timeout_s - 0.05:
        t += 0.05
        m.on_lidar(t)
        m.on_odom(t, 0.0, 0.0, 5.0, 0.0, 0.0, 0.01, 0.01)
    assert m.check(t) == Fault.NONE
    t += 0.1
    m.on_lidar(t)
    m.on_odom(t, 0.0, 0.0, 5.0, 0.0, 0.0, 0.01, 0.01)
    assert m.check(t) == Fault.CMD_STALE


@pytest.mark.parametrize('bad', [
    {'vx': 410.0}, {'x': math.nan}, {'yaw_rate': math.inf}])
def test_one_bad_odom_sample_between_checks_is_caught(bad):
    # Odom runs at 50 Hz and checks at 20 Hz: a good sample right after a
    # bad one must not hide it.
    m = SafetyMonitor(CFG, now=0.0)
    _drive(m, 0.0, 1.0)
    odom = {'x': 0.0, 'y': 0.0, 'vx': 5.0, 'vy': 0.0, 'yaw_rate': 0.0,
            'var_x': 0.01, 'var_y': 0.01}
    odom.update(bad)
    m.on_odom(1.02, **odom)
    _healthy(m, 1.04)
    assert m.check(1.05) == Fault.EKF_DIVERGED
    _healthy(m, 1.1)
    assert m.check(1.1) == Fault.NONE


def test_one_sample_outside_the_fence_is_caught():
    m = SafetyMonitor(CFG, now=0.0)
    _healthy(m, 0.0)
    m.anchor_geofence()
    _healthy(m, 0.02, x=CFG.geofence_radius_m + 1.0)
    _healthy(m, 0.04, x=CFG.geofence_radius_m - 1.0)
    assert m.check(0.05) == Fault.GEOFENCE


def test_reversing_does_not_trip_steering():
    m = SafetyMonitor(CFG, now=0.0)
    t = 0.0
    while t < 3.0:
        m.on_lidar(t)
        m.on_command(t, 0.2)
        # Backing up: negative forward speed flips the yaw rate's sign.
        yaw_rate = -3.0 * math.tan(0.2) / WHEELBASE
        m.on_odom(t, 0.0, 0.0, -3.0, 0.0, yaw_rate, 0.01, 0.01)
        t += 0.05
    assert m.check(t - 0.05) == Fault.NONE


def test_clock_jumping_backwards_counts_as_stale():
    # A Gazebo world reset rewinds sim time below every stored timestamp.
    m = SafetyMonitor(CFG, now=0.0)
    last = _drive(m, 0.0, 10.0)
    assert m.check(last) == Fault.NONE
    assert m.check(1.0) == Fault.ODOM_STALE


def test_non_finite_command_is_a_fault():
    m = SafetyMonitor(CFG, now=0.0)
    _drive(m, 0.0, 1.0)
    m.on_command(1.02, math.nan)
    m.on_command(1.04, 0.0)
    assert m.check(1.05) == Fault.CMD_INVALID
    _healthy(m, 1.1)
    assert m.check(1.1) == Fault.NONE


def test_fault_codes_are_stable():
    # These go on the CAN heartbeat; renumbering breaks the receiver.
    assert [f.value for f in Fault] == [0, 1, 2, 3, 4, 5, 6, 7]
    assert [f.name for f in Fault] == [
        'NONE', 'ODOM_STALE', 'EKF_DIVERGED', 'LIDAR_STALE', 'GEOFENCE',
        'STEER_TRACKING', 'CMD_STALE', 'CMD_INVALID']
