#!/usr/bin/env python3
"""Kinematic bicycle-model vehicle simulator."""

import math

from ackermann_msgs.msg import AckermannDriveStamped
from geometry_msgs.msg import Quaternion, TransformStamped, Vector3
from lhr_vehicle import load_vehicle
from nav_msgs.msg import Odometry
import rclpy
from rclpy.node import Node
from rclpy.time import Time
from rosgraph_msgs.msg import Clock
from tf2_ros import TransformBroadcaster


def yaw_to_quat(yaw: float) -> Quaternion:
    """Convert a yaw angle (rad) to a z-axis quaternion."""
    q = Quaternion()
    q.z = math.sin(yaw / 2.0)
    q.w = math.cos(yaw / 2.0)
    return q


class SimKinematic(Node):
    """Integrate a kinematic bicycle model and publish odom + TF."""

    def __init__(self, node_name='sim_kinematic'):
        super().__init__(node_name)

        # --- Parameters (vehicle defaults come from lhr_vehicle) ---
        veh = load_vehicle()
        self.declare_parameter('wheelbase', veh.wheelbase_m)
        self.declare_parameter('update_hz', 50.0)
        self.declare_parameter('max_steer', veh.max_steer_rad)
        self.declare_parameter('max_speed', veh.max_speed_mps)
        self.declare_parameter('frame_id', 'map')
        self.declare_parameter('child_frame_id', 'base_link')
        self.declare_parameter('init_x', 0.0)
        self.declare_parameter('init_y', 0.0)
        self.declare_parameter('init_yaw', 0.0)
        self.declare_parameter('publish_clock', False)

        self._L = self.get_parameter(
            'wheelbase').get_parameter_value().double_value
        update_hz = self.get_parameter(
            'update_hz').get_parameter_value().double_value
        self._max_steer = self.get_parameter(
            'max_steer').get_parameter_value().double_value
        self._max_speed = self.get_parameter(
            'max_speed').get_parameter_value().double_value
        self._frame_id = self.get_parameter(
            'frame_id').get_parameter_value().string_value
        self._child_frame_id = self.get_parameter(
            'child_frame_id').get_parameter_value().string_value

        # --- State ---
        self._x = self.get_parameter(
            'init_x').get_parameter_value().double_value
        self._y = self.get_parameter(
            'init_y').get_parameter_value().double_value
        self._yaw = self.get_parameter(
            'init_yaw').get_parameter_value().double_value
        self._v = 0.0
        self._steer = 0.0
        self._vy = 0.0
        self._yaw_rate = 0.0

        # --- Command subscriber ---
        self.create_subscription(
            AckermannDriveStamped, '/lhr/vehicle/cmd',
            self._cmd_cb, 10)

        # --- Clock ---
        # This node is the plant, so it is also the clock. Its step is a
        # fixed dt already, so publishing that as /clock makes every
        # other node advance in exact increments instead of drifting
        # with host load. It must keep its own wall-clock timer and
        # use_sim_time false: a clock source waiting on its own clock
        # never ticks.
        self._publish_clock = bool(
            self.get_parameter('publish_clock').value)
        # Start away from zero; a zero stamp reads as 'unset' downstream.
        self._sim_ns = 1_000_000_000
        self._clock_pub = (
            self.create_publisher(Clock, '/clock', 10)
            if self._publish_clock else None)

        # --- Odom publisher ---
        self._odom_pub = self.create_publisher(
            Odometry, '/lhr/vehicle/odom', 10)

        # --- TF broadcaster ---
        self._tf_bc = TransformBroadcaster(self)

        # --- Timer ---
        self._dt = 1.0 / update_hz
        self._step_ns = int(round(self._dt * 1e9))
        self.create_timer(self._dt, self._step)
        self.get_logger().info(
            f'Simulator interface ready  (L={self._L}, hz={update_hz}, '
            f'clock={"sim" if self._publish_clock else "wall"})')

    # ------------------------------------------------------------------
    def _cmd_cb(self, msg: AckermannDriveStamped):
        self._v = max(-self._max_speed,
                      min(self._max_speed, msg.drive.speed))
        self._steer = max(-self._max_steer,
                          min(self._max_steer,
                              msg.drive.steering_angle))

    # ------------------------------------------------------------------
    def _advance(self):
        """Advance the plant state by one fixed timestep."""
        # Kinematic bicycle model
        dt = self._dt
        self._x += self._v * math.cos(self._yaw) * dt
        self._y += self._v * math.sin(self._yaw) * dt
        self._yaw_rate = (self._v / self._L) * math.tan(self._steer)
        self._yaw += self._yaw_rate * dt

    def _step(self):
        """Advance the plant and publish a consistent clock, odometry and TF."""
        self._advance()

        if self._publish_clock:
            self._sim_ns += self._step_ns
            now = Time(nanoseconds=self._sim_ns).to_msg()
            self._clock_pub.publish(Clock(clock=now))
        else:
            now = self.get_clock().now().to_msg()

        # --- Odometry ---
        odom = Odometry()
        odom.header.stamp = now
        odom.header.frame_id = self._frame_id
        odom.child_frame_id = self._child_frame_id

        odom.pose.pose.position.x = self._x
        odom.pose.pose.position.y = self._y
        odom.pose.pose.orientation = yaw_to_quat(self._yaw)

        # Odometry twist belongs to child_frame_id, not the map frame.
        odom.twist.twist.linear = Vector3(x=self._v, y=self._vy, z=0.0)
        odom.twist.twist.angular = Vector3(
            x=0.0, y=0.0,
            z=self._yaw_rate)

        self._odom_pub.publish(odom)

        # --- TF: map → base_link ---
        t = TransformStamped()
        t.header.stamp = now
        t.header.frame_id = self._frame_id
        t.child_frame_id = self._child_frame_id
        t.transform.translation.x = self._x
        t.transform.translation.y = self._y
        t.transform.rotation = yaw_to_quat(self._yaw)
        self._tf_bc.sendTransform(t)


def main():
    """Entry point."""
    rclpy.init()
    node = SimKinematic()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()
