#!/usr/bin/env python3
"""
Adapter: AckermannDriveStamped -> individual joint commands for Gazebo.

Publishes:
  - 2x Float64 for steering joint positions (Ackermann-corrected angles)
  - 4x Float64 for wheel joint velocities (differential-corrected speeds)

Gazebo's joint controllers hold their last target, so without a timeout a
dead controller would leave the car driving on its last command. The real
car's actuators stop when commands stop (the VCU watchdog and the hardware
safety loop), so the adapter does the same: no command for cmd_timeout_s
zeroes the wheel speeds. Steering holds where it was.
"""

import math

from ackermann_msgs.msg import AckermannDriveStamped
from lhr_vehicle import load_vehicle
import rclpy
from rclpy.node import Node
from std_msgs.msg import Float64


class JointCmdAdapter(Node):

    def __init__(self):
        super().__init__('joint_cmd_adapter')

        veh = load_vehicle()
        self._wheelbase = veh.wheelbase_m
        self._track = veh.track_m
        self._wheel_radius = veh.wheel_radius_m
        self._max_steer = veh.max_steer_rad

        # 0 disables the timeout.
        self.declare_parameter('cmd_timeout_s', 0.3)
        self._cmd_timeout = float(self.get_parameter('cmd_timeout_s').value)
        self._last_cmd = None
        self._timed_out = False

        self._sub = self.create_subscription(
            AckermannDriveStamped, '/lhr/vehicle/cmd', self._cmd_cb, 10)

        # Steering position publishers
        self._pub_steer_fl = self.create_publisher(
            Float64,
            '/model/fsae_vehicle/joint/front_left_steering_joint/cmd_pos', 10)
        self._pub_steer_fr = self.create_publisher(
            Float64,
            '/model/fsae_vehicle/joint/front_right_steering_joint/cmd_pos', 10)

        # Wheel velocity publishers
        self._pub_vel_fl = self.create_publisher(
            Float64,
            '/model/fsae_vehicle/joint/front_left_wheel_joint/cmd_vel', 10)
        self._pub_vel_fr = self.create_publisher(
            Float64,
            '/model/fsae_vehicle/joint/front_right_wheel_joint/cmd_vel', 10)
        self._pub_vel_rl = self.create_publisher(
            Float64,
            '/model/fsae_vehicle/joint/rear_left_wheel_joint/cmd_vel', 10)
        self._pub_vel_rr = self.create_publisher(
            Float64,
            '/model/fsae_vehicle/joint/rear_right_wheel_joint/cmd_vel', 10)

        if self._cmd_timeout > 0.0:
            self.create_timer(0.05, lambda: self._watchdog(self._now()))

        self.get_logger().info(
            f'JointCmdAdapter ready  (L={self._wheelbase}, '
            f'track={self._track}, max_steer={self._max_steer}, '
            f'cmd_timeout={self._cmd_timeout}s)')

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    # ------------------------------------------------------------------
    def _watchdog(self, now: float):
        """Zero the wheel speeds once commands have stopped arriving."""
        if self._last_cmd is None or self._timed_out:
            return
        age = now - self._last_cmd
        # A clock that jumped backwards (a sim reset) is treated as a
        # timeout too, rather than as a command from the future.
        if age > self._cmd_timeout or age < 0.0:
            self._timed_out = True
            self.get_logger().warning(
                f'No command for {self._cmd_timeout:.2f} s, stopping wheels')
            for pub in (self._pub_vel_fl, self._pub_vel_fr,
                        self._pub_vel_rl, self._pub_vel_rr):
                pub.publish(Float64(data=0.0))

    def _cmd_cb(self, msg: AckermannDriveStamped):
        self._apply(msg.drive.speed, msg.drive.steering_angle, self._now())

    def _apply(self, speed: float, steering_angle: float, now: float):
        if self._timed_out:
            self.get_logger().info('Commands resumed')
        self._last_cmd = now
        self._timed_out = False
        steer = max(-self._max_steer, min(self._max_steer, steering_angle))

        left_angle, right_angle = self._ackermann_angles(steer)
        fl_vel, fr_vel, rl_vel, rr_vel = self._wheel_velocities(speed, steer)

        # Steering positions
        self._pub_steer_fl.publish(Float64(data=left_angle))
        self._pub_steer_fr.publish(Float64(data=right_angle))

        # Wheel velocities
        self._pub_vel_fl.publish(Float64(data=fl_vel))
        self._pub_vel_fr.publish(Float64(data=fr_vel))
        self._pub_vel_rl.publish(Float64(data=rl_vel))
        self._pub_vel_rr.publish(Float64(data=rr_vel))

    # ------------------------------------------------------------------
    def _ackermann_angles(self, steer_center: float):
        """
        Return (left_angle, right_angle) using Ackermann geometry.

        Positive steer = turn left.  When turning left the left wheel is
        the inner wheel (turns more) and the right is outer (turns less).
        """
        if abs(steer_center) < 1e-6:
            return 0.0, 0.0

        L, half_track = self._wheelbase, self._track / 2.0
        R = L / math.tan(abs(steer_center))
        inner = math.atan(L / (R - half_track))
        outer = math.atan(L / (R + half_track))

        if steer_center > 0:          # turning left
            return inner, outer
        else:                          # turning right
            return -outer, -inner

    # ------------------------------------------------------------------
    def _wheel_velocities(self, speed: float, steer_center: float):
        """
        Return (fl, fr, rl, rr) angular velocities in rad/s.

        Accounts for different turn radii at each wheel.
        """
        L, half_track, r = self._wheelbase, self._track / 2.0, self._wheel_radius
        omega_base = speed / r

        if abs(steer_center) < 1e-6:
            return omega_base, omega_base, omega_base, omega_base

        R = L / math.tan(abs(steer_center))
        omega_yaw = speed / R          # yaw rate of the vehicle

        # Rear wheels
        rl_omega = omega_yaw * (R - half_track) / r
        rr_omega = omega_yaw * (R + half_track) / r

        # Front wheels (further from ICR due to wheelbase offset)
        R_fl = math.sqrt(L**2 + (R - half_track)**2)
        R_fr = math.sqrt(L**2 + (R + half_track)**2)
        fl_omega = omega_yaw * R_fl / r
        fr_omega = omega_yaw * R_fr / r

        if steer_center < 0:           # turning right, swap inner/outer
            rl_omega, rr_omega = rr_omega, rl_omega
            fl_omega, fr_omega = fr_omega, fl_omega

        return fl_omega, fr_omega, rl_omega, rr_omega


def main(args=None):
    rclpy.init(args=args)
    node = JointCmdAdapter()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.try_shutdown()


if __name__ == '__main__':
    main()
