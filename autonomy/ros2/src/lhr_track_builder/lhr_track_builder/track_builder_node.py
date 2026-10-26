#!/usr/bin/env python3
"""Track builder node: subscribes to cones, publishes centerline Path."""

import math
from typing import List, Protocol, Tuple

from builtin_interfaces.msg import Time
from geometry_msgs.msg import Point, PoseStamped
from lhr_track_builder.centerline import pair_classified_cones
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
        self.declare_parameter('pairing_strategy', 'boundary')
        self.declare_parameter('cone_topic', '/lhr/sensor/cones_detected')
        self.declare_parameter('track_width', 3.5)
        self.declare_parameter('track_width_tolerance', 1.0)
        self.declare_parameter('planning_horizon_m', 30.0)
        self.declare_parameter('max_path_gap_m', 4.0)
        self.declare_parameter('max_path_turn_deg', 60.0)
        self.declare_parameter('boundary_use_color', True)
        self.declare_parameter('chaining_alg', 'constrained_greedy')
        self.declare_parameter('min_step_m', 0.3)
        self.declare_parameter('max_step_m', 4.0)
        self.declare_parameter('max_turn_deg', 70.0)
        self.declare_parameter('distance_weight', 1.0)
        self.declare_parameter('heading_weight', 1.0)
        self.declare_parameter('beam_width', 3)
        self.declare_parameter('beam_depth', 10)

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
        self._boundary_use_color = self.get_parameter(
            'boundary_use_color').get_parameter_value().bool_value
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
        self._beam_width = self.get_parameter('beam_width').value
        self._beam_depth = self.get_parameter('beam_depth').value
        for name, value in (
            ('beam_width', self._beam_width), ('beam_depth', self._beam_depth),
        ):
            if type(value) is not int or value <= 0:
                raise ValueError(f'{name} must be a positive integer')

        self._planning_horizon = float(self.get_parameter('planning_horizon_m').value)
        self._max_path_gap = float(self.get_parameter('max_path_gap_m').value)
        self._min_turn_cos = math.cos(math.radians(
            float(self.get_parameter('max_path_turn_deg').value)))

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
            if marker.action != Marker.ADD:
                continue
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
        if unknown:
            if self._pairing_strategy in ('boundary', 'boundary_graph'):
                handling = 'Boundary pairing uses geometry for these cones.'
            else:
                handling = (
                    f'{self._pairing_strategy.capitalize()} pairing ignores these cones; select '
                    'pairing_strategy:=boundary to use geometry-based pairing.'
                )
            self.get_logger().warning(
                f'Received {len(unknown)} uncolored cones (namespace "cones"). '
                f'{handling}',
                throttle_duration_sec=5.0,
            )

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
            self._publish_path([], self.get_clock().now().to_msg())
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
        if self._pairing_strategy == 'boundary_graph':
            return self._pair_boundary_graph
        algorithms: dict[str, PairingAlgorithm] = {
            'nearest': self._pair_nearest,
            'boundary': self._pair_boundary,
            'classified': self._pair_classified,
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

    def _pair_classified(self) -> List[PathPoint]:
        """Pair inferred left/right cones once within the track-width gate."""
        left = [position for _, position in sorted(self._left_cones.items())]
        right = [position for _, position in sorted(self._right_cones.items())]
        midpoints = pair_classified_cones(
            left,
            right,
            self._track_width,
            self._track_width_tol,
        )

        return self._chain_midpoints(midpoints)

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

        return self._chain_midpoints(midpoints)

    def _pair_boundary(self) -> List[PathPoint]:
        """Filter Delaunay edges by track width and optionally by cone color."""
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

        # Disabling the color filter preserves the original geometry-only pairing.
        accepted_edges: List[Edge] = []
        for first, second in sorted(edges):
            first_color = colors[first]
            second_color = colors[second]
            if (self._boundary_use_color and first_color is not None
                    and first_color == second_color):
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
        return self._chain_midpoints(midpoints)

    def _pair_boundary_graph(self) -> List[Tuple[float, float]]:
        """
        Pair cones across track boundaries using Delaunay triangulation.

        Connect midpoint candidates through shared triangles, then select
        a bounded forward corridor. Width alone cannot distinguish a
        cross-track pair from an edge along one boundary.
        """
        if len(self._left_cones) + len(self._right_cones) + len(self._unknown_cones) < 4:
            return []

        pts = np.array(list(self._left_cones.values()) + list(self._right_cones.values())
                       + list(self._unknown_cones.values()))

        try:
            tri = Delaunay(pts)
        except QhullError:
            return []

        # Connect cross-track edge midpoints only through shared triangles.
        # A global nearest-neighbour tour can join separate track sections or
        # run back down a boundary as previously unseen cones enter the map.
        edges = set()
        triangles = []
        for simplex in tri.simplices:
            triangle = [tuple(sorted((int(simplex[i]), int(simplex[(i + 1) % 3]))))
                        for i in range(3)]
            triangles.append(triangle)
            edges.update(triangle)

        lo = self._track_width - self._track_width_tol
        hi = self._track_width + self._track_width_tol
        # Short nearest-neighbour edges estimate each observed boundary's
        # tangent without colour labels. Long edges along that tangent are
        # skipped boundary cones, not cross-track pairs.
        tangents = {}
        for i, point in enumerate(pts):
            delta = pts - point
            distances = np.linalg.norm(delta, axis=1)
            distances[i] = np.inf
            neighbour = int(np.argmin(distances))
            if 0.5 < distances[neighbour] < lo:
                tangents[i] = delta[neighbour] / distances[neighbour]

        candidates = {}
        normals = {}
        widths = {}
        diagonal_limit = math.hypot(hi, self._max_path_gap)
        for edge in sorted(edges):
            a, b = edge
            delta = pts[b] - pts[a]
            width = float(np.linalg.norm(delta))
            if not lo <= width <= diagonal_limit:
                continue
            if any(abs(float(tangents[i] @ delta)) / width > 0.85
                   for i in (a, b) if i in tangents):
                continue
            midpoint = (pts[a] + pts[b]) / 2.0
            # A same-boundary edge that skips an observed cone is not a
            # cross-track pair. Preserve diagonals through curved corridors:
            # their midpoints connect the triangles into a continuous path.
            projection = (pts - pts[a]) @ delta / (width * width)
            closest = pts[a] + projection[:, None] * delta
            clearance = np.linalg.norm(pts - closest, axis=1)
            if np.any((projection > 1e-6) & (projection < 1.0 - 1e-6)
                      & (clearance < 0.25)):
                continue
            widths[edge] = width
            candidates[edge] = midpoint
            normals[edge] = np.array([-delta[1], delta[0]]) / width

        if not candidates or not self._have_odom:
            return []
        graph = {edge: set() for edge in candidates}
        for triangle in triangles:
            valid = [edge for edge in triangle if edge in candidates]
            for edge in valid:
                graph[edge].update(other for other in valid if other != edge)

        vehicle = np.array([self._veh_x, self._veh_y])
        heading = np.array([math.cos(self._veh_yaw), math.sin(self._veh_yaw)])
        starts = [edge for edge, midpoint in candidates.items()
                  if np.linalg.norm(midpoint - vehicle) <= self._max_path_gap * 2.5
                  and abs(float(normals[edge] @ heading)) >= self._min_turn_cos]
        # Prefer a seed with a forward graph continuation. The nearest
        # candidate may be the end of a partially observed corridor.

        def has_forward_link(edge):
            for other in graph[edge]:
                delta = candidates[other] - candidates[edge]
                gap = float(np.linalg.norm(delta))
                if gap > 1e-6 and gap <= self._max_path_gap:
                    direction = delta / gap
                    if (float(direction @ heading) >= self._min_turn_cos
                            and abs(float(normals[other] @ direction)) >= self._min_turn_cos):
                        return True
            return False

        starts = [edge for edge in starts if has_forward_link(edge)]
        if not starts:
            return []
        paths = []
        for start in starts:
            current = start
            ordered = [candidates[current]]
            visited = {current}
            direction_now = heading.copy()
            length = 0.0
            turn_cost = 0.0
            while len(ordered) < self._max_points:
                choices = []
                for edge in sorted(graph[current] - visited):
                    delta = candidates[edge] - candidates[current]
                    gap = float(np.linalg.norm(delta))
                    if gap < 1e-6 or gap > self._max_path_gap:
                        continue
                    direction = delta / gap
                    forward = float(direction @ direction_now)
                    alignment = abs(float(normals[edge] @ direction))
                    cross_width = widths[edge] * alignment
                    if (forward < self._min_turn_cos or alignment < self._min_turn_cos
                            or not lo <= cross_width <= hi):
                        continue
                    cost = (2.0 - forward - alignment
                            + abs(cross_width - self._track_width) / self._track_width)
                    choices.append((cost, gap, edge, direction))
                if not choices:
                    break
                cost, gap, edge, direction_now = min(choices, key=lambda choice: choice[:3])
                if length + gap > self._planning_horizon:
                    break
                ordered.append(candidates[edge])
                visited.add(edge)
                current = edge
                length += gap
                turn_cost += cost
            if len(ordered) < 2:
                continue
            # Do not select a nearby dead end when a continuous observed
            # corridor exists. Require some of the route ahead of the car.
            if max(float((point - vehicle) @ heading) for point in ordered) <= 0.1:
                continue
            score = (float(np.linalg.norm(candidates[start] - vehicle))
                     + self._track_width * (1.0 - abs(float(normals[start] @ heading)))
                     + 0.4 * turn_cost - 0.4 * length)
            paths.append((score, start, ordered))
        if not paths:
            return []
        ordered = min(paths, key=lambda path: path[:2])[2]
        return [tuple(point) for point in ordered]

    # ------------------------------------------------------------------
    # Path chaining helpers
    # ------------------------------------------------------------------
    def _chain_midpoints(self, midpoints: List[PathPoint]) -> List[PathPoint]:
        """Order paired candidates before applying the published path cap."""
        if len(midpoints) > 2 or (
            midpoints and self._chaining_alg in (
                'constrained_greedy', 'fixed_width_beam_search',
            )
        ):
            if self._have_odom:
                midpoints = self._chain_path_from_vehicle(midpoints)
            else:
                midpoints = self._chain_path_without_vehicle(midpoints)

        return midpoints[:self._max_points]

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

        # Sector-based algorithms choose direction before extending the path.
        if self._chaining_alg in ('constrained_greedy', 'fixed_width_beam_search'):
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

    def _initial_heading(self, points: List[PathPoint], start: int) -> float | None:
        """Use vehicle yaw or infer a tangent from a separated neighbor."""
        if self._have_odom:
            return self._veh_yaw

        neighbors = [
            j for j in range(len(points))
            if j != start
            and math.dist(points[start], points[j]) > 0.0
            and math.dist(points[start], points[j]) >= self._min_step_m
        ]
        if not neighbors:
            return None
        nearest = min(
            neighbors,
            key=lambda j: (math.dist(points[start], points[j]), j),
        )
        return math.atan2(
            points[nearest][1] - points[start][1],
            points[nearest][0] - points[start][0],
        )

    def _constrained_greedy(
        self, points: List[PathPoint], start: int,
    ) -> List[PathPoint]:
        """Choose low-cost steps inside a sector following the path heading."""
        if not points:
            return []
        ordered = [points[start]]
        remaining = set(range(len(points)))
        remaining.discard(start)
        heading = self._initial_heading(points, start)
        if heading is None:
            return ordered

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

    def _fixed_width_beam_search(
        self, points: List[PathPoint], start: int,
    ) -> List[PathPoint]:
        """Keep the lowest-cost partial paths across each expansion depth."""
        if not points:
            return []
        remaining = set(range(len(points)))
        remaining.discard(start)
        heading = self._initial_heading(points, start)
        if heading is None:
            return [points[start]]

        beam = [{
            'path': [start], 'remaining': remaining,
            'heading': heading, 'cost': 0.0,
        }]
        for _ in range(self._beam_depth):
            extensions = []
            for state in beam:
                x, y = points[state['path'][-1]]
                remaining = state['remaining'].copy()
                # Remove duplicates before copying the candidate set to children.
                redundant = {
                    j for j in remaining
                    if math.dist((x, y), points[j]) == 0.0
                    or math.dist((x, y), points[j]) < self._min_step_m
                }
                remaining.difference_update(redundant)
                for j in remaining:
                    dx, dy = points[j][0] - x, points[j][1] - y
                    distance = math.hypot(dx, dy)
                    if distance > self._max_step_m:
                        continue
                    candidate_heading = math.atan2(dy, dx)
                    heading_error = math.atan2(
                        math.sin(candidate_heading - state['heading']),
                        math.cos(candidate_heading - state['heading']),
                    )
                    if abs(heading_error) > self._max_turn_rad:
                        continue
                    step_cost = (
                        self._distance_weight * distance / self._max_step_m
                        + self._heading_weight
                        * (heading_error / self._max_turn_rad) ** 2
                    )
                    extensions.append({
                        'path': state['path'] + [j],
                        'remaining': remaining - {j},
                        'heading': candidate_heading,
                        'cost': state['cost'] + step_cost,
                    })

            if not extensions:
                break
            # All extensions have equal depth; dead ends cannot win on low cost.
            extensions.sort(key=lambda state: (state['cost'], state['path']))
            beam = extensions[:self._beam_width]

        return [points[j] for j in beam[0]['path']]

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
        rclpy.try_shutdown()
