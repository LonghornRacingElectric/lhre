#!/usr/bin/env python3
"""
A Mid-360 bolted to the car: mount pose, scan, cast, noise. Still no ROS.

The mount position comes from ``lhr_vehicle`` because vehicle numbers
live in one file. The mount *orientation* does not: it is the answer the
mount study has yet to produce, so it stays a parameter here and gets
written into ``vehicle.yaml`` once measured rather than guessed into it
now.
"""

from dataclasses import dataclass, field
import math

from lhr_lidar_sim.mid360 import (
    apply_range_model, beam_angles, Mid360Config, unit_directions)
from lhr_lidar_sim.scene import cast
import numpy as np


def rotation_zyx(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """
    Build the ROS yaw-pitch-roll rotation matrix.

    Positive pitch tilts the sensor's x axis downward, so pitching a
    Mid-360 down to put cones in its field of view is a positive
    number. That is worth stating, because the sensor sees from -7 to
    +52 degrees and the sign decides whether it sees the track or the
    sky.
    """
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return np.array([
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ])


@dataclass
class MountPose:
    """Where the sensor sits on the car, in base_link."""

    # Only for using this library directly. lidar_sim_node overrides all
    # three from vehicle.yaml, which is the source of truth; these are
    # kept in step with it so an offline scene is not a different car.
    x_m: float = 1.8
    y_m: float = 0.0
    z_m: float = 0.62
    roll_rad: float = 0.0
    pitch_rad: float = 0.0
    yaw_rad: float = 0.0

    def rotation(self) -> np.ndarray:
        """Return the sensor-to-base_link rotation."""
        return rotation_zyx(self.roll_rad, self.pitch_rad, self.yaw_rad)


@dataclass
class Mid360Sensor:
    """Produces one frame of points for a vehicle pose and a cone set."""

    config: Mid360Config = field(default_factory=Mid360Config)
    mount: MountPose = field(default_factory=MountPose)
    seed: int = 1

    def __post_init__(self):
        # Node-local, like the sensor sim's: drawing from the process
        # global stream means no run ever repeats.
        self._rng = np.random.default_rng(self.seed)

    def frame(self, t: float, vehicle_x: float, vehicle_y: float,
              vehicle_yaw: float, cones_xy: np.ndarray) -> np.ndarray:
        """Return an (N, 3) point cloud in the sensor frame."""
        points, _ = self.frame_labeled(
            t, vehicle_x, vehicle_y, vehicle_yaw, cones_xy)
        return points

    def frame_labeled(self, t: float, vehicle_x: float, vehicle_y: float,
                      vehicle_yaw: float,
                      cones_xy: np.ndarray) -> tuple:
        """
        Return (points, on_cone) for one frame, in the sensor frame.

        N is not the beam count: beams that reach nothing, fall outside
        the range window or drop out are absent, exactly as a real
        sensor reports only its returns. A caller counting points is
        therefore counting returns, which is what the mount study
        wants.
        """
        az, el = beam_angles(self.config, t)
        dirs_sensor = unit_directions(az, el)

        # sensor -> base_link -> world. Composed once, applied to all
        # 20,000 beams at once.
        world_from_sensor = (rotation_zyx(0.0, 0.0, vehicle_yaw)
                             @ self.mount.rotation())
        dirs_world = dirs_sensor @ world_from_sensor.T

        mount_offset = np.array(
            [self.mount.x_m, self.mount.y_m, self.mount.z_m])
        origin = (np.array([vehicle_x, vehicle_y, 0.0])
                  + rotation_zyx(0.0, 0.0, vehicle_yaw) @ mount_offset)

        cones = np.asarray(cones_xy, dtype=np.float64)
        if cones.size:
            cones = cones.reshape(-1, 2) - origin[:2]
        else:
            cones = np.empty((0, 2))

        ranges, hit, on_cone = cast(dirs_world, cones, float(origin[2]),
                                    max_range=self.config.max_range_m)
        # cast leaves misses at infinity, which would poison the noise
        # draw and the comparisons in the range model.
        ranges = np.where(hit, ranges, 0.0)
        hit = apply_range_model(ranges, hit, self.config, self._rng)

        if not np.any(hit):
            return np.empty((0, 3)), np.zeros(0, dtype=bool)

        points_world = dirs_world[hit] * ranges[hit, None]
        # Back into the sensor frame, which is what frame_id promises.
        return points_world @ world_from_sensor, on_cone[hit]


def points_on_cone(sensor: Mid360Sensor, t: float, distance_m: float,
                   frames: int = 1) -> int:
    """
    Count returns landing on a single cone straight ahead.

    This is the mount study's measurement, kept here so the study and
    the simulator cannot drift apart. Points are counted by proximity
    to the cone rather than by any flag the caster sets, so the count
    means the same thing it would on a recorded cloud.

    ``distance_m`` is measured from the *sensor*, not from base_link.
    The mount sits 1.8 m ahead of the rear axle, and measuring from the
    axle quietly shortens every range by that much, which near the
    field of view floor is the difference between seeing a cone and
    seeing nothing at all.
    """
    cones = np.array([[sensor.mount.x_m + distance_m, 0.0]])
    total = 0
    period = 1.0 / sensor.config.frame_rate_hz
    for k in range(frames):
        _, on_cone = sensor.frame_labeled(
            t + k * period, 0.0, 0.0, 0.0, cones)
        total += int(np.count_nonzero(on_cone))
    return total
