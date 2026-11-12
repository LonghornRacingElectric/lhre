"""Tests for timestamp-aligned planar pose interpolation."""

import math

from lhr_perception.pose_history import Pose2D, PoseHistory, sensor_point_to_map


def test_interpolates_position_and_heading():
    """Interpolate translation and rotation at a sensor timestamp."""
    history = PoseHistory()
    history.add(Pose2D(1_000_000_000, 1.0, 2.0, 0.2))
    history.add(Pose2D(1_100_000_000, 3.0, 4.0, 0.6))

    pose = history.lookup(1_025_000_000)

    assert pose is not None
    assert math.isclose(pose.x, 1.5)
    assert math.isclose(pose.y, 2.5)
    assert math.isclose(pose.yaw, 0.3)


def test_interpolates_heading_across_angle_wrap():
    """Follow the short rotation through pi rather than rotating backward."""
    history = PoseHistory()
    history.add(Pose2D(0, 0.0, 0.0, math.radians(179.0)))
    history.add(Pose2D(100, 0.0, 0.0, math.radians(-179.0)))

    pose = history.lookup(50)

    assert pose is not None
    assert math.isclose(abs(pose.yaw), math.pi, abs_tol=1e-9)


def test_requires_pose_samples_on_both_sides():
    """Wait instead of extrapolating beyond the known vehicle trajectory."""
    history = PoseHistory()
    history.add(Pose2D(100, 1.0, 2.0, 0.0))
    history.add(Pose2D(200, 2.0, 3.0, 0.1))

    assert history.lookup(99) is None
    assert history.lookup(201) is None


def test_exact_timestamp_returns_recorded_pose():
    """Return an odometry pose unchanged when timestamps match exactly."""
    history = PoseHistory()
    expected = Pose2D(100, 1.0, 2.0, 0.3)
    history.add(expected)

    assert history.lookup(100) == expected


def test_time_reset_discards_old_simulation_poses():
    """Clear poses from the previous run when simulation time goes backward."""
    history = PoseHistory()
    history.add(Pose2D(200, 2.0, 0.0, 0.0))
    history.add(Pose2D(100, 1.0, 0.0, 0.0))

    assert history.oldest_stamp_ns == 100
    assert history.newest_stamp_ns == 100
    assert history.lookup(200) is None


def test_sharp_turn_observations_share_one_map_position():
    """Keep a fixed cone stationary while the vehicle turns between odometry."""
    history = PoseHistory()
    history.add(Pose2D(0, 0.0, 0.0, 0.0))
    history.add(Pose2D(200, 0.0, 0.0, math.pi / 2.0))

    first_pose = history.lookup(0)
    turning_pose = history.lookup(100)
    assert first_pose is not None
    assert turning_pose is not None

    first_observation = sensor_point_to_map(
        5.0, 5.0, 0.0, 0.0, first_pose)
    # At 45 degrees the same map point lies straight ahead in sensor space.
    turning_observation = sensor_point_to_map(
        math.sqrt(50.0), 0.0, 0.0, 0.0, turning_pose)

    assert math.isclose(first_observation[0], turning_observation[0])
    assert math.isclose(first_observation[1], turning_observation[1])
