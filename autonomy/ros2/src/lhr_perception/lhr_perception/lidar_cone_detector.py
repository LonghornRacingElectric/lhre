#!/usr/bin/env python3
"""
LiDAR-based cone detector for FSAE driverless.

Subscribes to a PointCloud2 topic from the Mid-360 simulator or Gazebo,
clusters the pointcloud to find cone-sized objects, and publishes
a MarkerArray of detected cones with optional geometry-based side inference.

Output contract (consumed by lhr_track_builder):
  - Topic: /lhr/sensor/cones_detected (MarkerArray)
  - QoS: RELIABLE + TRANSIENT_LOCAL, depth 1
  - Namespace: "cones" (ids 0..N-1)
  - Markers: SPHERE type, scale 0.35, frame_id "map"

Optional side inference estimates track sides from geometry, not colour.
"""

from collections import deque
import math

from geometry_msgs.msg import TransformStamped
from lhr_perception.cone_map import update_cone_map
from lhr_perception.cone_side_classifier import (
    classify_cone_sides,
    LEFT,
    RIGHT,
    StableSideLabels,
    UNKNOWN,
    vehicle_relative_side_vote,
)
from lhr_perception.pose_history import Pose2D
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


def _quat_to_yaw(q) -> float:
    """Extract yaw from a quaternion (assumes near-zero roll/pitch)."""
    siny = 2.0 * (q.w * q.z + q.x * q.y)
    cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny, cosy)


