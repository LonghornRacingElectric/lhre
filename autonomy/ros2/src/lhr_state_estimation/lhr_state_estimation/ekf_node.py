#!/usr/bin/env python3
"""EKF state estimator: IMU + wheel speeds in, odometry + TF out."""

import math

from geometry_msgs.msg import Quaternion, TransformStamped, Vector3
from lhr_state_estimation.ekf import IDX_V, IDX_X, IDX_Y, IDX_YAW, IDX_YAW_RATE, VehicleEkf
from lhr_state_estimation.wheel_odometry import body_speed
from lhr_vehicle import load_vehicle
from nav_msgs.msg import Odometry
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Imu, JointState
from tf2_ros import TransformBroadcaster


def quat_to_yaw(q) -> float:
    """Extract the yaw angle (rad) from a quaternion."""
    return math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                      1.0 - 2.0 * (q.y * q.y + q.z * q.z))


def yaw_to_quat(yaw: float) -> Quaternion:
    """Convert a yaw angle (rad) to a z-axis quaternion."""
    q = Quaternion()
    q.z = math.sin(yaw / 2.0)
    q.w = math.cos(yaw / 2.0)
    return q


class EkfNode(Node):
    """
    Estimate the vehicle state from inertial and wheel-speed sensors.

    Prediction runs on the publish timer rather than on IMU arrival, so a
    sensor dropout degrades the estimate instead of freezing it. The IMU
    supplies the yaw-rate correction and the acceleration that drives the
    prediction; the rear wheels supply the speed correction.
    """

    def __init__(self):
        super().__init__('ekf_node')

        veh = load_vehicle()
        self.declare_parameter('imu_topic', '/lhr/imu/data')
        self.declare_parameter('wheel_topic', '/lhr/sensor/wheel_speeds')
        self.declare_parameter('odom_topic', '/lhr/vehicle/odom')
        self.declare_parameter('publish_hz', 50.0)
        self.declare_parameter('frame_id', 'map')
        self.declare_parameter('child_frame_id', 'base_link')
        self.declare_parameter('publish_tf', True)
        self.declare_parameter('wheel_radius', veh.wheel_radius_m)
        self.declare_parameter('rear_wheel_names', ['rl', 'rr'])
        # The car gets its start pose from a survey or GNSS; sim gets it from
        # the same launch arguments that place the vehicle.
        self.declare_parameter('init_x', 0.0)
        self.declare_parameter('init_y', 0.0)
        self.declare_parameter('init_yaw', 0.0)
        # Seed the pose from the first message on this topic instead, for
        # sources whose start pose the launch file does not know (Gazebo
        # spawns the car wherever the world puts it). Stands in for the
        # survey or GNSS fix that will seed the filter on the car.
        self.declare_parameter('init_pose_topic', '')
        # Tuning. Process terms are how much the model is trusted to hold
        # between corrections; measurement terms are sensor variances.
        self.declare_parameter('sigma_accel', 1.0)
        self.declare_parameter('sigma_yaw_accel', 1.0)
        self.declare_parameter('speed_variance', 0.01)
        self.declare_parameter('yaw_rate_variance', 0.0004)

        self._frame_id = self._str_param('frame_id')
        self._child_frame_id = self._str_param('child_frame_id')
        self._publish_tf = self.get_parameter(
            'publish_tf').get_parameter_value().bool_value
        self._wheel_radius = self._float_param('wheel_radius')
        self._rear_names = list(self.get_parameter(
            'rear_wheel_names').get_parameter_value().string_array_value)
        self._speed_var = self._float_param('speed_variance')
        self._yaw_rate_var = self._float_param('yaw_rate_variance')

        self._ekf = VehicleEkf(
            sigma_accel=self._float_param('sigma_accel'),
            sigma_yaw_accel=self._float_param('sigma_yaw_accel'))
        self._ekf.reset(
            x=self._float_param('init_x'),
            y=self._float_param('init_y'),
            yaw=self._float_param('init_yaw'))

        self._accel_x = 0.0
        self._last_predict = self.get_clock().now()
        self._warned_wheels = False

        init_topic = self._str_param('init_pose_topic')
        self._awaiting_init = bool(init_topic)
        if init_topic:
            self.create_subscription(
                Odometry, init_topic, self._init_pose_cb, 10)

        self.create_subscription(
            Imu, self._str_param('imu_topic'), self._imu_cb, 50)
        self.create_subscription(
            JointState, self._str_param('wheel_topic'), self._wheel_cb, 50)
        self._odom_pub = self.create_publisher(
            Odometry, self._str_param('odom_topic'), 10)
        self._tf_bc = TransformBroadcaster(self)

        publish_hz = self._float_param('publish_hz')
        self.create_timer(1.0 / publish_hz, self._step)
        self.get_logger().info(
            f'EKF ready  (hz={publish_hz}, r={self._wheel_radius:.4f}, '
            f'rear={self._rear_names})')

    # ------------------------------------------------------------------
    def _str_param(self, name: str) -> str:
        return self.get_parameter(name).get_parameter_value().string_value

    def _float_param(self, name: str) -> float:
        return self.get_parameter(name).get_parameter_value().double_value

    # ------------------------------------------------------------------
    # Sensor callbacks
    # ------------------------------------------------------------------
    def _init_pose_cb(self, msg: Odometry):
        if not self._awaiting_init:
            return
        x = msg.pose.pose.position.x
        y = msg.pose.pose.position.y
        yaw = quat_to_yaw(msg.pose.pose.orientation)
        self._ekf.reset(x=x, y=y, yaw=yaw)
        self._awaiting_init = False
        self.get_logger().info(
            f'seeded pose: x={x:.2f} y={y:.2f} yaw={yaw:.3f}')

    def _imu_cb(self, msg: Imu):
        self._accel_x = msg.linear_acceleration.x
        self._ekf.update_yaw_rate(msg.angular_velocity.z, self._yaw_rate_var)

    def _wheel_cb(self, msg: JointState):
        speeds = []
        for name in self._rear_names:
            if name not in msg.name:
                if not self._warned_wheels:
                    self.get_logger().warn(
                        f'wheel joint {name!r} missing from {list(msg.name)}; '
                        'speed corrections are off')
                    self._warned_wheels = True
                return
            speeds.append(msg.velocity[msg.name.index(name)])

        self._ekf.update_speed(
            body_speed(speeds[0], speeds[1], self._wheel_radius),
            self._speed_var)

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------
    def _step(self):
        """Predict to the current time and publish the estimate."""
        now = self.get_clock().now()
        dt = (now - self._last_predict).nanoseconds * 1e-9
        self._last_predict = now
        if self._awaiting_init:
            return
        self._ekf.predict(dt, self._accel_x)

        x, y, yaw = self._ekf.pose
        v, yaw_rate = self._ekf.twist
        stamp = now.to_msg()

        odom = Odometry()
        odom.header.stamp = stamp
        odom.header.frame_id = self._frame_id
        odom.child_frame_id = self._child_frame_id
        odom.pose.pose.position.x = x
        odom.pose.pose.position.y = y
        odom.pose.pose.orientation = yaw_to_quat(yaw)
        # Body frame, per REP-103: the sim publishes the same magnitude.
        odom.twist.twist.linear = Vector3(x=v, y=0.0, z=0.0)
        odom.twist.twist.angular = Vector3(x=0.0, y=0.0, z=yaw_rate)

        p = self._ekf.P
        odom.pose.covariance[0] = p[IDX_X, IDX_X]
        odom.pose.covariance[7] = p[IDX_Y, IDX_Y]
        odom.pose.covariance[35] = p[IDX_YAW, IDX_YAW]
        odom.twist.covariance[0] = p[IDX_V, IDX_V]
        odom.twist.covariance[35] = p[IDX_YAW_RATE, IDX_YAW_RATE]
        self._odom_pub.publish(odom)

        if self._publish_tf:
            t = TransformStamped()
            t.header.stamp = stamp
            t.header.frame_id = self._frame_id
            t.child_frame_id = self._child_frame_id
            t.transform.translation.x = x
            t.transform.translation.y = y
            t.transform.rotation = yaw_to_quat(yaw)
            self._tf_bc.sendTransform(t)


def main():
    """Entry point."""
    rclpy.init()
    node = EkfNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()
