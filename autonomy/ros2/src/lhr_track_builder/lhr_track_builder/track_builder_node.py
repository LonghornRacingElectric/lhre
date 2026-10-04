#!/usr/bin/env python3
"""Track builder node: subscribes to cones, publishes centerline Path."""

import math
from typing import List, Protocol, Tuple

from builtin_interfaces.msg import Time
from geometry_msgs.msg import Point, PoseStamped
from nav_msgs.msg import Odometry, Path
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from scipy.spatial import Delaunay, QhullError
from std_msgs.msg import ColorRGBA, Header
from visualization_msgs.msg import Marker, MarkerArray


PathPoint = Tuple[float, float]
ConeColor = str | None
Edge = Tuple[int, int]


class PairingAlgorithm(Protocol):
    """Pair cones to produce candidate centerline points."""

    def __call__(self) -> List[PathPoint]:
        """Return candidate centerline points."""
        ...


class ChainingAlgorithm(Protocol):
    """Order points into a path beginning at a specified point."""

    def __call__(
        self, points: List[PathPoint], start: int,
    ) -> List[PathPoint]:
        """Return the points in path order."""
        ...


class TrackBuilder(Node):
    """Subscribe to cone markers, compute midpoints, publish centerline."""

    def __init__(self):
        """Configure pairing, chaining, and the ROS subscriptions and publishers."""
        super().__init__('track_builder')

        # --- Parameters ---
        self.declare_parameter('frame_id', 'map')
        self.declare_parameter('publish_hz', 5.0)
        self.declare_parameter('max_points', 200)
        self.declare_parameter('pairing_strategy', 'nearest')
        self.declare_parameter('cone_topic', '/lhr/sensor/cones_detected')
        self.declare_parameter('track_width', 3.5)
        self.declare_parameter('track_width_tolerance', 1.0)
        self.declare_parameter('chaining_alg', 'greedy')
        self.declare_parameter('min_step_m', 0.3)
        self.declare_parameter('max_step_m', 4.0)
        self.declare_parameter('max_turn_deg', 70.0)
        self.declare_parameter('distance_weight', 1.0)
        self.declare_parameter('heading_weight', 1.0)

        self._frame_id = self.get_parameter(
            'frame_id').get_parameter_value().string_value
        publish_hz = self.get_parameter(
            'publish_hz').get_parameter_value().double_value
        self._max_points = self.get_parameter(
            'max_points').get_parameter_value().integer_value
        self._pairing_strategy = self.get_parameter(
            'pairing_strategy').get_parameter_value().string_value
        cone_topic = self.get_parameter(
            'cone_topic').get_parameter_value().string_value
        self._track_width = self.get_parameter(
            'track_width').get_parameter_value().double_value
        self._track_width_tol = self.get_parameter(
            'track_width_tolerance').get_parameter_value().double_value
        self._chaining_alg = self.get_parameter(
            'chaining_alg').get_parameter_value().string_value
        self._min_step_m = self.get_parameter('min_step_m').value
        self._max_step_m = self.get_parameter('max_step_m').value
        max_turn_deg = self.get_parameter('max_turn_deg').value
        self._distance_weight = self.get_parameter('distance_weight').value
        self._heading_weight = self.get_parameter('heading_weight').value
        if not all(math.isfinite(value) for value in (
            self._min_step_m, self._max_step_m, max_turn_deg,
            self._distance_weight, self._heading_weight,
        )):
            raise ValueError('Chaining parameters must be finite')
        if not 0.0 <= self._min_step_m < self._max_step_m:
            raise ValueError('Require 0 <= min_step_m < max_step_m')
        if not 0.0 < max_turn_deg <= 180.0:
            raise ValueError('Require 0 < max_turn_deg <= 180')
        if (self._distance_weight < 0.0 or self._heading_weight < 0.0
                or max(self._distance_weight, self._heading_weight) == 0.0):
            raise ValueError(
                'Chaining weights must be nonnegative with at least one positive')
        self._max_turn_rad = math.radians(max_turn_deg)

        # --- Stored cone positions grouped by known color ---
        self._left_cones: dict = {}
        self._right_cones: dict = {}
        self._unknown_cones: dict = {}
        self._pairing_algorithm = self._get_pairing_algorithm()

        # --- Vehicle pose (used by nearest/boundary strategy) ---
        self._veh_x = 0.0
        self._veh_y = 0.0
        self._veh_yaw = 0.0
        self._have_odom = False

        # --- QoS ---
        # Subscriber must match cone publisher (TRANSIENT_LOCAL)
        sub_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        # Publishers use VOLATILE — we republish at publish_hz so
        # latching is unnecessary, and avoids DDS overhead on WSL2.
        pub_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
        )

        # --- Subscribers ---
        self.create_subscription(
            MarkerArray, cone_topic, self._cones_cb, sub_qos)
        self.create_subscription(
            Odometry, '/lhr/vehicle/odom', self._odom_cb, 10)

        # --- Publishers ---
        self._path_pub = self.create_publisher(
            Path, '/lhr/track/centerline', pub_qos)
        self._debug_pub = self.create_publisher(
            MarkerArray, '/lhr/track/centerline_markers', pub_qos)

        # --- Timer ---
        self.create_timer(1.0 / publish_hz, self._on_timer)
        self.get_logger().info(
            f'TrackBuilder ready  (strategy={self._pairing_strategy}, '
            f'hz={publish_hz}, max_points={self._max_points})')

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------
    def _cones_cb(self, msg: MarkerArray):
        """Extract known- and unknown-color cone positions, keyed by ID."""
        left: dict = {}
        right: dict = {}
        unknown: dict = {}
        for marker in msg.markers:
            pos = marker.pose.position
            position = (pos.x, pos.y)
            if marker.ns == 'left_cones':
                left[marker.id] = position
            elif marker.ns == 'right_cones':
                right[marker.id] = position
            elif marker.ns == 'cones':
                unknown[marker.id] = position
        self._left_cones = left
        self._right_cones = right
        self._unknown_cones = unknown

    def _odom_cb(self, msg: Odometry):
        self._veh_x = msg.pose.pose.position.x
        self._veh_y = msg.pose.pose.position.y
        q = msg.pose.pose.orientation
        self._veh_yaw = math.atan2(
            2.0 * (q.w * q.z + q.x * q.y),
            1.0 - 2.0 * (q.y * q.y + q.z * q.z))
        self._have_odom = True

    def _on_timer(self):
        """Compute centerline and publish Path + debug markers."""
        midpoints = self._compute_midpoints()
        if not midpoints:
            return

        now = self.get_clock().now().to_msg()
        self._publish_path(midpoints, now)
        self._publish_debug_markers(midpoints, now)

    # ------------------------------------------------------------------
    # Centerline computation
    # ------------------------------------------------------------------
    def _compute_midpoints(self) -> List[PathPoint]:
        """Pair cones and return candidate centerline points."""
        return self._pairing_algorithm()

    def _get_pairing_algorithm(self) -> PairingAlgorithm:
        algorithms: dict[str, PairingAlgorithm] = {
            'nearest': self._pair_nearest,
            'boundary': self._pair_boundary,
        }

        try:
            return algorithms[self._pairing_strategy]
        except KeyError as error:
            choices = ', '.join(algorithms)
            message = (
                f'Unknown pairing_strategy {self._pairing_strategy!r}; '
                f'choose {choices}'
            )
            raise ValueError(message) from error

    def _pair_nearest(self) -> List[PathPoint]:
        """
        Pair each left cone with the nearest unpaired right cone.

        After pairing, the configured chaining algorithm orders the
        midpoints, starting nearest the vehicle when odometry is available.

        This is a method to benchmark.
        """
        if not self._left_cones or not self._right_cones:
            return []

        right_items = list(self._right_cones.items())
        used_right: set = set()
        midpoints: List[PathPoint] = []

        for lid in sorted(self._left_cones.keys()):
            lx, ly = self._left_cones[lid]
            best_dist = float('inf')
            best_idx = -1

            for j, (_rid, (rx, ry)) in enumerate(right_items):
                if j in used_right:
                    continue
                d = (lx - rx) ** 2 + (ly - ry) ** 2
                if d < best_dist:
                    best_dist = d
                    best_idx = j

            if best_idx >= 0:
                used_right.add(best_idx)
                rx, ry = right_items[best_idx][1]
                midpoints.append(((lx + rx) / 2.0, (ly + ry) / 2.0))

            if len(midpoints) >= self._max_points:
                break

        # Chain midpoints into path order starting from the vehicle
        if len(midpoints) > 2 or (
            midpoints and self._chaining_alg == 'constrained_greedy'
        ):
            if self._have_odom:
                midpoints = self._chain_path_from_vehicle(midpoints)
            else:
                midpoints = self._chain_path_without_vehicle(midpoints)

        return midpoints

    def _pair_boundary(self) -> List[PathPoint]:
        """Pair Delaunay neighbors that can span the track boundaries."""
        blue_points = list(self._left_cones.values())
        yellow_points = list(self._right_cones.values())
        unknown_points = list(self._unknown_cones.values())
        points = blue_points + yellow_points + unknown_points
        if len(points) < 3:
            return []

        colors: List[ConeColor] = (
            ['blue'] * len(blue_points)
            + ['yellow'] * len(yellow_points)
            + [None] * len(unknown_points)
        )
        positions = np.array(points)

        # 1. Triangulate every cone position without using color.
        try:
            triangulation = Delaunay(positions)
        except QhullError:
            return []

        # 2. Extract each triangle edge once.
        edges: set[Edge] = set()
        for simplex in triangulation.simplices:
            for index in range(3):
                first = int(simplex[index])
                second = int(simplex[(index + 1) % 3])
                edges.add((min(first, second), max(first, second)))

        minimum_width = self._track_width - self._track_width_tol
        maximum_width = self._track_width + self._track_width_tol

        # 3. Reject known same-color edges, then apply the width check.
        accepted_edges: List[Edge] = []
        for first, second in sorted(edges):
            first_color = colors[first]
            second_color = colors[second]
            if first_color is not None and first_color == second_color:
                continue

            distance = math.dist(positions[first], positions[second])
            if minimum_width <= distance <= maximum_width:
                accepted_edges.append((first, second))

        # 4. Compute the midpoint of every accepted edge.
        midpoints = [
            (
                float((positions[first][0] + positions[second][0]) / 2.0),
                float((positions[first][1] + positions[second][1]) / 2.0),
            )
            for first, second in accepted_edges
        ]
        midpoints = midpoints[:self._max_points]

        if len(midpoints) > 2 or (
            midpoints and self._chaining_alg == 'constrained_greedy'
        ):
            if self._have_odom:
                midpoints = self._chain_path_from_vehicle(midpoints)
            else:
                midpoints = self._chain_path_without_vehicle(midpoints)

        return midpoints

    # ------------------------------------------------------------------
    # Path chaining helpers
    # ------------------------------------------------------------------
    def _chain_path_from_vehicle(
        self, points: List[PathPoint],
    ) -> List[PathPoint]:
        """Chain from the vehicle and orient the result with its heading."""
        chaining_algorithm: ChainingAlgorithm = self._get_chaining_algorithm()
        vx, vy = self._veh_x, self._veh_y
        start_idx = min(
            range(len(points)),
            key=lambda i: (points[i][0] - vx) ** 2 + (points[i][1] - vy) ** 2)

        ordered = chaining_algorithm(points, start_idx)

        # Constrained greedy chooses its direction before extending the path.
        if self._chaining_alg == 'constrained_greedy':
            return ordered

        if len(ordered) >= 2:
            dx = ordered[1][0] - ordered[0][0]
            dy = ordered[1][1] - ordered[0][1]
            path_angle = math.atan2(dy, dx)
            angle_diff = math.atan2(
                math.sin(path_angle - self._veh_yaw),
                math.cos(path_angle - self._veh_yaw))
            if abs(angle_diff) > math.pi / 2:
                ordered.reverse()

        return ordered

    def _chain_path_without_vehicle(
        self, points: List[PathPoint],
    ) -> List[PathPoint]:
        """Chain from point zero when vehicle odometry is unavailable."""
        chaining_algorithm: ChainingAlgorithm = self._get_chaining_algorithm()
        return chaining_algorithm(points, 0)

    def _get_chaining_algorithm(self) -> ChainingAlgorithm:
        algorithms: dict[str, ChainingAlgorithm] = {
            'greedy': self._greedy_chain_path,
            'constrained_greedy': self._constrained_greedy,
            'fixed_width_beam_search': self._fixed_width_beam_search,
        }

        try:
            return algorithms[self._chaining_alg]
        except KeyError as error:
            choices = ', '.join(algorithms)
            message = (
                f'Unknown chaining_alg {self._chaining_alg!r}; '
                f'choose {choices}'
            )
            raise ValueError(message) from error

    @staticmethod
    def _greedy_chain_path(
        points: List[PathPoint], start: int,
    ) -> List[PathPoint]:
        """Order points using the existing greedy nearest-neighbor walk."""
        ordered = [points[start]]
        remaining = set(range(len(points)))
        remaining.discard(start)

        while remaining:
            lx, ly = ordered[-1]
            best_j = min(
                remaining,
                key=lambda j: (
                    (points[j][0] - lx) ** 2
                    + (points[j][1] - ly) ** 2
                ),
            )
            ordered.append(points[best_j])
            remaining.remove(best_j)

        return ordered

    def _constrained_greedy(
        self, points: List[PathPoint], start: int,
    ) -> List[PathPoint]:
        """Choose low-cost steps inside a sector following the path heading."""
        if not points:
            return []
        ordered = [points[start]]
        remaining = set(range(len(points)))
        remaining.discard(start)
        heading = self._veh_yaw

        if not self._have_odom:
            # A separated neighbor supplies a tangent when yaw is unavailable.
            neighbors = [
                j for j in remaining
                if math.dist(points[start], points[j]) > 0.0
                and math.dist(points[start], points[j]) >= self._min_step_m
            ]
            if not neighbors:
                return ordered
            nearest = min(
                neighbors,
                key=lambda j: (math.dist(points[start], points[j]), j),
            )
            heading = math.atan2(
                points[nearest][1] - points[start][1],
                points[nearest][0] - points[start][0],
            )

        while remaining:
            x, y = ordered[-1]
            best_idx = None
            best_score = float('inf')
            best_heading = heading
            redundant = set()
            for j in remaining:
                dx, dy = points[j][0] - x, points[j][1] - y
                distance = math.hypot(dx, dy)
                if distance == 0.0 or distance < self._min_step_m:
                    # Suppress close duplicates so they cannot rejoin later.
                    redundant.add(j)
                    continue
                if distance > self._max_step_m:
                    continue
                candidate_heading = math.atan2(dy, dx)
                heading_error = math.atan2(
                    math.sin(candidate_heading - heading),
                    math.cos(candidate_heading - heading),
                )
                if abs(heading_error) > self._max_turn_rad:
                    continue
                score = (
                    self._distance_weight * distance / self._max_step_m
                    + self._heading_weight
                    * (heading_error / self._max_turn_rad) ** 2
                )
                if (best_idx is None or score < best_score
                        or (score == best_score and j < best_idx)):
                    best_idx = j
                    best_score = score
                    best_heading = candidate_heading

            remaining.difference_update(redundant)
            if best_idx is None:
                break
            ordered.append(points[best_idx])
            remaining.remove(best_idx)
            heading = best_heading

        return ordered

    @staticmethod
    def _fixed_width_beam_search(
        points: List[PathPoint], start: int,
    ) -> List[PathPoint]:
        """Order points using fixed-width beam search."""
        raise NotImplementedError(
            'fixed-width beam search is not implemented',
        )

    # ------------------------------------------------------------------
    # Publishing helpers
    # ------------------------------------------------------------------
    def _make_header(self, stamp: Time) -> Header:
        header = Header()
        header.stamp = stamp
        header.frame_id = self._frame_id
        return header

    def _publish_path(self, midpoints: List[PathPoint], stamp: Time):
        path = Path()
        path.header = self._make_header(stamp)
        for x, y in midpoints:
            ps = PoseStamped()
            ps.header = self._make_header(stamp)
            ps.pose.position.x = x
            ps.pose.position.y = y
            ps.pose.position.z = 0.0
            ps.pose.orientation.w = 1.0
            path.poses.append(ps)
        self._path_pub.publish(path)

    def _publish_debug_markers(
        self, midpoints: List[PathPoint], stamp: Time,
    ):
        markers = MarkerArray()

        # Midpoint spheres
        for i, (x, y) in enumerate(midpoints):
            m = Marker()
            m.header = self._make_header(stamp)
            m.ns = 'centerline_points'
            m.id = i
            m.type = Marker.SPHERE
            m.action = Marker.ADD
            m.pose.position = Point(x=x, y=y, z=0.0)
            m.pose.orientation.w = 1.0
            m.scale.x = 0.2
            m.scale.y = 0.2
            m.scale.z = 0.2
            m.color = ColorRGBA(r=0.0, g=1.0, b=0.0, a=1.0)
            markers.markers.append(m)

        # LINE_STRIP connecting midpoints
        line = Marker()
        line.header = self._make_header(stamp)
        line.ns = 'centerline_line'
        line.id = 0
        line.type = Marker.LINE_STRIP
        line.action = Marker.ADD
        line.scale.x = 0.08
        line.color = ColorRGBA(r=0.0, g=1.0, b=0.0, a=0.8)
        line.pose.orientation.w = 1.0
        for x, y in midpoints:
            line.points.append(Point(x=x, y=y, z=0.0))
        markers.markers.append(line)

        self._debug_pub.publish(markers)


def main():
    """Entry point."""
    rclpy.init()
    node = TrackBuilder()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
