#!/usr/bin/env python3
"""Cone publisher: generates a track and publishes left/right cone markers."""

import math
import random
from typing import List, Tuple

from geometry_msgs.msg import Point
from lhr_trackgen.cone_geometry import CONE_SPECS, cone_triangles, start_finish_gates
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import ColorRGBA
from visualization_msgs.msg import Marker, MarkerArray

# ---------------------------------------------------------------------------
# Track generators
# ---------------------------------------------------------------------------

ConeList = List[Tuple[float, float]]


def generate_simple_track(seed: int = 0,
                          **_kw) -> Tuple[ConeList, ConeList]:
    """Original squashed-oval generator (kept for debugging)."""
    rng = random.Random(seed)

    n = 80
    a, b, wobble = 20.0, 12.0, 2.0

    center = []
    for i in range(n):
        t = 2.0 * math.pi * i / n
        x = a * math.cos(t) + wobble * math.cos(3 * t)
        y = b * math.sin(t) + wobble * math.sin(2 * t)
        center.append((x, y))

    width = 3.5
    half = width / 2.0

    left: ConeList = []
    right: ConeList = []
    for i in range(n):
        x0, y0 = center[i - 1]
        x1, y1 = center[i]
        x2, y2 = center[(i + 1) % n]

        tx = x2 - x0
        ty = y2 - y0
        norm = math.hypot(tx, ty) or 1.0
        tx /= norm
        ty /= norm

        nx, ny = -ty, tx
        jitter = rng.uniform(-0.05, 0.05)

        lx = x1 + (half + jitter) * nx
        ly = y1 + (half + jitter) * ny
        rx = x1 - (half + jitter) * nx
        ry = y1 - (half + jitter) * ny

        if i % 2 == 0:
            left.append((lx, ly))
            right.append((rx, ry))

    return left, right


# ---------------------------------------------------------------------------
# Oval generator
# ---------------------------------------------------------------------------

def generate_oval_track(
    seed: int = 0,
    radius_m: float = 25.0,
    width_m: float = 3.5,
    cone_spacing_m: float = 2.0,
    aspect: float = 0.6,
    **_kw,
) -> Tuple[ConeList, ConeList]:
    """
    Generate a smooth oval (elliptical) track.

    The oval has semi-major axis *radius_m* and semi-minor axis
    *radius_m * aspect*.  Cones are placed at uniform arc-length
    intervals no larger than *cone_spacing_m* on either boundary.
    """
    _validate_dimensions(radius_m, width_m, cone_spacing_m)
    if not math.isfinite(aspect) or aspect <= 0:
        raise ValueError('aspect must be positive and finite')
    dense = [(radius_m * math.cos(2 * math.pi * i / 1000),
              radius_m * aspect * math.sin(2 * math.pi * i / 1000))
             for i in range(1000)]
    if _minimum_radius(dense) <= width_m / 2:
        raise ValueError('oval is too tight for the requested width')
    return _offset_cones(dense, width_m / 2, cone_spacing_m)


# ---------------------------------------------------------------------------
# Autocross generator (waypoint + Catmull-Rom)
# ---------------------------------------------------------------------------

def _catmull_rom_segment(
    p0: Tuple[float, float],
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    p3: Tuple[float, float],
    num_pts: int,
) -> List[Tuple[float, float]]:
    """Evaluate Catmull-Rom spline between p1 and p2."""
    pts: List[Tuple[float, float]] = []
    for k in range(num_pts):
        t = k / num_pts
        t2 = t * t
        t3 = t2 * t

        # Catmull-Rom basis (tau = 0.5)
        b0 = -0.5 * t3 + t2 - 0.5 * t
        b1 = 1.5 * t3 - 2.5 * t2 + 1.0
        b2 = -1.5 * t3 + 2.0 * t2 + 0.5 * t
        b3 = 0.5 * t3 - 0.5 * t2

        x = b0 * p0[0] + b1 * p1[0] + b2 * p2[0] + b3 * p3[0]
        y = b0 * p0[1] + b1 * p1[1] + b2 * p2[1] + b3 * p3[1]
        pts.append((x, y))
    return pts


def _catmull_rom_closed(
    waypoints: List[Tuple[float, float]],
    pts_per_seg: int = 20,
) -> List[Tuple[float, float]]:
    """Build a closed Catmull-Rom spline through waypoints."""
    n = len(waypoints)
    curve: List[Tuple[float, float]] = []
    for i in range(n):
        p0 = waypoints[(i - 1) % n]
        p1 = waypoints[i]
        p2 = waypoints[(i + 1) % n]
        p3 = waypoints[(i + 2) % n]
        curve.extend(_catmull_rom_segment(p0, p1, p2, p3, pts_per_seg))
    return curve


