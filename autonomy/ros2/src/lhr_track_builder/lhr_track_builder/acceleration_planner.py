"""Interpolate detected acceleration boundaries and publish their mean path."""

import math

from geometry_msgs.msg import PoseStamped
from lhr_vehicle import load_vehicle
from nav_msgs.msg import Odometry, Path
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool, Float32, String
from visualization_msgs.msg import Marker, MarkerArray


def _mean_boundary_path(left, right, start, end, spacing):
    """Interpolate independently sampled rows at common longitudinal positions."""
    rows = []
    for points in (left, right):
        points = np.asarray(points, dtype=float).reshape(-1, 2)
        points = points[np.isfinite(points).all(axis=1)]
        if len(points) < 2:
            return []
        positions, inverse = np.unique(points[:, 0], return_inverse=True)
        if len(positions) < 2 or positions[-1] - positions[0] < 2.0:
            return []
        lateral = np.bincount(inverse, weights=points[:, 1]) / np.bincount(inverse)
        rows.append((positions, lateral))

    # Bound extrapolation to 3 m beyond the observations on either side.
    start = max(start, max(row[0][0] for row in rows) - 3.0)
    end = min(end, min(row[0][-1] for row in rows) + 3.0)
    if end <= start:
        return []
    samples = np.linspace(start, end, max(2, math.ceil((end - start) / spacing) + 1))
    boundaries = []
    for positions, lateral in rows:
        values = np.interp(samples, positions, lateral)
        for indices in ((0, 1), (-1, -2)):
            i, j = indices
            mask = samples < positions[0] if i == 0 else samples > positions[-1]
            slope = (lateral[j] - lateral[i]) / (positions[j] - positions[i])
            values[mask] = lateral[i] + slope * (samples[mask] - positions[i])
        boundaries.append(values)
    widths = boundaries[0] - boundaries[1]
    if np.any(widths < 4.0) or np.any(widths > 6.5):
        return []
    return list(zip(samples.tolist(), ((boundaries[0] + boundaries[1]) / 2).tolist()))


class AccelerationPlanner(Node):
    """Plan an open path from detected cones, with a fixed staged mission frame."""

    def __init__(self):
        super().__init__('acceleration_planner')
        self.declare_parameter('acceleration_path_spacing_m', 0.5)
        self.declare_parameter('acceleration_stop_distance_m', 20.0)
        self.declare_parameter('input_timeout_sec', 1.0)
        self._spacing = float(self.get_parameter('acceleration_path_spacing_m').value)
        self._stop_distance = float(self.get_parameter('acceleration_stop_distance_m').value)
        self._timeout = float(self.get_parameter('input_timeout_sec').value)
        if not (math.isfinite(self._spacing) and self._spacing > 0
                and math.isfinite(self._stop_distance) and 1 <= self._stop_distance < 75
                and math.isfinite(self._timeout) and self._timeout > 0):
            raise ValueError('Spacing/timeout must be positive; stop distance must be in [1,75)')
        vehicle = load_vehicle()
        # base_link is the rear axle; staging places the collision-body nose 0.30 m back.
        self._nose = vehicle.chassis_center_x_m + vehicle.body_length_m / 2
        self._finish_s = 75.0 + 0.30 + self._nose
        self._stop_s = self._finish_s + self._stop_distance - self._nose
        self._origin = None
        self._odom = None
        self._cones = None
        self._odom_time = self._cone_time = None
        self._status = ''
        self._speed = 0.0
        self._stopped_since = None
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(MarkerArray, '/lhr/sensor/cones_detected', self._cones_cb, qos)
        self.create_subscription(Odometry, '/lhr/vehicle/odom', self._odom_cb, 10)
        self.create_subscription(String, '/lhr/mission/status', self._status_cb, 10)
        self._path_pub = self.create_publisher(Path, '/lhr/track/centerline', 10)
        self._remaining_pub = self.create_publisher(Float32, '/lhr/acceleration/remaining', 10)
        self._complete_pub = self.create_publisher(Bool, '/lhr/acceleration/complete', 10)
        self.create_timer(0.1, self._plan)

    def _cones_cb(self, msg):
        self._cones = msg
        self._cone_time = self.get_clock().now()

    def _odom_cb(self, msg):
        if self._odom is not None:
            previous = self._odom
            dt = ((msg.header.stamp.sec - previous.header.stamp.sec)
                  + (msg.header.stamp.nanosec - previous.header.stamp.nanosec) / 1e9)
            if dt > 0:
                p, old = msg.pose.pose.position, previous.pose.pose.position
                # Gazebo's pose publisher leaves twist zero even while moving.
                self._speed = math.hypot(p.x - old.x, p.y - old.y) / dt
        self._odom = msg
        self._odom_time = self.get_clock().now()
        if self._origin is None:
            p, q = msg.pose.pose.position, msg.pose.pose.orientation
            yaw = math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y**2 + q.z**2))
            self._origin = (p.x, p.y, yaw)

    def _status_cb(self, msg):
        self._status = msg.data

    def _plan(self):
        now = self.get_clock().now()
        path = Path()
        path.header.stamp = now.to_msg()
        if self._odom is None:
            self._path_pub.publish(path)
            return
        path.header.frame_id = self._odom.header.frame_id
        if self._cones is None or any(
                (now - stamp).nanoseconds / 1e9 > self._timeout
                for stamp in (self._odom_time, self._cone_time)):
            self._path_pub.publish(path)
            return
        ox, oy, yaw = self._origin
        c, s = math.cos(yaw), math.sin(yaw)
        position = self._odom.pose.pose.position
        progress = c * (position.x - ox) + s * (position.y - oy)
        left, right = [], []
        for marker in self._cones.markers:
            if marker.action != Marker.ADD or marker.header.frame_id != path.header.frame_id:
                continue
            p = marker.pose.position
            forward = c * (p.x - ox) + s * (p.y - oy)
            lateral = -s * (p.x - ox) + c * (p.y - oy)
            # Geometry handles unknown LiDAR colors and orange exit cones alike.
            # Ignore unrelated nearby cones outside the staged straight corridor.
            if abs(abs(lateral) - 2.564) <= 0.8:
                (left if lateral > 0 else right).append((forward, lateral))
        samples = _mean_boundary_path(left, right, progress - 1, self._stop_s, self._spacing)
        for forward, lateral in samples:
            pose = PoseStamped()
            pose.header = path.header
            pose.pose.position.x = ox + c * forward - s * lateral
            pose.pose.position.y = oy + s * forward + c * lateral
            pose.pose.orientation.z = math.sin(yaw / 2)
            pose.pose.orientation.w = math.cos(yaw / 2)
            path.poses.append(pose)
        self._path_pub.publish(path)
        remaining = self._stop_s - progress
        self._remaining_pub.publish(Float32(data=max(0.0, remaining)))
        stopped = (self._status == 'DRIVING' and bool(samples)
                   and progress + self._nose >= self._finish_s
                   and -1.0 <= remaining <= 0.5 and self._speed < 0.1)
        if not stopped:
            self._stopped_since = None
        elif self._stopped_since is None:
            self._stopped_since = now
        completed = (self._stopped_since is not None
                     and (now - self._stopped_since).nanoseconds / 1e9 >= 0.5)
        self._complete_pub.publish(Bool(data=completed))


def main():
    """Run the acceleration planner."""
    rclpy.init()
    node = AccelerationPlanner()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()
