"""Checks on the mounted Mid-360: pose, framing, determinism."""

import math

from lhr_lidar_sim.mid360 import Mid360Config
from lhr_lidar_sim.scene import CONE_HEIGHT_M
from lhr_lidar_sim.sensor import (
    Mid360Sensor, MountPose, points_on_cone, rotation_zyx)
import numpy as np
import pytest

NO_NOISE = Mid360Config(range_noise_std_m=0.0)


def test_zero_rotation_is_the_identity():
    assert np.allclose(rotation_zyx(0.0, 0.0, 0.0), np.eye(3))


def test_positive_pitch_tilts_the_x_axis_down():
    # The sign convention decides whether a Mid-360 looks at the track
    # or at the sky, so it gets a test rather than a comment.
    forward = rotation_zyx(0.0, math.radians(30.0), 0.0) @ np.array(
        [1.0, 0.0, 0.0])
    assert forward[2] < 0.0
    assert forward[2] == pytest.approx(-math.sin(math.radians(30.0)))


def test_yaw_rotates_forward_towards_left():
    turned = rotation_zyx(0.0, 0.0, math.radians(90.0)) @ np.array(
        [1.0, 0.0, 0.0])
    assert np.allclose(turned, [0.0, 1.0, 0.0], atol=1e-12)


def test_frame_is_deterministic_for_a_seed():
    cones = np.array([[8.0, 0.0], [8.0, 3.0]])
    cfg = Mid360Config(range_noise_std_m=0.02, dropout_rate=0.1)

    def once(seed):
        sensor = Mid360Sensor(config=cfg, mount=MountPose(), seed=seed)
        return sensor.frame(0.0, 0.0, 0.0, 0.0, cones)

    first, second, other = once(3), once(3), once(4)
    assert first.shape == second.shape
    assert np.array_equal(first, second)
    assert first.shape != other.shape or not np.array_equal(first, other)


def test_frame_returns_only_returns_not_every_beam():
    # Most of a 360 x 59 degree field of view sees empty sky, so a
    # frame must be far smaller than the beam count. A model that
    # returned one point per beam would be inventing surfaces.
    sensor = Mid360Sensor(config=NO_NOISE, mount=MountPose(), seed=1)
    points = sensor.frame(0.0, 0.0, 0.0, 0.0, np.empty((0, 2)))
    assert 0 < points.shape[0] < NO_NOISE.points_per_frame()
    assert points.shape[1] == 3


def test_labels_line_up_with_points():
    cones = np.array([[6.0, 0.0]])
    sensor = Mid360Sensor(config=NO_NOISE, mount=MountPose(), seed=1)
    points, on_cone = sensor.frame_labeled(0.0, 0.0, 0.0, 0.0, cones)
    assert on_cone.shape[0] == points.shape[0]
    assert on_cone.any(), 'a cone 6 m ahead should produce some returns'


def test_ground_returns_land_on_the_ground():
    # Transformed back out of the sensor frame, a ground return has to
    # sit at z = 0. This is what catches a wrong rotation: the points
    # would still exist, just in the wrong place.
    mount = MountPose(pitch_rad=math.radians(12.0))
    sensor = Mid360Sensor(config=NO_NOISE, mount=mount, seed=1)
    points, on_cone = sensor.frame_labeled(
        0.0, 0.0, 0.0, 0.0, np.empty((0, 2)))
    assert not on_cone.any()

    world = points @ mount.rotation().T
    world[:, 2] += mount.z_m
    assert np.allclose(world[:, 2], 0.0, atol=1e-9)


def test_cone_returns_sit_within_the_cone_height():
    cones = np.array([[1.8 + 6.0, 0.0]])
    mount = MountPose()
    sensor = Mid360Sensor(config=NO_NOISE, mount=mount, seed=1)
    points, on_cone = sensor.frame_labeled(0.0, 0.0, 0.0, 0.0, cones)

    world = points[on_cone] @ mount.rotation().T
    world[:, 2] += mount.z_m
    assert world.shape[0] > 0
    assert world[:, 2].min() >= -1e-9
    assert world[:, 2].max() <= CONE_HEIGHT_M + 1e-9


def test_vehicle_yaw_carries_the_sensor_around():
    # The same cone, approached from a yawed pose, must still be seen.
    # Placed so it is 6 m ahead of the mount in both cases.
    mount = MountPose()
    sensor = Mid360Sensor(config=NO_NOISE, mount=mount, seed=1)

    ahead = np.array([[mount.x_m + 6.0, 0.0]])
    _, straight = sensor.frame_labeled(0.0, 0.0, 0.0, 0.0, ahead)

    left = np.array([[0.0, mount.x_m + 6.0]])
    _, yawed = sensor.frame_labeled(
        0.0, 0.0, 0.0, math.radians(90.0), left)

    assert straight.sum() > 0
    assert yawed.sum() > 0
    # Same geometry, so comparable return counts.
    assert yawed.sum() == pytest.approx(straight.sum(), rel=0.6)


def test_returns_per_cone_fall_off_with_range():
    # The robust result across elevation profiles: a cone at range
    # gives very few returns. The absolute numbers are what bound
    # detection range, so the ordering is asserted, not the values.
    sensor = Mid360Sensor(config=NO_NOISE, mount=MountPose(), seed=1)
    near = points_on_cone(sensor, 0.0, 5.0, frames=10)
    mid = points_on_cone(sensor, 0.0, 10.0, frames=10)
    far = points_on_cone(sensor, 0.0, 20.0, frames=10)
    assert near > mid > far > 0


def test_a_cone_at_range_gives_only_a_handful_of_returns_per_frame():
    # Guards the finding the mount study rests on. If a refactor ever
    # makes this large, the model has started inventing returns.
    sensor = Mid360Sensor(config=NO_NOISE, mount=MountPose(), seed=1)
    per_second = points_on_cone(sensor, 0.0, 10.0, frames=10)
    assert 5 <= per_second <= 60


def test_nothing_beyond_max_range_comes_back():
    cfg = Mid360Config(range_noise_std_m=0.0, max_range_m=8.0)
    sensor = Mid360Sensor(config=cfg, mount=MountPose(), seed=1)
    points = sensor.frame(0.0, 0.0, 0.0, 0.0, np.empty((0, 2)))
    assert np.all(np.linalg.norm(points, axis=1) <= 8.0 + 1e-9)
