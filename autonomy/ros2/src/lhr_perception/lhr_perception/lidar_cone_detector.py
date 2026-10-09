#!/usr/bin/env python3
"""
LiDAR-based cone detector for FSAE driverless.

Subscribes to a PointCloud2 topic from the Mid-360 simulator or Gazebo,
clusters the pointcloud to find cone-sized objects, and publishes
an unclassified MarkerArray of all detected cones.

Output contract (consumed by lhr_track_builder):
  - Topic: /lhr/sensor/cones_detected (MarkerArray)
  - QoS: RELIABLE + TRANSIENT_LOCAL, depth 1
  - Namespace: "cones" (ids 0..N-1)
  - Markers: SPHERE type, scale 0.35, frame_id "map"

Left/right classification is NOT performed here — the LiDAR has no
colour information.  The track_builder's 'boundary' pairing strategy
handles centerline construction from unclassified cones.
"""

from collections import deque

from geometry_msgs.msg import TransformStamped
from lhr_vehicle import load_vehicle
from nav_msgs.msg import Odometry
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy, qos_profile_sensor_data, QoSProfile, ReliabilityPolicy)
from rclpy.time import Time
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
from sensor_msgs.msg import PointCloud2
from sensor_msgs_py import point_cloud2
from std_msgs.msg import ColorRGBA
from tf2_ros import Buffer, TransformException, TransformListener
from visualization_msgs.msg import Marker, MarkerArray


# Height above the ground plane below which returns are treated as ground.
GROUND_CLEARANCE_M = 0.05


def transform_points(points: np.ndarray, transform) -> np.ndarray:
    """Apply a full rigid transform to an Nx3 array."""
    q = transform.rotation
    quat = np.array([q.x, q.y, q.z, q.w], dtype=np.float64)
    norm = np.linalg.norm(quat)
    if not np.isfinite(norm) or norm == 0.0:
        raise ValueError('Transform quaternion must be finite and nonzero')
    x, y, z, w = quat / norm
    rotation = np.array([
        [1 - 2 * (y*y + z*z), 2 * (x*y - z*w), 2 * (x*z + y*w)],
        [2 * (x*y + z*w), 1 - 2 * (x*x + z*z), 2 * (y*z - x*w)],
        [2 * (x*z - y*w), 2 * (y*z + x*w), 1 - 2 * (x*x + y*y)],
    ])
    t = transform.translation
    return points @ rotation.T + np.array([t.x, t.y, t.z])


