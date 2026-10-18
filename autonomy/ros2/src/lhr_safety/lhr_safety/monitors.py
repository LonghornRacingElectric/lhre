"""
Fault checks for the software half of the safety loop.

Free of ROS imports so every check is unit tested directly; the node only
feeds timestamps and message fields in and acts on the answer. Times are
seconds on whatever clock the caller uses, as long as it is one clock.
"""

from dataclasses import dataclass
from enum import IntEnum
import math
from typing import Optional


class Fault(IntEnum):
    """
    Why the stack is not safe to drive. 0 means healthy.

    The values are the status code the heartbeat will carry on CAN, so they
    are append-only: never renumber or reuse one.
    """

    NONE = 0
    ODOM_STALE = 1
    EKF_DIVERGED = 2
    LIDAR_STALE = 3
    GEOFENCE = 4
    STEER_TRACKING = 5


@dataclass(frozen=True)
class SafetyConfig:
    """Thresholds. Defaults are starting points for #117 to tune."""

    wheelbase_m: float
    max_speed_mps: float
    monitor_lidar: bool = True
    # Three missed frames at 10 Hz. One late frame is normal jitter.
    lidar_timeout_s: float = 0.3
    # The EKF and the Gazebo bridge both publish odom at 50 Hz or more.
    odom_timeout_s: float = 0.25
    # A divergence bound, not an accuracy bound: the MVP's 0.5 m accuracy
    # check is scored against truth, which this node never reads. 0 turns it
    # off, the default: without GNSS the EKF's position variance starts at
    # 1 m^2 and only grows, so any bound trips at startup. Set it once GNSS
    # keeps the covariance bounded.
    max_position_std_m: float = 0.0
    # Speed above this multiple of the car's top speed is a broken estimate.
    max_speed_factor: float = 1.5
    geofence_radius_m: float = 150.0
    # Yaw rate is noise-dominated near standstill, so the steering check
    # only runs above this speed.
    steer_min_speed_mps: float = 2.0
    steer_yaw_rate_tol: float = 0.15
    steer_rel_tol: float = 0.5
    # Long enough to ride through the steering actuator's rate limit.
    steer_window_s: float = 1.0


class SafetyMonitor:
    """Tracks the latest inputs and reports the highest-priority fault."""

    def __init__(self, config: SafetyConfig, now: float):
        self._config = config
        self._start = now
        self._last_lidar: Optional[float] = None
        self._last_odom: Optional[float] = None
        self._origin: Optional[tuple] = None
        self._diverged = False
        self._outside = False
        self._steer_cmd: Optional[float] = None
        self._mismatch_since: Optional[float] = None
        self._steer_fault = False

    def on_lidar(self, now: float):
        self._last_lidar = now

    def on_command(self, steering_angle: float):
        self._steer_cmd = steering_angle

    def on_odom(self, now: float, x: float, y: float, vx: float, vy: float,
                yaw_rate: float, var_x: float, var_y: float):
        """Feed one odometry sample; velocity may be in any planar frame."""
        cfg = self._config
        self._last_odom = now
        values = (x, y, vx, vy, yaw_rate, var_x, var_y)
        if not all(math.isfinite(v) for v in values):
            self._diverged = True
            return
        speed = math.hypot(vx, vy)
        std = math.sqrt(max(var_x, var_y, 0.0))
        too_uncertain = 0.0 < cfg.max_position_std_m < std
        self._diverged = (too_uncertain
                          or speed > cfg.max_speed_factor * cfg.max_speed_mps)

        # The test area is anchored where the car first reports a pose,
        # the way a surveyed fence is anchored to the start box.
        if self._origin is None:
            self._origin = (x, y)
        dist = math.hypot(x - self._origin[0], y - self._origin[1])
        self._outside = dist > cfg.geofence_radius_m

        self._check_steering(now, speed, yaw_rate)

    def _check_steering(self, now: float, speed: float, yaw_rate: float):
        """Compare measured yaw rate with what the command should produce."""
        cfg = self._config
        if self._steer_cmd is None or speed < cfg.steer_min_speed_mps:
            self._mismatch_since = None
            self._steer_fault = False
            return
        expected = speed * math.tan(self._steer_cmd) / cfg.wheelbase_m
        tol = max(cfg.steer_yaw_rate_tol, cfg.steer_rel_tol * abs(expected))
        if abs(yaw_rate - expected) <= tol:
            self._mismatch_since = None
            self._steer_fault = False
            return
        if self._mismatch_since is None:
            self._mismatch_since = now
        self._steer_fault = now - self._mismatch_since >= cfg.steer_window_s

    def _age(self, last: Optional[float], now: float) -> float:
        # Never having heard a topic counts from startup, so a sensor that
        # never comes up is stale rather than silently ignored.
        return now - (self._start if last is None else last)

    def check(self, now: float) -> Fault:
        """Return the highest-priority fault right now, or NONE."""
        cfg = self._config
        if self._age(self._last_odom, now) > cfg.odom_timeout_s:
            return Fault.ODOM_STALE
        if self._diverged:
            return Fault.EKF_DIVERGED
        if (cfg.monitor_lidar
                and self._age(self._last_lidar, now) > cfg.lidar_timeout_s):
            return Fault.LIDAR_STALE
        if self._outside:
            return Fault.GEOFENCE
        if self._steer_fault:
            return Fault.STEER_TRACKING
        return Fault.NONE
