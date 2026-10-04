#!/usr/bin/env python3
"""Publish a synthetic Livox Mid-360 cloud on /lhr/lidar/points."""

import math

from lhr_lidar_sim.mid360 import Mid360Config
from lhr_lidar_sim.sensor import Mid360Sensor, MountPose
from lhr_vehicle import load_vehicle
from nav_msgs.msg import Odometry
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import PointCloud2
from sensor_msgs_py import point_cloud2
from std_msgs.msg import Header
from visualization_msgs.msg import MarkerArray

LIDAR_FRAME = 'lidar'


def quat_to_yaw(q) -> float:
    """Return yaw from a quaternion, ignoring roll and pitch."""
    siny = 2.0 * (q.w * q.z + q.x * q.y)
    cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny, cosy)


class LidarSimNode(Node):
    """Casts the Mid-360 model against the ground-truth cone set."""

    def __init__(self):
        super().__init__('lidar_sim')

        # Mount *position* comes from vehicle.yaml, like every other
        # vehicle number. Mount *orientation* does not: it is what the
        # mount study has to determine, so it stays a parameter until
        # there is a measured answer to write down.
        vehicle = load_vehicle()
        # lhr_vehicle's Vec3 is a plain (x, y, z) tuple, not an object.
        mount_x, mount_y, mount_z = vehicle.lidar_position_m

        self.declare_parameter('mount_roll_rad', 0.0)
        self.declare_parameter('mount_pitch_rad', 0.0)
        self.declare_parameter('mount_yaw_rad', 0.0)
        self.declare_parameter('seed', 1)
        self.declare_parameter('frame_rate_hz', 10.0)
        self.declare_parameter('point_rate_hz', 200_000.0)
        self.declare_parameter('max_range_m', 40.0)
        self.declare_parameter('range_noise_std_m', 0.02)
        self.declare_parameter('dropout_rate', 0.0)
        self.declare_parameter('elevation_profile', 'rosette')

        def param(name):
            return self.get_parameter(name).value

        frame_rate = float(param('frame_rate_hz'))
        config = Mid360Config(
            point_rate_hz=float(param('point_rate_hz')),
            frame_rate_hz=frame_rate,
            max_range_m=float(param('max_range_m')),
            range_noise_std_m=float(param('range_noise_std_m')),
            dropout_rate=float(param('dropout_rate')),
            elevation_profile=str(param('elevation_profile')),
        )
        mount = MountPose(
            x_m=mount_x, y_m=mount_y, z_m=mount_z,
            roll_rad=float(param('mount_roll_rad')),
            pitch_rad=float(param('mount_pitch_rad')),
            yaw_rad=float(param('mount_yaw_rad')),
        )
        self._sensor = Mid360Sensor(
            config=config, mount=mount, seed=int(param('seed')))

        self._cones = np.empty((0, 2))
        self._veh = (0.0, 0.0, 0.0)
        self._have_odom = False

        latch_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self.create_subscription(
            MarkerArray, '/lhr/track/cones', self._cones_cb, latch_qos)
        self.create_subscription(
            Odometry, '/lhr/vehicle/odom', self._odom_cb, 10)
        self._cloud_pub = self.create_publisher(
            PointCloud2, '/lhr/lidar/points', 10)

        self.create_timer(1.0 / frame_rate, self._tick)

        self.get_logger().info(
            f'Mid-360 sim ready: {config.points_per_frame()} beams at '
            f'{frame_rate:.0f} Hz, pitch '
            f'{math.degrees(mount.pitch_rad):.1f} deg, '
            f'profile {config.elevation_profile}')

    def _cones_cb(self, msg: MarkerArray):
        self._cones = np.array(
            [[m.pose.position.x, m.pose.position.y] for m in msg.markers],
            dtype=np.float64).reshape(-1, 2)

    def _odom_cb(self, msg: Odometry):
        self._veh = (msg.pose.pose.position.x,
                     msg.pose.pose.position.y,
                     quat_to_yaw(msg.pose.pose.orientation))
        self._have_odom = True

    def _tick(self):
        if not self._have_odom:
            return

        # Scan time comes from the ROS clock so the pattern advances
        # with sim time, which is what keeps the non-repetition
        # reproducible under a seeded run.
        now = self.get_clock().now()
        t = now.nanoseconds * 1e-9

        x, y, yaw = self._veh
        points = self._sensor.frame(t, x, y, yaw, self._cones)

        header = Header()
        header.stamp = now.to_msg()
        header.frame_id = LIDAR_FRAME
        self._cloud_pub.publish(
            point_cloud2.create_cloud_xyz32(header, points))


def main():
    """Entry point."""
    rclpy.init()
    node = LidarSimNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.try_shutdown()


if __name__ == '__main__':
    main()
