"""Each safety check trips on its fault and stays quiet on a healthy run."""

import math

from lhr_safety.monitors import Fault, SafetyConfig, SafetyMonitor
import pytest

WHEELBASE = 1.5494
CFG = SafetyConfig(wheelbase_m=WHEELBASE, max_speed_mps=15.0)
# The yaw-rate rule only runs for a plant with no steering measurement.
YAW_CFG = SafetyConfig(wheelbase_m=WHEELBASE, max_speed_mps=15.0, require_steer_measurement=False)


def _sensors(monitor, t, yaw_rate=0.0):
    """Feed the EKF's inputs: one IMU sample (the gyro's yaw rate) and one wheel-speed sample."""
    monitor.on_imu(t, yaw_rate)
    monitor.on_wheels(t)


def _healthy(monitor, t, x=0.0, y=0.0, speed=5.0, steer=0.0):
    """Feed one consistent tick: every stream fresh and a matching command."""
    monitor.on_lidar(t)
    monitor.on_command(t, steer)
    monitor.on_steering(t, steer)
    yaw_rate = speed * math.tan(steer) / WHEELBASE
    monitor.on_odom(t, x, y, speed, 0.0, yaw_rate, 0.01, 0.01)
    _sensors(monitor, t, yaw_rate)


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
        _sensors(m, t)
    assert m.check(t) == Fault.LIDAR_STALE


def test_lidar_check_can_be_off():
    cfg = SafetyConfig(wheelbase_m=WHEELBASE, max_speed_mps=15.0,
                       monitor_lidar=False)
    m = SafetyMonitor(cfg, now=0.0)
    m.on_command(1.0, 0.0)
    m.on_steering(1.0, 0.0)
    m.on_odom(1.0, 0.0, 0.0, 5.0, 0.0, 0.0, 0.01, 0.01)
    _sensors(m, 1.0)
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
    m = SafetyMonitor(YAW_CFG, now=0.0)
    # Commanding a hard turn while the car drives dead straight.
    t = 0.0
    while t < CFG.steer_window_s - 0.1:
        m.on_lidar(t)
        m.on_command(t, 0.3)
        m.on_odom(t, 5.0 * t, 0.0, 5.0, 0.0, 0.0, 0.01, 0.01)
        _sensors(m, t)
        assert m.check(t) == Fault.NONE
        t += 0.05
    while t < CFG.steer_window_s + 0.1:
        m.on_lidar(t)
        m.on_command(t, 0.3)
        m.on_odom(t, 5.0 * t, 0.0, 5.0, 0.0, 0.0, 0.01, 0.01)
        _sensors(m, t)
        t += 0.05
    assert m.check(t - 0.05) == Fault.STEER_TRACKING


def test_brief_steering_lag_does_not_trip():
    m = SafetyMonitor(YAW_CFG, now=0.0)
    m.on_lidar(0.0)
    m.on_command(0.0, 0.3)
    m.on_odom(0.0, 0.0, 0.0, 5.0, 0.0, 0.0, 0.01, 0.01)
    # The car catches up well inside the window.
    _drive(m, 0.05, 3.0, steer=0.3)
    assert m.check(3.0) == Fault.NONE