def _validate_dimensions(radius, width, spacing):
    """Reject invalid scene dimensions before sampling."""
    if any(not math.isfinite(v) or v <= 0 for v in (radius, width, spacing)):
        raise ValueError('radius, width and spacing must be positive and finite')


def _minimum_radius(points):
    """Measure the tightest bend using three-point circumcircles."""
    minimum = math.inf
    for i, b in enumerate(points):
        a, c = points[i - 1], points[(i + 1) % len(points)]
        cross = abs((b[0] - a[0]) * (c[1] - b[1])
                    - (b[1] - a[1]) * (c[0] - b[0]))
        if cross > 1e-12:
            radius = (math.dist(a, b) * math.dist(b, c) * math.dist(a, c)
                      / (2 * cross))
            minimum = min(minimum, radius)
    return minimum


def _boundaries_cross(left, right):
    """Reject crossed boundaries, including nonadjacent edges of either loop."""
    edges = []
    for side, points in enumerate((left, right)):
        edges.extend((side, i, p, points[(i + 1) % len(points)])
                     for i, p in enumerate(points))

    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    for k, (side, i, a, b) in enumerate(edges):
        for other, j, c, d in edges[k + 1:]:
            if side == other and (i - j) % len(left) in (0, 1, len(left) - 1):
                continue
            if (max(a[0], b[0]) < min(c[0], d[0])
                    or max(c[0], d[0]) < min(a[0], b[0])
                    or max(a[1], b[1]) < min(c[1], d[1])
                    or max(c[1], d[1]) < min(a[1], b[1])):
                continue
            if orient(a, b, c) * orient(a, b, d) <= 0 and \
                    orient(c, d, a) * orient(c, d, b) <= 0:
                return True
    return False


def _offset_cones(center, half_width, cone_spacing):
    """Space paired cones by the longer boundary, including the closing edge."""
    normals = []
    for i in range(len(center)):
        a, b = center[i - 1], center[(i + 1) % len(center)]
        tx, ty = b[0] - a[0], b[1] - a[1]
        length = math.hypot(tx, ty)
        normals.append((-ty / length, tx / length))
    boundaries = [[(p[0] + sign * half_width * n[0],
                    p[1] + sign * half_width * n[1])
                   for p, n in zip(center, normals)] for sign in (1, -1)]
    arc = [0.0]
    for i in range(len(center)):
        arc.append(arc[-1] + max(math.dist(side[i], side[(i + 1) % len(center)])
                                 for side in boundaries))
    # A tiny reserve covers normal renormalization during interpolation.
    count = max(3, math.ceil(arc[-1] / (cone_spacing * .999)))
    left, right = [], []
    j = 0
    for i in range(count):
        target = arc[-1] * i / count
        while arc[j + 1] < target:
            j += 1
        f = (target - arc[j]) / (arc[j + 1] - arc[j])
        k = (j + 1) % len(center)
        x, y = (center[j][axis] * (1 - f) + center[k][axis] * f
                for axis in (0, 1))
        nx, ny = (normals[j][axis] * (1 - f) + normals[k][axis] * f
                  for axis in (0, 1))
        length = math.hypot(nx, ny)
        nx, ny = nx / length, ny / length
        left.append((x + half_width * nx, y + half_width * ny))
        right.append((x - half_width * nx, y - half_width * ny))
    return left, right


def generate_autocross_track(
    seed: int = 0,
    num_waypoints: int = 10,
    radius_m: float = 25.0,
    jitter_m: float = 10.0,
    width_m: float = 3.5,
    cone_spacing_m: float = 2.0,
    **_kw,
) -> Tuple[ConeList, ConeList]:
    """
    Generate a randomised autocross track with S-curves and chicanes.

    Reduce seeded waypoint jitter until bends have radius at least
    max(6 m, twice the track width), then sample both closed boundaries.
    """
    _validate_dimensions(radius_m, width_m, cone_spacing_m)
    if num_waypoints < 4 or not math.isfinite(jitter_m) or jitter_m < 0:
        raise ValueError('use at least four waypoints and nonnegative finite jitter')
    rng = random.Random(seed)
    perturbations = [(rng.uniform(-math.pi / num_waypoints * .6,
                                  math.pi / num_waypoints * .6),
                      rng.uniform(-jitter_m, jitter_m))
                     for _ in range(num_waypoints)]
    # Preserve seeded character while reducing bends that fold the corridor.
    for attempt in range(24):
        strength = .75 ** attempt
        waypoints = []
        for i, (angle_delta, radius_delta) in enumerate(perturbations):
            angle = 2 * math.pi * i / num_waypoints + strength * angle_delta
            radius = max(radius_m * .3, radius_m + strength * radius_delta)
            waypoints.append((radius * math.cos(angle), radius * math.sin(angle)))
        center = _catmull_rom_closed(waypoints, 100)
        if _minimum_radius(center) < max(6.0, width_m * 2):
            continue
        left, right = _offset_cones(center, width_m / 2, cone_spacing_m)
        if not _boundaries_cross(left, right):
            return left, right
    raise ValueError('track dimensions cannot support a smooth, uncrossed corridor')


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