class LidarConeDetector(Node):
    """Detect cone clusters using scan-time transforms and base-frame heights."""

    def __init__(self):
        super().__init__('lidar_cone_detector')

        # Exclude the car in base_link, independent of sensor mount pose.
        veh = load_vehicle()
        body_front_x = veh.chassis_center_x_m + veh.body_length_m / 2.0
        self._car_x_min = -(veh.wheel_radius_m + 0.3)
        self._car_x_max = body_front_x + 0.25
        self._car_half_width = (veh.half_track_m + veh.wheel_width_m / 2.0
                                + 0.12)

        # --- Parameters ---
        self.declare_parameter('max_range', 20.0)
        self.declare_parameter('min_range', 0.9)
        # base_link is at ground level; sensor pitch cannot change this band.
        self.declare_parameter('ground_z_min', GROUND_CLEARANCE_M)
        self.declare_parameter('ground_z_max', 0.55)
        self.declare_parameter('stack_window_sec', 0.5)
        self.declare_parameter('stack_max_frames', 10)
        self.declare_parameter('transform_wait_sec', 0.5)
        self.declare_parameter('cluster_radius', 0.35)
        self.declare_parameter('min_cluster_points', 3)
        self.declare_parameter('max_cluster_extent', 0.5)
        self.declare_parameter('max_cluster_points', 500)
        self.declare_parameter('dedup_radius', 0.6)
        self.declare_parameter('publish_hz', 10.0)

        self._max_range = self.get_parameter('max_range').value
        self._min_range = self.get_parameter('min_range').value
        self._ground_z_min = self.get_parameter('ground_z_min').value
        self._ground_z_max = self.get_parameter('ground_z_max').value
        self._cluster_radius = self.get_parameter('cluster_radius').value
        self._min_cluster_pts = self.get_parameter('min_cluster_points').value
        self._max_cluster_extent = self.get_parameter('max_cluster_extent').value
        self._max_cluster_pts = self.get_parameter('max_cluster_points').value
        self._dedup_radius = self.get_parameter('dedup_radius').value
        self._dedup_radius_sq = self._dedup_radius ** 2
        publish_hz = self.get_parameter('publish_hz').value
        self._transform_wait_sec = self.get_parameter('transform_wait_sec').value

        self._stack_window = float(self.get_parameter('stack_window_sec').value)
        self._stack_max_frames = int(self.get_parameter('stack_max_frames').value)
        if self._stack_window < 0 or self._stack_max_frames < 1:
            raise ValueError('Stack window must be nonnegative and frame limit positive')
        self._scan_history = deque(maxlen=self._stack_max_frames)
        self._last_scan_ns = None

        # --- State ---
        self._latest_cloud: PointCloud2 | None = None
        self._cloud_received_at = None
        self._tf_buffer = Buffer(node=self)
        self._tf_listener = TransformListener(self._tf_buffer, self)

        # Accumulated cone positions in map frame: [x, y, observation_count].
        self._cone_map: list[list[float]] = []

        # --- QoS ---
        latch_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )

        # --- Subscribers ---
        self.create_subscription(
            PointCloud2, '/lhr/lidar/points', self._cloud_cb, qos_profile_sensor_data)
        self.create_subscription(
            Odometry, '/lhr/vehicle/odom', self._odom_cb, 10)

        # --- Publishers ---
        self._det_pub = self.create_publisher(
            MarkerArray, '/lhr/sensor/cones_detected', latch_qos)
        self._debug_pub = self.create_publisher(
            MarkerArray, '/lhr/perception/debug', 10)

        # --- Timer ---
        self.create_timer(1.0 / publish_hz, self._process)
        self.get_logger().info(
            f'LidarConeDetector ready  (range=[{self._min_range}, '
            f'{self._max_range}]m, cluster_r={self._cluster_radius}m)')

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------
    def _cloud_cb(self, msg: PointCloud2):
        self._latest_cloud = msg
        self._cloud_received_at = self.get_clock().now()

    def _odom_cb(self, msg: Odometry):
        # Buffer the stamped full pose, including roll/pitch. Gazebo's
        # odometry bridge need not also publish a duplicate map->base TF.
        transform = TransformStamped()
        transform.header = msg.header
        transform.child_frame_id = msg.child_frame_id
        transform.transform.translation.x = msg.pose.pose.position.x
        transform.transform.translation.y = msg.pose.pose.position.y
        transform.transform.translation.z = msg.pose.pose.position.z
        transform.transform.rotation = msg.pose.pose.orientation
        self._tf_buffer.set_transform(transform, 'vehicle_odometry')

    # ------------------------------------------------------------------
    # Main processing loop
    # ------------------------------------------------------------------
    def _process(self):
        if self._latest_cloud is None:
            return

        cloud = self._latest_cloud
        if not cloud.header.frame_id or (
                cloud.header.stamp.sec == 0 and cloud.header.stamp.nanosec == 0):
            self.get_logger().warning('Dropping cloud with missing frame or timestamp')
            self._latest_cloud = None
            return
        stamp = Time.from_msg(cloud.header.stamp)
        try:
            sensor_to_base = self._tf_buffer.lookup_transform(
                'base_link', cloud.header.frame_id, stamp)
            base_to_map = self._tf_buffer.lookup_transform('map', 'base_link', stamp)
        except TransformException as error:
            self.get_logger().warning(
                f'Waiting for scan-time TF: {error}', throttle_duration_sec=5.0)
            # TF can arrive after the cloud. Retry without blocking the executor,
            # but never fall back to a newer pose or keep an unusable scan forever.
            age = (self.get_clock().now() - self._cloud_received_at).nanoseconds * 1e-9
            if age > self._transform_wait_sec:
                self.get_logger().warning(f'Dropping cloud without scan-time TF: {error}')
                self._latest_cloud = None
            return
        self._latest_cloud = None

        points = point_cloud2.read_points_numpy(
            cloud, field_names=('x', 'y', 'z'), skip_nans=True).reshape(-1, 3)
        points = points[np.isfinite(points).all(axis=1)]

        # Height and body filters belong in the ground-level vehicle frame.
        points = transform_points(points, sensor_to_base.transform)
        z = points[:, 2]
        height_mask = (z > self._ground_z_min) & (z < self._ground_z_max)
        points = points[height_mask]

        # Step 3: Vehicle exclusion zone (filter out the car's own body)
        car_mask = ~(
            (points[:, 0] > self._car_x_min) &
            (points[:, 0] < self._car_x_max) &
            (points[:, 1] > -self._car_half_width) &
            (points[:, 1] < self._car_half_width)
        )
        points = points[car_mask]

        # Step 4: Range filter (2D distance from sensor)
        origin = sensor_to_base.transform.translation
        ranges = np.linalg.norm(points[:, :2] - [origin.x, origin.y], axis=1)
        range_mask = (ranges > self._min_range) & (ranges < self._max_range)
        points = points[range_mask]

        # Register each scan separately before stacking. A persistent cone map
        # alone cannot turn several isolated returns into a valid cluster.
        mapped = transform_points(points, base_to_map.transform)
        points = self._stack_scan(stamp.nanoseconds, mapped)
        clusters = self._cluster(points)

        # Step 6–8: Validate, transform, dedup
        new_cones = 0
        for cluster in clusters:
            if not self._is_cone(cluster[:, :2]):
                continue

            centroid = cluster.mean(axis=0)
            mx, my = float(centroid[0]), float(centroid[1])

            # Dedup / merge against accumulated map
            if self._try_merge(mx, my, self._cone_map):
                continue

            # New cone
            self._cone_map.append([mx, my, 1.0])
            new_cones += 1

        if new_cones > 0:
            self.get_logger().info(
                f'+{new_cones} cones  (total: {len(self._cone_map)})')

        self._publish_accumulated()
        self._publish_debug()

    # ------------------------------------------------------------------
    # Clustering
    # ------------------------------------------------------------------
    def _stack_scan(self, stamp_ns, points):
        """Keep a bounded scan-time window of already registered map points."""
        if self._last_scan_ns is not None and stamp_ns < self._last_scan_ns:
            # A replay restart must not mix runs or retain the previous map.
            self._scan_history.clear()
            self._cone_map.clear()
        if self._last_scan_ns == stamp_ns:
            # Republishing one scan must never satisfy the evidence threshold.
            self._scan_history.pop()
        self._last_scan_ns = stamp_ns
        self._scan_history.append((stamp_ns, points))
        oldest = stamp_ns - int(self._stack_window * 1e9)
        while len(self._scan_history) > 1 and self._scan_history[0][0] <= oldest:
            self._scan_history.popleft()
        return np.concatenate([scan for _, scan in self._scan_history], axis=0)

    def _cluster(self, points: np.ndarray) -> list[np.ndarray]:
        """Cluster 2D points using cKDTree radius queries."""
        xy = points[:, :2]
        if len(xy) < self._min_cluster_pts:
            return []

        # Real captures contain dense clutter. Find all neighbor pairs in C
        # and label the graph once rather than querying each point in Python.
        pairs = cKDTree(xy).query_pairs(self._cluster_radius, output_type='ndarray')
        graph = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])),
                           shape=(len(xy), len(xy)))
        _, labels = connected_components(graph, directed=False)
        order = np.argsort(labels, kind='stable')
        groups = np.split(order, np.flatnonzero(np.diff(labels[order])) + 1)
        return [points[group] for group in groups if len(group) >= self._min_cluster_pts]

    def _is_cone(self, cluster_xy: np.ndarray) -> bool:
        """Check if a cluster matches expected cone dimensions."""
        if len(cluster_xy) > self._max_cluster_pts:
            return False
        extent = cluster_xy.max(axis=0) - cluster_xy.min(axis=0)
        if extent[0] > self._max_cluster_extent:
            return False
        if extent[1] > self._max_cluster_extent:
            return False
        return True

    # ------------------------------------------------------------------
    # Deduplication with running average
    # ------------------------------------------------------------------
    def _try_merge(self, mx: float, my: float,
                   cone_map: list[list[float]]) -> bool:
        """
        Try to merge into the nearest existing cone in *cone_map*.

        Returns True if merged (position updated via running average).
        Returns False if no existing cone is within dedup radius.
        """
        for entry in cone_map:
            ex, ey = entry[0], entry[1]
            if (mx - ex) ** 2 + (my - ey) ** 2 < self._dedup_radius_sq:
                n = entry[2]
                entry[0] = (ex * n + mx) / (n + 1)
                entry[1] = (ey * n + my) / (n + 1)
                entry[2] = n + 1
                return True
        return False

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------
    def _publish_accumulated(self):
        """Publish accumulated cones as MarkerArray."""
        if not self._cone_map:
            return

        now = self.get_clock().now().to_msg()
        msg = MarkerArray()

        for i, entry in enumerate(self._cone_map):
            msg.markers.append(self._make_marker(
                i, 'cones', entry[0], entry[1], now,
                ColorRGBA(r=1.0, g=0.5, b=0.0, a=1.0)))

        self._det_pub.publish(msg)

    def _publish_debug(self):
        """Publish debug visualization of accumulated cones."""
        if not self._cone_map:
            return

        now = self.get_clock().now().to_msg()
        msg = MarkerArray()

        for i, entry in enumerate(self._cone_map):
            m = self._make_marker(
                i, 'debug_cones', entry[0], entry[1], now,
                ColorRGBA(r=1.0, g=0.5, b=0.0, a=0.5))
            m.scale.x = 0.25
            m.scale.y = 0.25
            m.scale.z = 0.25
            msg.markers.append(m)

        self._debug_pub.publish(msg)

    @staticmethod
    def _make_marker(mid: int, ns: str, x: float, y: float,
                     stamp, color: ColorRGBA) -> Marker:
        m = Marker()
        m.header.frame_id = 'map'
        m.header.stamp = stamp
        m.ns = ns
        m.id = mid
        m.type = Marker.SPHERE
        m.action = Marker.ADD
        m.pose.position.x = x
        m.pose.position.y = y
        m.pose.position.z = 0.0
        m.pose.orientation.w = 1.0
        m.scale.x = 0.35
        m.scale.y = 0.35
        m.scale.z = 0.35
        m.color = color
        return m


def main(args=None):
    rclpy.init(args=args)
    node = LidarConeDetector()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