def test_steering_is_ignored_at_low_speed():
    m = SafetyMonitor(YAW_CFG, now=0.0)
    t = 0.0
    while t < 3.0:
        m.on_lidar(t)
        m.on_command(t, 0.5)
        m.on_odom(t, 0.0, 0.0, 1.0, 0.0, 0.0, 0.01, 0.01)
        _sensors(m, t)
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
        _sensors(m, t)
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
        _sensors(m, t)
    assert m.check(t) == Fault.NONE
    t += 0.1
    m.on_lidar(t)
    m.on_odom(t, 0.0, 0.0, 5.0, 0.0, 0.0, 0.01, 0.01)
    _sensors(m, t)
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
    m = SafetyMonitor(YAW_CFG, now=0.0)
    t = 0.0
    while t < 3.0:
        m.on_lidar(t)
        m.on_command(t, 0.2)
        # Backing up: negative forward speed flips the yaw rate's sign.
        yaw_rate = -3.0 * math.tan(0.2) / WHEELBASE
        m.on_odom(t, 0.0, 0.0, -3.0, 0.0, yaw_rate, 0.01, 0.01)
        _sensors(m, t, yaw_rate)
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
    assert [f.value for f in Fault] == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert [f.name for f in Fault] == [
        'NONE', 'ODOM_STALE', 'EKF_DIVERGED', 'LIDAR_STALE', 'GEOFENCE',
        'STEER_TRACKING', 'CMD_STALE', 'CMD_INVALID', 'IMU_STALE', 'WHEEL_STALE',
        'MISSION_STALE']


# --- Measured steering angle (#208) ---

RATE_CFG = SafetyConfig(wheelbase_m=WHEELBASE, max_speed_mps=15.0, max_steer_rate_rad_s=1.0)


def _steer_run(monitor, commands, measured, end, dt=0.02):
    """Drive straight with fresh streams; commands(t) and measured(t) give the angles."""
    t, first_trip = 0.0, None
    while t <= end + 1e-9:
        monitor.on_lidar(t)
        monitor.on_command(t, commands(t))
        # A car that does not turn: the yaw-rate rule would trip, the angle rule must decide.
        monitor.on_odom(t, 5.0 * t, 0.0, 5.0, 0.0, 0.0, 0.01, 0.01)
        _sensors(monitor, t)
        monitor.on_steering(t, measured(t))
        if first_trip is None and monitor.check(t) == Fault.STEER_TRACKING:
            first_trip = t
        t += dt
    return first_trip


def test_measured_steering_that_follows_never_trips():
    # A sweep the actuator follows exactly: the yaw rate disagrees, but the
    # measured angle owns the check once it arrives.
    m = SafetyMonitor(CFG, now=0.0)
    assert _steer_run(m, lambda t: 0.3 * math.sin(t), lambda t: 0.3 * math.sin(t), 10.0) is None


def test_rate_limited_step_is_not_a_fault():
    # A 0.5 rad step that the wheels follow at their 1 rad/s limit.
    m = SafetyMonitor(RATE_CFG, now=0.0)
    trip = _steer_run(m, lambda t: 0.5 if t >= 1.0 else 0.0,
                      lambda t: min(0.5, max(0.0, t - 1.0)), 4.0)
    assert trip is None


def test_command_past_the_lock_is_not_a_fault():
    # The adapter clamps 0.7 rad to the 0.55 rad lock; wheels held there are right.
    lock_cfg = SafetyConfig(wheelbase_m=WHEELBASE, max_speed_mps=15.0, max_steer_rad=0.55)
    m = SafetyMonitor(lock_cfg, now=0.0)
    assert _steer_run(m, lambda t: 0.7, lambda t: 0.55, 3.0) is None


def test_actuator_slower_than_its_limit_trips():
    m = SafetyMonitor(RATE_CFG, now=0.0)
    trip = _steer_run(m, lambda t: 0.5 if t >= 1.0 else 0.0,
                      lambda t: min(0.5, max(0.0, 0.5 * (t - 1.0))), 4.0)
    assert trip is not None


def test_frozen_steering_trips_one_window_after_it_shows():
    # Frozen at 0.1 rad while the command ramps away at 0.1 rad/s: the gap
    # passes the tolerance at 0.35 s, then the window runs.
    m = SafetyMonitor(CFG, now=0.0)
    trip = _steer_run(m, lambda t: 0.1 + 0.1 * t, lambda t: 0.1, 3.0)
    visible = CFG.steer_angle_tol_rad / 0.1
    assert trip == pytest.approx(visible + CFG.steer_angle_window_s, abs=0.05)


