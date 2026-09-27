#!/usr/bin/env python3
"""
Synthesize IMU and wheel-speed sensors by degrading ground-truth odometry.

Same idea as the cone sensor sim: take what the simulator knows perfectly,
apply the error a real sensor would have, and publish that. Feeding raw truth
to the estimator would prove nothing, so noise, bias and rolling-radius scale
error are the point of this node, not decoration.

Speed and yaw rate are differentiated from the truth *pose* rather than read
from its twist, because the kinematic sim reports twist in the world frame
and Gazebo reports it in the body frame; a pose difference means the same
thing in both.

The real car reports four wheel speeds (USM, one CAN frame per corner), so
this publishes four even though the estimator only consumes the rear pair.
"""

import math

from lhr_vehicle import load_vehicle
from nav_msgs.msg import Odometry
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.time import Time
from sensor_msgs.msg import Imu, JointState


def quat_to_yaw(q) -> float:
    """Extract the yaw angle (rad) from a quaternion."""
    return math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                      1.0 - 2.0 * (q.y * q.y + q.z * q.z))


def wrap_angle(angle: float) -> float:
    """Wrap an angle into [-pi, pi)."""
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


class InertialSim(Node):
    """Publish noisy IMU and wheel-speed messages from truth odometry."""

    def __init__(self):
        super().__init__('inertial_sim')

        veh = load_vehicle()
        self.declare_parameter('truth_topic', '/lhr/vehicle/odom_truth')
        self.declare_parameter('wheel_topic', '/lhr/sensor/wheel_speeds')
        self.declare_parameter('imu_topic', '/lhr/imu/data')
        # Gazebo bridges a physics IMU on the same topic; turn this off there.
        self.declare_parameter('publish_imu', True)
        self.declare_parameter('wheel_names', ['fl', 'fr', 'rl', 'rr'])
        self.declare_parameter('frame_id', 'base_link')
        self.declare_parameter('wheel_radius', veh.wheel_radius_m)
        self.declare_parameter('track', veh.track_m)
        self.declare_parameter('gyro_noise_std', 0.01)
        self.declare_parameter('gyro_bias', 0.0)
        self.declare_parameter('accel_noise_std', 0.1)
        self.declare_parameter('accel_bias', 0.0)
        self.declare_parameter('wheel_speed_noise_std', 0.05)
        # 1.0 is a perfectly known rolling radius; real tyres are not.
        self.declare_parameter('wheel_scale_error', 1.0)
        self.declare_parameter('seed', 0)

        self._wheel_names = list(self.get_parameter(
            'wheel_names').get_parameter_value().string_array_value)
        self._frame_id = self._str_param('frame_id')
        self._radius = self._float_param('wheel_radius')
        self._half_track = self._float_param('track') / 2.0
        self._gyro_std = self._float_param('gyro_noise_std')
        self._gyro_bias = self._float_param('gyro_bias')
        self._accel_std = self._float_param('accel_noise_std')
        self._accel_bias = self._float_param('accel_bias')
        self._wheel_std = self._float_param('wheel_speed_noise_std')
        self._wheel_scale = self._float_param('wheel_scale_error')
        self._publish_imu = self.get_parameter(
            'publish_imu').get_parameter_value().bool_value
        self._rng = np.random.default_rng(
            self.get_parameter('seed').get_parameter_value().integer_value)

        self._prev = None
        self._prev_speed = 0.0

        self.create_subscription(
            Odometry, self._str_param('truth_topic'), self._truth_cb, 50)
        self._wheel_pub = self.create_publisher(
            JointState, self._str_param('wheel_topic'), 50)
        self._imu_pub = self.create_publisher(
            Imu, self._str_param('imu_topic'), 50)

        self.get_logger().info(
            f'InertialSim ready  (imu={self._publish_imu}, '
            f'gyro_std={self._gyro_std}, wheel_std={self._wheel_std}, '
            f'scale={self._wheel_scale})')

    # ------------------------------------------------------------------
    def _str_param(self, name: str) -> str:
        return self.get_parameter(name).get_parameter_value().string_value

    def _float_param(self, name: str) -> float:
        return self.get_parameter(name).get_parameter_value().double_value

    # ------------------------------------------------------------------
    def _truth_cb(self, msg: Odometry):
        t = Time.from_msg(msg.header.stamp).nanoseconds * 1e-9
        x = msg.pose.pose.position.x
        y = msg.pose.pose.position.y
        yaw = quat_to_yaw(msg.pose.pose.orientation)

        if self._prev is None:
            self._prev = (t, x, y, yaw)
            return

        t_prev, x_prev, y_prev, yaw_prev = self._prev
        dt = t - t_prev
        if dt <= 0.0:
            return
        self._prev = (t, x, y, yaw)

        # Signed speed: distance travelled, negative when it runs against
        # the heading the vehicle held over the step.
        dx, dy = x - x_prev, y - y_prev
        speed = math.hypot(dx, dy) / dt
        if dx * math.cos(yaw_prev) + dy * math.sin(yaw_prev) < 0.0:
            speed = -speed
        yaw_rate = wrap_angle(yaw - yaw_prev) / dt
        accel = (speed - self._prev_speed) / dt
        self._prev_speed = speed

        self._publish_wheels(msg.header.stamp, speed, yaw_rate)
        if self._publish_imu:
            self._publish_imu_msg(msg.header.stamp, speed, yaw_rate, accel)

    def _publish_wheels(self, stamp, speed: float, yaw_rate: float):
        # A wheel at body-frame offset y sees speed - yaw_rate * y; left
        # wheels sit at +half_track. Steering is ignored: at FSAE lock the
        # rolling-speed error is far below the noise floor.
        lateral = yaw_rate * self._half_track
        wheel_speeds = [speed - lateral, speed + lateral,
                        speed - lateral, speed + lateral]

        msg = JointState()
        msg.header.stamp = stamp
        msg.header.frame_id = self._frame_id
        msg.name = list(self._wheel_names)
        msg.velocity = [
            (w / self._radius) * self._wheel_scale
            + self._rng.normal(0.0, self._wheel_std)
            for w in wheel_speeds
        ]
        self._wheel_pub.publish(msg)

    def _publish_imu_msg(self, stamp, speed: float, yaw_rate: float,
                         accel: float):
        msg = Imu()
        msg.header.stamp = stamp
        msg.header.frame_id = self._frame_id
        # No orientation solution, per REP-145.
        msg.orientation_covariance[0] = -1.0
        msg.angular_velocity.z = (
            yaw_rate + self._gyro_bias + self._rng.normal(0.0, self._gyro_std))
        msg.linear_acceleration.x = (
            accel + self._accel_bias + self._rng.normal(0.0, self._accel_std))
        msg.linear_acceleration.y = (
            speed * yaw_rate + self._rng.normal(0.0, self._accel_std))
        msg.angular_velocity_covariance[8] = self._gyro_std ** 2
        msg.linear_acceleration_covariance[0] = self._accel_std ** 2
        self._imu_pub.publish(msg)


def main():
    """Entry point."""
    rclpy.init()
    node = InertialSim()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()
