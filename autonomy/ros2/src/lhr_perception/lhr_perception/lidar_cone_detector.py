#!/usr/bin/env python3
"""
LiDAR-based cone detector for FSAE driverless.

Subscribes to a PointCloud2 topic from Gazebo's gpu_lidar sensor,
clusters the pointcloud to find cone-sized objects, and publishes
a persistent MarkerArray with geometry-inferred track sides.

Output contract (consumed by lhr_track_builder):
  - Topic: /lhr/sensor/cones_detected (MarkerArray)
  - QoS: RELIABLE + TRANSIENT_LOCAL, depth 1
  - Namespace: "left_cones", "right_cones", or "cones" while unknown or
    when side classification is disabled
  - Markers: SPHERE type, scale 0.35, frame_id "map"

LiDAR does not observe cone colour. Side classification accumulates the
cone's vehicle-relative lateral position, then shares evidence along
geometrically continuous boundary fragments.
"""

from collections import deque
import math

from lhr_perception.cone_map import update_cone_map
from lhr_perception.cone_side_classifier import (
    classify_cone_sides,
    LEFT,
    RIGHT,
    StableSideLabels,
    UNKNOWN,
    vehicle_relative_side_vote,
)
from lhr_perception.pose_history import Pose2D, PoseHistory, sensor_point_to_map
from lhr_vehicle import load_vehicle
from nav_msgs.msg import Odometry
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from scipy.spatial import cKDTree
from sensor_msgs.msg import PointCloud2
from sensor_msgs_py import point_cloud2
from std_msgs.msg import ColorRGBA
from visualization_msgs.msg import Marker, MarkerArray


# Height above the ground plane below which returns are treated as ground.
GROUND_CLEARANCE_M = 0.15


def _quat_to_yaw(q) -> float:
    """Extract yaw from a quaternion (assumes near-zero roll/pitch)."""
    siny = 2.0 * (q.w * q.z + q.x * q.y)
    cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny, cosy)