def test_losing_the_measured_angle_trips():
    m = SafetyMonitor(CFG, now=0.0)
    assert _steer_run(m, lambda t: 0.0, lambda t: 0.0, 1.0) is None
    t = 1.0
    while t < 1.0 + CFG.steer_angle_timeout_s + 0.1:
        t += 0.02
        m.on_lidar(t)
        m.on_command(t, 0.0)
        m.on_odom(t, 5.0 * t, 0.0, 5.0, 0.0, 0.0, 0.01, 0.01)
        _sensors(m, t)
    assert m.check(t) == Fault.STEER_TRACKING


def test_non_finite_measured_angle_trips():
    m = SafetyMonitor(CFG, now=0.0)
    trip = _steer_run(m, lambda t: 0.0, lambda t: math.nan if t >= 1.0 else 0.0, 2.0)
    assert trip == pytest.approx(1.0 + CFG.steer_angle_window_s, abs=0.05)


def test_a_measured_angle_that_never_arrives_trips_by_default():
    # A wrong topic or a dead encoder at boot must not fall back to the yaw rate.
    m = SafetyMonitor(CFG, now=0.0)
    t = 0.0
    while t < CFG.steer_angle_timeout_s + 0.1:
        m.on_lidar(t)
        m.on_command(t, 0.0)
        m.on_odom(t, 0.0, 0.0, 5.0, 0.0, 0.0, 0.01, 0.01)
        _sensors(m, t)
        t += 0.02
    assert m.check(t) == Fault.STEER_TRACKING


@pytest.mark.parametrize('turning, expected', [(True, Fault.NONE), (False, Fault.STEER_TRACKING)])
def test_without_a_requirement_the_yaw_rate_rule_takes_over(turning, expected):
    # No measured angle at all: a missing measurement is not a fault here,
    # and the yaw rate alone decides whether a 0.3 rad command is followed.
    m = SafetyMonitor(YAW_CFG, now=0.0)
    yaw_rate = 5.0 * math.tan(0.3) / WHEELBASE if turning else 0.0
    t = 0.0
    while t < 3.0:
        m.on_lidar(t)
        m.on_command(t, 0.3)
        m.on_odom(t, 0.0, 0.0, 5.0, 0.0, yaw_rate, 0.01, 0.01)
        _sensors(m, t, yaw_rate)
        t += 0.05
    assert m.check(t - 0.05) == expected


# --- The EKF's inputs (#126) ---

def _without(monitor, start, end, imu=True, wheels=True, dt=0.02):
    """Drive with every stream fresh except the ones switched off; return the last time."""
    t = start
    while t <= end + 1e-9:
        monitor.on_lidar(t)
        monitor.on_command(t, 0.0)
        monitor.on_steering(t, 0.0)
        # The EKF predicts on its own timer: odometry stays fresh and finite.
        monitor.on_odom(t, 5.0 * t, 0.0, 5.0, 0.0, 0.0, 0.01, 0.01)
        if imu:
            monitor.on_imu(t, 0.0)
        if wheels:
            monitor.on_wheels(t)
        t += dt
    return t - dt


@pytest.mark.parametrize('lost, fault, timeout', [
    ('imu', Fault.IMU_STALE, CFG.imu_timeout_s),
    ('wheels', Fault.WHEEL_STALE, CFG.wheel_timeout_s),
])
def test_sensor_that_stops_is_stale_although_odom_stays_fresh(lost, fault, timeout):
    m = SafetyMonitor(CFG, now=0.0)
    last = _drive(m, 0.0, 1.0)
    keep = {'imu': lost != 'imu', 'wheels': lost != 'wheels'}
    t = _without(m, last + 0.02, last + timeout - 0.02, **keep)
    assert m.check(t) == Fault.NONE
    t = _without(m, t + 0.02, last + timeout + 0.04, **keep)
    assert m.check(t) == fault