GENERATORS = {
    'simple': generate_simple_track,
    'autocross': generate_autocross_track,
    'oval': generate_oval_track,
}


# ---------------------------------------------------------------------------
# ROS node
# ---------------------------------------------------------------------------

class ConePublisher(Node):
    """Publish left/right cone markers on /lhr/track/cones."""

    def __init__(self):
        super().__init__('cone_publisher')

        # --- Parameters ---
        self.declare_parameter('start_finish_cones', False)
        self.declare_parameter('seed', 1)
        self.declare_parameter('frame_id', 'map')
        self.declare_parameter('publish_hz', 5.0)
        self.declare_parameter('track_style', 'autocross')
        self.declare_parameter('num_waypoints', 10)
        self.declare_parameter('radius_m', 25.0)
        self.declare_parameter('jitter_m', 10.0)
        self.declare_parameter('width_m', 3.5)
        self.declare_parameter('cone_spacing_m', 2.0)

        self.seed = int(self.get_parameter('seed').value)
        self.start_finish_cones = bool(self.get_parameter('start_finish_cones').value)
        self.frame_id = str(self.get_parameter('frame_id').value)
        hz = float(self.get_parameter('publish_hz').value)
        style = str(self.get_parameter('track_style').value)
        num_wp = int(self.get_parameter('num_waypoints').value)
        radius = float(self.get_parameter('radius_m').value)
        jitter = float(self.get_parameter('jitter_m').value)
        width = float(self.get_parameter('width_m').value)
        spacing = float(self.get_parameter('cone_spacing_m').value)

        # --- Generate track ---
        gen = GENERATORS.get(style)
        if gen is None:
            self.get_logger().warn(
                f"Unknown track_style '{style}', falling back to 'autocross'")
            gen = generate_autocross_track

        self.left, self.right = gen(
            seed=self.seed,
            num_waypoints=num_wp,
            radius_m=radius,
            jitter_m=jitter,
            width_m=width,
            cone_spacing_m=spacing,
        )

        # --- Publisher ---
        qos = QoSProfile(depth=1)
        qos.reliability = ReliabilityPolicy.RELIABLE
        qos.durability = DurabilityPolicy.TRANSIENT_LOCAL

        self.pub = self.create_publisher(
            MarkerArray, '/lhr/track/cones', qos)

        period = 1.0 / max(hz, 0.1)
        self.timer = self.create_timer(period, self.on_timer)

        self.get_logger().info(
            f'Publishing {len(self.left)} left + {len(self.right)} right '
            f'cones (style={style}, seed={self.seed})')

    def make_cone_marker(
        self, mid: int, ns: str,
        x: float, y: float,
        r: float, g: float, b: float, kind=None,
    ) -> Marker:
        """Create a nominal small competition cone marker."""
        m = Marker()
        m.header.frame_id = self.frame_id
        m.header.stamp = self.get_clock().now().to_msg()
        m.ns = ns
        m.id = mid
        m.type = Marker.TRIANGLE_LIST
        m.action = Marker.ADD
        m.pose.position.x = float(x)
        m.pose.position.y = float(y)
        m.pose.position.z = 0.0
        m.pose.orientation.w = 1.0

        m.scale.x = m.scale.y = m.scale.z = 1.0
        kind = kind or ('blue' if ns == 'left_cones' else 'yellow')
        m.text = kind
        faces, colors = cone_triangles(CONE_SPECS[kind])
        for face, color in zip(faces, colors):
            for px, py, pz in face:
                m.points.append(Point(x=px, y=py, z=pz))
                m.colors.append(ColorRGBA(r=color[0], g=color[1], b=color[2], a=1.0))

        m.color.a = 1.0
        m.color.r = m.color.g = m.color.b = 1.0
        return m

    def on_timer(self):
        """Publish all cone markers."""
        arr = MarkerArray()

        for i, (x, y) in enumerate(self.left):
            arr.markers.append(
                self.make_cone_marker(i, 'left_cones', x, y,
                                      0.0, 0.2, 1.0))

        base = 10000
        for i, (x, y) in enumerate(self.right):
            arr.markers.append(
                self.make_cone_marker(base + i, 'right_cones', x, y,
                                      1.0, 1.0, 0.0))

        if self.start_finish_cones:
            for i, (x, y) in enumerate(start_finish_gates(self.left, self.right)):
                arr.markers.append(self.make_cone_marker(
                    20000 + i, 'start_finish', x, y, 1., .35, 0., kind='orange_large'))
        self.pub.publish(arr)


def main():
    """Entry point."""
    rclpy.init()
    node = ConePublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