class LidarConeDetector(Node):

    def __init__(self):
        super().__init__('lidar_cone_detector')

        # LiDAR mount in base_link (rear-axle center), and the box around
        # the car's own body to drop from every scan, in the sensor frame.
        veh = load_vehicle()
        self._sensor_x_offset, self._sensor_y_offset, sensor_z = veh.lidar_position_m
        body_front_x = veh.chassis_center_x_m + veh.body_length_m / 2.0
        self._car_x_min = -(self._sensor_x_offset + veh.wheel_radius_m + 0.3)
        self._car_x_max = body_front_x - self._sensor_x_offset + 0.25
        self._car_half_width = (veh.half_track_m + veh.wheel_width_m / 2.0
                                + 0.12)

        # --- Parameters ---
        self.declare_parameter('max_range', 20.0)
        self.declare_parameter('min_range', 0.9)
        # Ground returns sit at z = -mount height in the sensor frame; keep
        # the band just above them so lowering the mast in vehicle.yaml
        # cannot let the ground plane through as phantom cones.
        self.declare_parameter('ground_z_min', -(sensor_z - GROUND_CLEARANCE_M))
        self.declare_parameter('ground_z_max', 0.5)
        self.declare_parameter('cluster_radius', 0.35)
        self.declare_parameter('min_cluster_points', 1)
        self.declare_parameter('max_cluster_extent', 0.5)
        self.declare_parameter('max_cluster_points', 50)
        self.declare_parameter('dedup_radius', 1.5)
        self.declare_parameter('publish_hz', 10.0)
        self.declare_parameter('pose_history_sec', 2.0)
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

        self._max_range = self.get_parameter('max_range').value
        self._min_range = self.get_parameter('min_range').value
        self._ground_z_min = self.get_parameter('ground_z_min').value
        self._ground_z_max = self.get_parameter('ground_z_max').value
        self._cluster_radius = self.get_parameter('cluster_radius').value
        self._min_cluster_pts = self.get_parameter('min_cluster_points').value
        self._max_cluster_extent = self.get_parameter('max_cluster_extent').value
        self._max_cluster_pts = self.get_parameter('max_cluster_points').value
        self._dedup_radius = self.get_parameter('dedup_radius').value
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

        # --- State ---
        self._pose_history = PoseHistory(
            self.get_parameter('pose_history_sec').value)
        # A short queue prevents a scan from being overwritten while it waits
        # for the odometry sample immediately after its timestamp.
        self._cloud_queue: deque[PointCloud2] = deque(maxlen=3)
        self._waiting_for_pose = False
        self._processed_synced_scan = False
        self._reported_startup_drop = False

        # Accumulated cone positions in map frame: [x, y, observation_count].
        self._cone_map: list[list[float]] = []
        self._side_votes: list[float] = []
        self._cone_sides: list[int] = []
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
            PointCloud2, '/lhr/lidar/points', self._cloud_cb, 10)
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
        self._cloud_queue.append(msg)

    def _odom_cb(self, msg: Odometry):
        self._pose_history.add(Pose2D(
            stamp_ns=self._stamp_ns(msg.header.stamp),
            x=msg.pose.pose.position.x,
            y=msg.pose.pose.position.y,
            yaw=_quat_to_yaw(msg.pose.pose.orientation),
        ))

    # ------------------------------------------------------------------
    # Main processing loop
    # ------------------------------------------------------------------
    def _process(self):
        if not self._pose_history or not self._cloud_queue:
            return

        cloud = self._cloud_queue[0]
        cloud_stamp_ns = self._stamp_ns(cloud.header.stamp)
        pose = self._pose_history.lookup(cloud_stamp_ns)
        if pose is None:
            oldest = self._pose_history.oldest_stamp_ns
            if oldest is not None and cloud_stamp_ns < oldest:
                self._cloud_queue.popleft()
                if not self._processed_synced_scan:
                    if not self._reported_startup_drop:
                        self.get_logger().info(
                            'discarding startup LiDAR scan captured before '
                            'odometry began')
                        self._reported_startup_drop = True
                else:
                    self.get_logger().warn(
                        'dropping LiDAR scan older than retained odometry history')
                return
            if not self._waiting_for_pose:
                self.get_logger().info(
                    'waiting for timestamp-aligned odometry for LiDAR scan')
                self._waiting_for_pose = True
            return

        self._waiting_for_pose = False
        self._processed_synced_scan = True
        self._cloud_queue.popleft()

        # Step 1: Deserialize to numpy
        points = point_cloud2.read_points_numpy(
            cloud, field_names=('x', 'y', 'z'))
        if len(points) == 0:
            return

        # Step 2: Ground removal (height filter in sensor frame)
        z = points[:, 2]
        height_mask = (z > self._ground_z_min) & (z < self._ground_z_max)
        points = points[height_mask]
        if len(points) == 0:
            return

        # Step 3: Vehicle exclusion zone (filter out the car's own body)
        car_mask = ~(
            (points[:, 0] > self._car_x_min) &
            (points[:, 0] < self._car_x_max) &
            (points[:, 1] > -self._car_half_width) &
            (points[:, 1] < self._car_half_width)
        )
        points = points[car_mask]
        if len(points) == 0:
            return

        # Step 4: Range filter (2D distance from sensor)
        xy = points[:, :2]
        ranges = np.linalg.norm(xy, axis=1)
        range_mask = (ranges > self._min_range) & (ranges < self._max_range)
        points = points[range_mask]
        xy = points[:, :2]
        if len(xy) == 0:
            return

        # Step 5: Euclidean clustering
        clusters = self._cluster(xy)

        # Step 6–8: Validate, transform, dedup
        detections: list[tuple[float, float]] = []
        for cluster_xy in clusters:
            if not self._is_cone(cluster_xy):
                continue

            centroid = cluster_xy.mean(axis=0)
            sx, sy = float(centroid[0]), float(centroid[1])

            # Transform to map frame
            mx, my = self._sensor_to_map(sx, sy, pose)
            detections.append((mx, my))

        new_cones = update_cone_map(
            detections, self._cone_map, self._dedup_radius)

        if new_cones > 0:
            self.get_logger().info(
                f'+{new_cones} cones  (total: {len(self._cone_map)})')

        self._update_side_classification(pose)
        self._publish_accumulated()
        self._publish_debug()

    # ------------------------------------------------------------------
    # Clustering
    # ------------------------------------------------------------------
    def _cluster(self, xy: np.ndarray) -> list[np.ndarray]:
        """Cluster 2D points using cKDTree radius queries."""
        if len(xy) < self._min_cluster_pts:
            return []

        tree = cKDTree(xy)
        visited = np.zeros(len(xy), dtype=bool)
        clusters: list[np.ndarray] = []

        for i in range(len(xy)):
            if visited[i]:
                continue
            indices = tree.query_ball_point(xy[i], self._cluster_radius)
            if len(indices) < self._min_cluster_pts:
                visited[i] = True
                continue
            visited[indices] = True
            clusters.append(xy[indices])

        return clusters

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
    # Coordinate transforms
    # ------------------------------------------------------------------
    @staticmethod
    def _stamp_ns(stamp) -> int:
        """Convert a ROS time message to integer nanoseconds."""
        return stamp.sec * 1_000_000_000 + stamp.nanosec

    def _sensor_to_map(self, sx: float, sy: float,
                       pose: Pose2D) -> tuple[float, float]:
        """Transform a point from sensor frame to map frame."""
        return sensor_point_to_map(
            sx, sy, self._sensor_x_offset, self._sensor_y_offset, pose)

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
        rclpy.shutdown()


if __name__ == '__main__':
    main()