@pytest.mark.parametrize('lost, fault', [('imu', Fault.IMU_STALE), ('wheels', Fault.WHEEL_STALE)])
def test_sensor_that_never_arrives_is_stale(lost, fault):
    m = SafetyMonitor(CFG, now=0.0)
    t = _without(m, 0.0, 0.5, imu=lost != 'imu', wheels=lost != 'wheels')
    assert m.check(t) == fault


def test_sensor_checks_can_be_off():
    # A stack that does not read the IMU or wheel speeds (estimator:=truth).
    cfg = SafetyConfig(wheelbase_m=WHEELBASE, max_speed_mps=15.0,
                       monitor_imu=False, monitor_wheels=False)
    m = SafetyMonitor(cfg, now=0.0)
    t = _without(m, 0.0, 2.0, imu=False, wheels=False)
    assert m.check(t) == Fault.NONE


def test_dead_sensor_ranks_below_the_pose_and_above_everything_else():
    m = SafetyMonitor(CFG, now=0.0)
    last = _drive(m, 0.0, 1.0)
    # The IMU, the wheels, the command and the cloud all stop; odom keeps coming.
    t = last
    while t < last + 0.5:
        t += 0.02
        m.on_steering(t, 0.0)
        m.on_odom(t, 5.0 * t, 0.0, 5.0, 0.0, 0.0, 0.01, 0.01)
    assert m.check(t) == Fault.IMU_STALE
    m.on_imu(t, 0.0)
    assert m.check(t) == Fault.WHEEL_STALE
    m.on_odom(t, 0.0, 0.0, 400.0, 0.0, 0.0, 0.01, 0.01)
    assert m.check(t) == Fault.EKF_DIVERGED


@pytest.mark.parametrize('gyro_turning, expected',
                         [(True, Fault.NONE), (False, Fault.STEER_TRACKING)])
def test_yaw_rate_rule_reads_the_gyro_not_the_estimate(gyro_turning, expected):
    # The estimate says the car turns as commanded; only the gyro knows
    # whether it does, so a frozen or wrong estimate cannot hide a stuck
    # actuator.
    m = SafetyMonitor(YAW_CFG, now=0.0)
    turning = 5.0 * math.tan(0.3) / WHEELBASE
    t = 0.0
    while t < 3.0:
        m.on_lidar(t)
        m.on_command(t, 0.3)
        m.on_odom(t, 0.0, 0.0, 5.0, 0.0, turning, 0.01, 0.01)
        _sensors(m, t, turning if gyro_turning else 0.0)
        t += 0.05
    assert m.check(t - 0.05) == expected


@pytest.mark.parametrize('turning, expected', [(True, Fault.NONE), (False, Fault.STEER_TRACKING)])
def test_without_an_imu_the_yaw_rate_rule_reads_the_odometry(turning, expected):
    cfg = SafetyConfig(wheelbase_m=WHEELBASE, max_speed_mps=15.0,
                       require_steer_measurement=False, monitor_imu=False)
    m = SafetyMonitor(cfg, now=0.0)
    yaw_rate = 5.0 * math.tan(0.3) / WHEELBASE if turning else 0.0
    t = 0.0
    while t < 3.0:
        m.on_lidar(t)
        m.on_command(t, 0.3)
        m.on_odom(t, 0.0, 0.0, 5.0, 0.0, yaw_rate, 0.01, 0.01)
        m.on_wheels(t)
        t += 0.05
    assert m.check(t - 0.05) == expected


def test_non_finite_gyro_is_not_read_as_a_steering_fault():
    # A NaN gyro reaches the EKF too, which then trips EKF_DIVERGED; the
    # yaw-rate rule must not blame the steering for it.
    m = SafetyMonitor(YAW_CFG, now=0.0)
    turning = 5.0 * math.tan(0.3) / WHEELBASE
    t = 0.0
    while t < 3.0:
        m.on_lidar(t)
        m.on_command(t, 0.3)
        m.on_odom(t, 0.0, 0.0, 5.0, 0.0, turning, 0.01, 0.01)
        m.on_imu(t, math.nan)
        m.on_wheels(t)
        t += 0.05
    assert m.check(t - 0.05) == Fault.NONE


