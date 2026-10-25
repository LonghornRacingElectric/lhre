"""Conservative planar vehicle-envelope and cone-base scoring on scene truth."""

import hashlib
import json
import math

from lhr_trackgen.cone_geometry import CONE_SPECS

# Bound the motion of every footprint point between sampled sweep poses.
# Each sample is padded by half this bound, covering intermediate poses too.
SWEEP_STEP_M = .05
FOOTPRINT_MODEL = 'body_wheel_envelope_v1'


def rectangle(x, y, yaw, rear, front, half_width):
    """Transform a base_link rectangle into map coordinates."""
    c, s = math.cos(yaw), math.sin(yaw)
    return tuple((x + c*u - s*v, y + s*u + c*v)
                 for u, v in ((rear, -half_width), (front, -half_width),
                              (front, half_width), (rear, half_width)))


def bounds(points):
    """Return axis-aligned broad-phase bounds."""
    return min(x for x, _ in points), min(y for _, y in points), \
        max(x for x, _ in points), max(y for _, y in points)


def overlap(a, b):
    """Include touching bounds as a possible contact."""
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def inside(point, polygon):
    """Test polygon interior by ray crossing; boundary contact is checked separately."""
    x, y = point
    result = False
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        if (a[1] > y) != (b[1] > y):
            if x < a[0] + (y-a[1]) * (b[0]-a[0]) / (b[1]-a[1]):
                result = not result
    return result


def segment_hits_rectangle(a, b, x, y, yaw, rear, front, half_width):
    """Clip a boundary segment against a vehicle-frame box, including tangency."""
    c, s = math.cos(yaw), math.sin(yaw)
    local = [(c*(px-x) + s*(py-y), -s*(px-x) + c*(py-y)) for px, py in (a, b)]
    low, high = 0., 1.
    for dimension, minimum, maximum in ((0, rear, front), (1, -half_width, half_width)):
        start = local[0][dimension]
        delta = local[1][dimension] - start
        if abs(delta) < 1e-12:
            if not minimum <= start <= maximum:
                return False
        else:
            t1, t2 = sorted(((minimum-start)/delta, (maximum-start)/delta))
            low, high = max(low, t1), min(high, t2)
            if low > high:
                return False
    return True


def rectangles_touch(a, b):
    """Use separating axes for two convex rectangles; touching counts as contact."""
    for polygon in (a, b):
        for p, q in zip(polygon[:2], polygon[1:3]):
            nx, ny = p[1]-q[1], q[0]-p[0]
            ap = [x*nx + y*ny for x, y in a]
            bp = [x*nx + y*ny for x, y in b]
            if max(ap) < min(bp) or max(bp) < min(ap):
                return False
    return True


class CourseEnvelope:
    """Latch road boundaries and score a padded sweep of the whole vehicle."""

    def __init__(self, left, right, cones, vehicle, closed):
        if len(left) != len(right) or len(left) < 2:
            raise ValueError('course needs matching ordered boundary pairs')
        if not all(math.isfinite(v) for side in (left, right) for point in side for v in point):
            raise ValueError('course coordinates must be finite')
        # A single conservative box includes the chassis and all wheel steering
        # angles. It deliberately includes the space between collision solids.
        wheel_extent = math.hypot(vehicle.wheel_radius_m, vehicle.wheel_width_m/2)
        self.rear = min(vehicle.chassis_center_x_m - vehicle.body_length_m/2, -wheel_extent)
        self.front = max(vehicle.chassis_center_x_m + vehicle.body_length_m/2,
                         vehicle.wheelbase_m + wheel_extent)
        self.half_width = max(vehicle.body_width_m/2, vehicle.half_track_m + wheel_extent)
        self.radius = math.hypot(max(abs(self.rear), abs(self.front)), self.half_width)
        self.quads, self.edges = [], []
        count = len(left) if closed else len(left)-1
        for i in range(count):
            j = (i+1) % len(left)
            quad = (left[i], left[j], right[j], right[i])
            self.quads.append((quad, bounds(quad)))
            self.edges.extend(((left[i], left[j]), (right[i], right[j])))
        if not closed:
            self.edges.extend(((left[0], right[0]), (left[-1], right[-1])))
        self.edges = [(a, b, bounds((a, b))) for a, b in self.edges]
        self.cones = []
        identities = set()
        for identity, x, y, yaw, kind in cones:
            if identity in identities or kind not in CONE_SPECS:
                raise ValueError('duplicate cone identity or unknown cone envelope')
            if not all(map(math.isfinite, (x, y, yaw))):
                raise ValueError('cone pose must be finite')
            identities.add(identity)
            half = CONE_SPECS[kind].base_width_m/2
            polygon = rectangle(x, y, yaw, -half, half, half)
            self.cones.append((identity, polygon, bounds(polygon)))
        self.sha256 = hashlib.sha256(json.dumps(
            {'left': left, 'right': right, 'closed': closed, 'cones': cones,
             'footprint': [self.rear, self.front, self.half_width],
             'cone_base_widths': {kind: spec.base_width_m for kind, spec in CONE_SPECS.items()},
             'model': FOOTPRINT_MODEL, 'sweep_step_m': SWEEP_STEP_M},
            separators=(',', ':')).encode()).hexdigest()
        self.hit_ids = set()
        self.previous = None

    def contains(self, x, y):
        """Test membership in the union of ordered boundary strips."""
        return any(box[0] <= x <= box[2] and box[1] <= y <= box[3]
                   and inside((x, y), quad) for quad, box in self.quads)

    def pose_check(self, x, y, yaw, padding=0.):
        """Return off-course status and touched nominal cone identities."""
        rear, front, half = self.rear-padding, self.front+padding, self.half_width+padding
        polygon = rectangle(x, y, yaw, rear, front, half)
        box = bounds(polygon)
        outside = (not all(self.contains(*point) for point in polygon)
                   or any(overlap(box, edge_box) and segment_hits_rectangle(
                       a, b, x, y, yaw, rear, front, half) for a, b, edge_box in self.edges))
        hits = {identity for identity, cone, cone_box in self.cones
                if overlap(box, cone_box) and rectangles_touch(polygon, cone)}
        return outside, hits

    def observe(self, x, y, yaw, continuous=True):
        """Cover translation and shortest-angle rotation between valid observations."""
        if not all(map(math.isfinite, (x, y, yaw))):
            raise ValueError('scored pose must be finite')
        previous, self.previous = self.previous, (x, y, yaw)
        if previous is None or not continuous:
            outside, hits = self.pose_check(x, y, yaw)
        else:
            px, py, angle = previous
            turn = (yaw-angle+math.pi) % (2*math.pi) - math.pi
            motion = math.hypot(x-px, y-py) + self.radius*abs(turn)
            steps = max(1, math.ceil(motion / SWEEP_STEP_M))
            padding = motion / (2*steps)
            outside, hits = False, set()
            for i in range(steps+1):
                fraction = i/steps
                bad, contacts = self.pose_check(px+(x-px)*fraction, py+(y-py)*fraction,
                                                angle+turn*fraction, padding)
                outside |= bad
                hits.update(contacts)
        self.hit_ids.update(hits)
        return outside, hits
