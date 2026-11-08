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
    CMD_STALE = 6
    CMD_INVALID = 7


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
    # pure_pursuit publishes at 20 Hz once it has a mission status, so
    # silence means the controller died; the actuators would hold the last
    # command.
    cmd_timeout_s: float = 0.25
    # A divergence bound, not an accuracy bound: the MVP's 0.5 m accuracy
    # check is scored against truth, which this node never reads. 0 turns it
    # off, the default: without GNSS the EKF's position variance starts at
    # 1 m^2 and only grows, so any bound trips at startup. Set it once GNSS
    # keeps the covariance bounded.
    max_position_std_m: float = 0.0
    # Speed above this multiple of the car's top speed is a broken estimate.
    max_speed_factor: float = 1.5
    # Holds any course the rules checker accepts: no point on a closed loop
    # is farther from another than half the lap, and laps are at most 500 m.
    # Acceleration reaches 152 m with its braking zone. Sim gate runs then
    # share one fence; the car gets the surveyed venue boundary (#229).
    geofence_radius_m: float = 300.0
    # STEER_TRACKING compares the measured steering angle (the steering
    # encoder; Gazebo's steering joints in sim) with the command, once the
    # actuator has had time to get there at max_steer_rate_rad_s (#208).
    # The node fills the rate and the lock in from vehicle.yaml. The
    # actuator clamps a command past the lock, so the reference does too: a
    # saturated actuator is not a stuck one.
    max_steer_rate_rad_s: float = math.inf
    max_steer_rad: float = math.inf
    # About 2 degrees, held for ten 50 Hz samples: a stuck actuator, not
    # transport delay.
    steer_angle_tol_rad: float = 0.035
    steer_angle_window_s: float = 0.2
    # A measured angle that stops arriving, or never arrives, means the
    # steering can no longer be checked, so it trips too.
    steer_angle_timeout_s: float = 0.25
    # The car and Gazebo always publish the measured angle, so a missing one
    # is a fault from startup like any other stale stream; a wrong topic or
    # a dead encoder must not quietly hand the check to the weaker yaw-rate
    # rule. Only a plant with no steering measurement turns this off.
    require_steer_measurement: bool = True
    # The fallback when require_steer_measurement is off and no measured
    # angle has arrived: compare the yaw rate with what the command should
    # produce.
    # Yaw rate is noise-dominated near standstill, so this only runs above
    # steer_min_speed_mps.
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
        self._last_cmd: Optional[float] = None
        self._pose: Optional[tuple] = None
        self._origin: Optional[tuple] = None
        # Odom arrives faster than check() runs, so a one-sample excursion
        # is remembered until the next check rather than overwritten by the
        # sample after it.
        self._diverged = self._diverged_seen = False
        self._outside = self._outside_seen = False
        self._cmd_invalid = self._cmd_invalid_seen = False
        self._steer_cmd: Optional[float] = None
        self._mismatch_since: Optional[float] = None
        self._steer_fault = False
        # Measured-angle rule: the reference the actuator should have
        # reached by now, and when the measurement last arrived.
        self._last_steer_measured: Optional[float] = None
        self._steer_ref: Optional[float] = None
        self._angle_mismatch_since: Optional[float] = None
        self._angle_fault = False

    def on_lidar(self, now: float):
        self._last_lidar = now

    def on_command(self, now: float, steering_angle: float):
        self._last_cmd = now
        # A NaN steering angle clamps to full lock in the actuator adapter.
        self._cmd_invalid = not math.isfinite(steering_angle)
        self._cmd_invalid_seen |= self._cmd_invalid
        self._steer_cmd = None if self._cmd_invalid else steering_angle

    def anchor_geofence(self):
        """Centre the test area on the latest pose; call when arming."""
        self._origin = self._pose
        self._outside = False

    def on_odom(self, now: float, x: float, y: float, vx: float, vy: float,
                yaw_rate: float, var_x: float, var_y: float):
        """Feed one odometry sample, twist in the body frame (REP-105)."""
        cfg = self._config
        self._last_odom = now
        values = (x, y, vx, vy, yaw_rate, var_x, var_y)
        if not all(math.isfinite(v) for v in values):
            self._diverged = self._diverged_seen = True
            return
        speed = math.hypot(vx, vy)
        std = math.sqrt(max(var_x, var_y, 0.0))
        too_uncertain = 0.0 < cfg.max_position_std_m < std
        self._diverged = (too_uncertain
                          or speed > cfg.max_speed_factor * cfg.max_speed_mps)
        self._diverged_seen |= self._diverged

        # The fence is centred where the car arms, the way a surveyed fence
        # is anchored to the start box, not wherever the stack booted.
        self._pose = (x, y)
        if self._origin is not None:
            dist = math.hypot(x - self._origin[0], y - self._origin[1])
            self._outside = dist > cfg.geofence_radius_m
            self._outside_seen |= self._outside

        self._check_steering(now, vx, yaw_rate)

    def on_steering(self, now: float, angle: float):
        """Feed one measured steering angle (rad, bicycle-equivalent)."""
        cfg = self._config
        previous = self._last_steer_measured
        self._last_steer_measured = now
        if not math.isfinite(angle):
            ok = False
        elif self._steer_cmd is None:
            # Nothing to follow: the reference sits where the wheels are.
            self._steer_ref = angle
            ok = True
        else:
            if self._steer_ref is None or previous is None or now < previous:
                self._steer_ref = angle
            else:
                # No time passed, no movement, even at an unlimited rate.
                dt = now - previous
                step = cfg.max_steer_rate_rad_s * dt if dt > 0.0 else 0.0
                target = max(-cfg.max_steer_rad, min(cfg.max_steer_rad, self._steer_cmd))
                self._steer_ref += max(-step, min(step, target - self._steer_ref))
            ok = abs(angle - self._steer_ref) <= cfg.steer_angle_tol_rad
        if ok:
            self._angle_mismatch_since = None
            self._angle_fault = False
            return
        if self._angle_mismatch_since is None or self._angle_mismatch_since > now:
            self._angle_mismatch_since = now
        self._angle_fault = now - self._angle_mismatch_since >= cfg.steer_angle_window_s

    def _check_steering(self, now: float, forward: float, yaw_rate: float):
        """Compare measured yaw rate with what the command should produce."""
        cfg = self._config
        if cfg.require_steer_measurement or self._last_steer_measured is not None:
            return  # the measured-angle rule owns STEER_TRACKING
        # Signed forward speed: reversing flips the sign of the yaw rate a
        # given steering angle produces.
        if self._steer_cmd is None or abs(forward) < cfg.steer_min_speed_mps:
            self._mismatch_since = None
            self._steer_fault = False
            return
        expected = forward * math.tan(self._steer_cmd) / cfg.wheelbase_m
        tol = max(cfg.steer_yaw_rate_tol, cfg.steer_rel_tol * abs(expected))
        if abs(yaw_rate - expected) <= tol:
            self._mismatch_since = None
            self._steer_fault = False
            return
        if self._mismatch_since is None or self._mismatch_since > now:
            self._mismatch_since = now
        self._steer_fault = now - self._mismatch_since >= cfg.steer_window_s

    def _age(self, last: Optional[float], now: float) -> float:
        # Never having heard a topic counts from startup, so a sensor that
        # never comes up is stale rather than silently ignored.
        age = now - (self._start if last is None else last)
        # A clock that jumped backwards (a sim reset) leaves every stored
        # time in the future. Treat that as stale, not as perfectly fresh.
        return math.inf if age < 0 else age

    def check(self, now: float) -> Fault:
        """Return the highest-priority fault right now, or NONE."""
        cfg = self._config
        diverged = self._diverged or self._diverged_seen
        outside = self._outside or self._outside_seen
        cmd_invalid = self._cmd_invalid or self._cmd_invalid_seen
        self._diverged_seen = self._outside_seen = False
        self._cmd_invalid_seen = False

        if self._age(self._last_odom, now) > cfg.odom_timeout_s:
            return Fault.ODOM_STALE
        if diverged:
            return Fault.EKF_DIVERGED
        if self._age(self._last_cmd, now) > cfg.cmd_timeout_s:
            return Fault.CMD_STALE
        if cmd_invalid:
            return Fault.CMD_INVALID
        if (cfg.monitor_lidar
                and self._age(self._last_lidar, now) > cfg.lidar_timeout_s):
            return Fault.LIDAR_STALE
        if outside:
            return Fault.GEOFENCE
        if cfg.require_steer_measurement or self._last_steer_measured is not None:
            if (self._angle_fault or self._age(self._last_steer_measured, now)
                    > cfg.steer_angle_timeout_s):
                return Fault.STEER_TRACKING
        elif self._steer_fault:
            return Fault.STEER_TRACKING
        return Fault.NONE