# --- A dead mission_manager (#127) ---

def _mission(monitor, start, end, state_armed, status=True, dt=0.05):
    """Drive healthy; mission status at 10 Hz while status is on."""
    t, next_status = start, start
    while t <= end + 1e-9:
        _healthy(monitor, t)
        if status and t >= next_status - 1e-9:
            monitor.on_mission(t, state_armed)
            next_status += 0.1
        t += dt
    return t - dt


def test_mission_status_that_stops_while_armed_is_stale():
    m = SafetyMonitor(CFG, now=0.0)
    last = _mission(m, 0.0, 2.0, state_armed=True)
    m.on_mission(last, True)   # the last status DRIVING sent before dying
    t = _mission(m, last + 0.05, last + CFG.mission_timeout_s - 0.05, True, status=False)
    assert m.check(t) == Fault.NONE
    t = _mission(m, t + 0.05, last + CFG.mission_timeout_s + 0.1, True, status=False)
    assert m.check(t) == Fault.MISSION_STALE


def test_mission_status_that_stops_while_not_armed_is_not_a_fault():
    # OFF, FINISHED and EMERGENCY: the car is not driving, or already stopped.
    m = SafetyMonitor(CFG, now=0.0)
    last = _mission(m, 0.0, 2.0, state_armed=False)
    t = _mission(m, last + 0.05, last + 3.0, False, status=False)
    assert m.check(t) == Fault.NONE


def test_mission_status_that_never_arrives_is_not_a_fault():
    # The manager is still coming up; nothing has armed.
    m = SafetyMonitor(CFG, now=0.0)
    t = _mission(m, 0.0, 3.0, state_armed=True, status=False)
    assert m.check(t) == Fault.NONE


def test_a_status_that_leaves_the_armed_states_clears_the_check():
    m = SafetyMonitor(CFG, now=0.0)
    last = _mission(m, 0.0, 1.0, state_armed=True)
    m.on_mission(last + 0.1, False)   # FINISHED, then the manager exits
    t = _mission(m, last + 0.15, last + 2.0, False, status=False)
    assert m.check(t) == Fault.NONE


def test_dead_mission_manager_ranks_above_the_command():
    m = SafetyMonitor(CFG, now=0.0)
    last = _mission(m, 0.0, 1.0, state_armed=True)
    # pure_pursuit dies too: the command and the status both stop.
    t = last
    while t < last + 1.0:
        t += 0.02
        m.on_lidar(t)
        m.on_steering(t, 0.0)
        m.on_odom(t, 5.0 * t, 0.0, 5.0, 0.0, 0.0, 0.01, 0.01)
        _sensors(m, t)
    assert m.check(t) == Fault.MISSION_STALE


def _yaw_tick(m, t, gyro):
    """Command 0.3 rad on the yaw-rate rule; the gyro says how much the car turns."""
    m.on_lidar(t)
    m.on_command(t, 0.3)
    m.on_odom(t, 0.0, 0.0, 5.0, 0.0, 0.0, 0.01, 0.01)
    m.on_imu(t, gyro)
    m.on_wheels(t)


def test_a_gap_in_the_gyro_restarts_the_mismatch_window():
    # 0.85 s of mismatch, a 4 s gap of NaN gyro samples, then one more
    # mismatching sample: the gap does not count as mismatch.
    m = SafetyMonitor(YAW_CFG, now=0.0)
    t = 0.0
    while t < 0.9:
        _yaw_tick(m, t, 0.0)
        t += 0.05
    while t < 4.9:
        _yaw_tick(m, t, math.nan)
        t += 0.05
    _yaw_tick(m, t, 0.0)
    assert m.check(t) == Fault.NONE
    end = t + YAW_CFG.steer_window_s + 0.1
    while t < end:
        t += 0.05
        _yaw_tick(m, t, 0.0)
    assert m.check(t) == Fault.STEER_TRACKING