class LidarConeDetector(Node):
    """Detect cone clusters with explicit mount and height-frame conventions."""

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
        self.declare_parameter('sensor_transform_source', 'tf')
        self.declare_parameter('height_filter_frame', 'base_link')
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

        self.declare_parameter('classify_sides', True)
        self.declare_parameter('side_vote_max_range', 7.0)
        self.declare_parameter('side_vote_max_forward', 3.0)
        self.declare_parameter('side_vote_max_lateral', 5.0)
        self.declare_parameter('side_vote_deadband', 0.5)
        self.declare_parameter('side_update_distance', 1.0)
        self.declare_parameter('side_confirmations', 2)
        self.declare_parameter('boundary_link_distance', 3.0)
        self.declare_parameter('boundary_gap_distance', 3.3)
        self.declare_parameter('boundary_gap_angle_deg', 40.0)

        self._sensor_transform_source = self.get_parameter('sensor_transform_source').value
        self._height_filter_frame = self.get_parameter('height_filter_frame').value
        if self._sensor_transform_source not in ('tf', 'vehicle_config'):
            raise ValueError('sensor_transform_source must be tf or vehicle_config')
        if self._height_filter_frame not in ('base_link', 'sensor'):
            raise ValueError('height_filter_frame must be base_link or sensor')
        self._configured_mount = TransformStamped()
        position = self._configured_mount.transform.translation
        position.x, position.y, position.z = veh.lidar_position_m
        self._configured_mount.transform.rotation.w = 1.0
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
        self._classify_sides = self.get_parameter('classify_sides').value
        self._side_vote_max_range = self.get_parameter(
            'side_vote_max_range').value
        self._side_vote_max_forward = self.get_parameter(
            'side_vote_max_forward').value
        self._side_vote_max_lateral = self.get_parameter(
            'side_vote_max_lateral').value
        self._side_vote_deadband = self.get_parameter(
            'side_vote_deadband').value
        self._side_update_distance = self.get_parameter(
            'side_update_distance').value
        side_confirmations = self.get_parameter('side_confirmations').value
        self._boundary_link_distance = self.get_parameter(
            'boundary_link_distance').value
        self._boundary_gap_distance = self.get_parameter(
            'boundary_gap_distance').value
        self._boundary_gap_angle = math.radians(self.get_parameter(
            'boundary_gap_angle_deg').value)
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

        self._side_votes: list[float] = []
        self._cone_sides: list[int] = []
        self._side_confirmations = side_confirmations
        self._stable_sides = StableSideLabels(side_confirmations)
        self._last_side_pose: Pose2D | None = None
        self._seeded_sides = False
        self._published_namespaces: list[str] = []

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
            if self._sensor_transform_source == 'vehicle_config':
                sensor_to_base = self._configured_mount
            else:
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

        # Gazebo retains its sensor-frame band; pitched Mid-360 scans use
        # ground-level base_link so changing the mount cannot move the cutoff.
        if self._height_filter_frame == 'sensor':
            z = points[:, 2]
            points = points[(z > self._ground_z_min) & (z < self._ground_z_max)]
        points = transform_points(points, sensor_to_base.transform)
        if self._height_filter_frame == 'base_link':
            z = points[:, 2]
            points = points[(z > self._ground_z_min) & (z < self._ground_z_max)]

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
        detections = []
        for cluster in clusters:
            if self._is_cone(cluster[:, :2]):
                centroid = cluster.mean(axis=0)
                detections.append((float(centroid[0]), float(centroid[1])))
        new_cones = update_cone_map(detections, self._cone_map, self._dedup_radius)
        transform = base_to_map.transform
        self._update_side_classification(Pose2D(
            stamp.nanoseconds, transform.translation.x, transform.translation.y,
            _quat_to_yaw(transform.rotation)))

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
            self._side_votes.clear()
            self._cone_sides.clear()
            self._stable_sides = StableSideLabels(self._side_confirmations)
            self._last_side_pose = None
            self._seeded_sides = False
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
    def _update_side_classification(self, pose: Pose2D):
        """Accumulate local side evidence and enforce boundary continuity."""
        if not self._classify_sides:
            self._cone_sides = [UNKNOWN] * len(self._cone_map)
            return

        while len(self._side_votes) < len(self._cone_map):
            self._side_votes.append(0.0)

        self._cone_sides = self._stable_sides.labels(len(self._cone_map))
        if self._last_side_pose is not None:
            travelled = math.hypot(
                pose.x - self._last_side_pose.x,
                pose.y - self._last_side_pose.y,
            )
            if travelled < self._side_update_distance:
                return

        for index, entry in enumerate(self._cone_map):
            self._side_votes[index] += vehicle_relative_side_vote(
                entry[0],
                entry[1],
                pose,
                min_distance=self._min_range,
                max_distance=self._side_vote_max_range,
                max_forward=self._side_vote_max_forward,
                max_lateral=self._side_vote_max_lateral,
                lateral_deadband=self._side_vote_deadband,
            )

        proposed = classify_cone_sides(
            self._cone_map,
            self._side_votes,
            link_distance=self._boundary_link_distance,
            gap_distance=self._boundary_gap_distance,
            max_gap_angle=self._boundary_gap_angle,
        )
        self._cone_sides = self._stable_sides.update(
            proposed, seed=not self._seeded_sides)
        self._seeded_sides = True
        self._last_side_pose = pose

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------
    def _publish_accumulated(self):
        """Publish accumulated cones as MarkerArray."""
        if not self._cone_map:
            return

        now = self.get_clock().now().to_msg()
        msg = MarkerArray()

        for i, (entry, side) in enumerate(zip(
                self._cone_map, self._cone_sides)):
            namespace, color = self._side_marker_style(side)
            if (i < len(self._published_namespaces)
                    and self._published_namespaces[i] != namespace):
                msg.markers.append(self._make_delete_marker(
                    i, self._published_namespaces[i], now))
            msg.markers.append(self._make_marker(
                i, namespace, entry[0], entry[1], now, color))

        self._published_namespaces = [
            self._side_marker_style(side)[0] for side in self._cone_sides]
        self._det_pub.publish(msg)

    def _publish_debug(self):
        """Publish debug visualization of accumulated cones."""
        if not self._cone_map:
            return

        now = self.get_clock().now().to_msg()
        msg = MarkerArray()

        for i, (entry, side) in enumerate(zip(
                self._cone_map, self._cone_sides)):
            _, color = self._side_marker_style(side)
            color.a = 0.5
            m = self._make_marker(
                i, 'debug_cones', entry[0], entry[1], now,
                color)
            m.scale.x = 0.25
            m.scale.y = 0.25
            m.scale.z = 0.25
            msg.markers.append(m)

        self._debug_pub.publish(msg)

    @staticmethod
    def _side_marker_style(side: int) -> tuple[str, ColorRGBA]:
        """Return the output namespace and RViz colour for one side."""
        if side == LEFT:
            return 'left_cones', ColorRGBA(r=0.0, g=0.3, b=1.0, a=1.0)
        if side == RIGHT:
            return 'right_cones', ColorRGBA(r=1.0, g=1.0, b=0.0, a=1.0)
        return 'cones', ColorRGBA(r=1.0, g=0.5, b=0.0, a=1.0)

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

    @staticmethod
    def _make_delete_marker(mid: int, ns: str, stamp) -> Marker:
        """Remove the previous namespace when a cone changes classification."""
        marker = Marker()
        marker.header.frame_id = 'map'
        marker.header.stamp = stamp
        marker.ns = ns
        marker.id = mid
        marker.action = Marker.DELETE
        return marker


def main(args=None):
    rclpy.init(args=args)
    node = LidarConeDetector()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
