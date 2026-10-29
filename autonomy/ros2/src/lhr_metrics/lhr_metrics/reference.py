"""Immutable reference geometry and directed circuit checks, independent of ROS."""

import hashlib
import json
import math
import xml.etree.ElementTree as ET


def paired_centerline(left, right):
    """Pair ordered truth boundaries; never infer a route from perceived cones."""
    if len(left) != len(right) or len(left) < 2:
        raise ValueError('reference needs equally sized, complete truth boundaries')
    return [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(left, right)]


def world_geometry(filename, *, acceleration=False):
    """Read matching boundaries and every nominal cone from a generated world."""
    root = ET.parse(filename).getroot()
    sides = [{}, {}]
    cones = []
    for item in root.findall('./world/include'):
        name = item.findtext('name', '')
        if not name.startswith(('cone_blue_', 'cone_yellow_', 'cone_orange_')):
            continue
        pose = [float(value) for value in item.findtext('pose', '').split()]
        if len(pose) != 6 or not all(map(math.isfinite, pose)):
            raise ValueError('invalid world cone pose')
        kind = next((kind for kind in ('orange_large', 'orange_small', 'blue', 'yellow')
                     if name.startswith(f'cone_{kind}_')), None)
        if kind is None or abs(pose[3]) > 1e-9 or abs(pose[4]) > 1e-9:
            raise ValueError('world cone must have a known upright envelope')
        cones.append((name, pose[0], pose[1], pose[5], kind))
        if acceleration:
            # This generator's straight is along +x, centered at y=0. Color
            # does not encode the side of orange start/finish/exit cones.
            if pose[1] == 0:
                raise ValueError('acceleration boundary cone lies on the centerline')
            side, key = (0 if pose[1] > 0 else 1), pose[0]
        else:
            if name.startswith('cone_orange_'):
                continue
            side = 0 if name.startswith('cone_blue_') else 1
            key = int(name.rsplit('_', 1)[1])
        if key in sides[side]:
            raise ValueError('duplicate world boundary position or cone index')
        sides[side][key] = pose[:2]
    # Indices may have gaps where another model replaced a cone. Missing
    # partners must still fail, rather than zip unrelated boundary vertices.
    keys = sorted(sides[0])
    if keys != sorted(sides[1]):
        raise ValueError('world reference boundaries have mismatched partners')
    left, right = ([sides[side][key] for key in keys] for side in (0, 1))
    paired_centerline(left, right)  # Validate before publishing any geometry.
    return left, right, cones


def world_centerline(filename, *, acceleration=False):
    """Read the reference centerline, preserving the geometry-only API."""
    left, right, _ = world_geometry(filename, acceleration=acceleration)
    return paired_centerline(left, right)


class ReferenceTrack:
    """Project onto the segments of one latched, ordered truth polyline."""

    def __init__(self, points, closed=True):
        cleaned = []
        for point in points:
            point = tuple(map(float, point))
            if len(point) != 2 or not all(map(math.isfinite, point)):
                raise ValueError('reference coordinates must be finite XY pairs')
            if not cleaned or point != cleaned[-1]:
                cleaned.append(point)
        if len(cleaned) > 1 and cleaned[0] == cleaned[-1]:
            cleaned.pop()
        if len(cleaned) < (3 if closed else 2):
            raise ValueError('reference has too few distinct vertices')
        if closed:
            twice_area = sum(a[0]*b[1] - b[0]*a[1]
                             for a, b in zip(cleaned, cleaned[1:] + cleaned[:1]))
            if abs(twice_area) < 1e-9:
                raise ValueError('a closed reference must enclose nonzero area')
        self.points = tuple(cleaned)
        self.closed = closed
        self.segments = []
        self.length = 0.0
        ends = self.points[1:] + (self.points[:1] if closed else ())
        for start, end in zip(self.points, ends):
            dx, dy = end[0] - start[0], end[1] - start[1]
            length = math.hypot(dx, dy)
            self.segments.append((start, dx, dy, length, self.length))
            self.length += length
        self.sha256 = hashlib.sha256(json.dumps(
            {'points': self.points, 'closed': closed}, separators=(',', ':')).encode()).hexdigest()

    def project(self, x, y):
        """Return segment distance, arc coordinate, projected point and unit tangent."""
        best = None
        for index, ((ax, ay), dx, dy, length, arc) in enumerate(self.segments):
            fraction = min(1.0, max(0.0, ((x - ax)*dx + (y - ay)*dy) / length**2))
            qx, qy = ax + fraction*dx, ay + fraction*dy
            distance = math.hypot(x - qx, y - qy)
            if best is None or distance < best[0]:
                tx, ty = dx/length, dy/length
                neighbor = None
                if fraction < 1e-9 and (index > 0 or self.closed):
                    neighbor = self.segments[index-1]
                elif fraction > 1-1e-9 and (index+1 < len(self.segments) or self.closed):
                    neighbor = self.segments[(index+1) % len(self.segments)]
                if neighbor is not None:
                    nx, ny, nl = neighbor[1:4]
                    norm = math.hypot(tx + nx/nl, ty + ny/nl)
                    if norm > 1e-9:
                        tx, ty = (tx + nx/nl)/norm, (ty + ny/nl)/norm
                best = (distance, arc + fraction*length, (qx, qy), (tx, ty))
        return best


class CircuitProgress:
    """Require continuous on-course travel and a forward crossing of a fixed gate."""

    def __init__(self, track, *, min_fraction=.98, gate_half_width=2.0,
                 min_lap_time=5.0, start_speed=.1, max_gap=1.0, corridor=2.0, on_course=None):
        for value in (gate_half_width, max_gap, corridor):
            if not math.isfinite(value) or value <= 0:
                raise ValueError('gate width, observation gap and corridor must be positive')
        if (not 0 < min_fraction <= 1 or not math.isfinite(min_lap_time)
                or min_lap_time < 0 or not math.isfinite(start_speed) or start_speed < 0):
            raise ValueError('invalid lap progress or timing limits')
        self.track = track
        self.min_fraction, self.gate_half_width = min_fraction, gate_half_width
        self.min_lap_time, self.start_speed = min_lap_time, start_speed
        self.max_gap, self.corridor = max_gap, corridor
        self.on_course = on_course or (lambda x, y: self.track.project(x, y)[0] <= corridor)
        self.previous = self.gate = None
        self.started_at = self.completed_at = None
        self.progress = 0.0
        self.invalid_reason = ''

    def observe(self, x, y, now, speed, driving=True):
        """Observe a stamped pose; once continuity is lost this run cannot earn a lap."""
        if not all(map(math.isfinite, (x, y, now, speed))):
            self.invalid_reason = 'nonfinite_odometry'
            return False
        distance, arc, point, tangent = self.track.project(x, y)
        if self.previous is not None and now <= self.previous[2]:
            # Repeated simulation time (pause) earns neither time nor progress.
            if now < self.previous[2]:
                self.invalid_reason = 'backward_odometry_time'
            return False
        if self.gate is None:
            self.gate = (point, tangent)
        previous = self.previous
        self.previous = (x, y, now, arc)
        if self.started_at is None:
            if not driving or speed < self.start_speed:
                return False
            self.started_at = now
            return False
        if not driving:
            self.invalid_reason = 'left_driving_state'
        if self.completed_at is not None or self.invalid_reason or previous is None:
            return False
        px, py, stamp, prev_arc = previous
        if now - stamp > self.max_gap:
            self.invalid_reason = 'odometry_gap'
            return False
        ds = math.hypot(x - px, y - py)
        delta = arc - prev_arc
        if self.track.closed:
            delta = (delta + self.track.length/2) % self.track.length - self.track.length/2
        # Test the observed chord too: endpoint-only checks would credit a
        # shortcut across a circuit when odometry is sparse.
        steps = max(1, math.ceil(ds / (self.corridor/2)))
        on_course = self.on_course(x, y) and all(
            self.on_course(px + (x-px)*i/steps, py + (y-py)*i/steps)
            for i in range(steps))
        # At an offset corner the closest segment can switch across the
        # vertex without equal vehicle travel. Allow the two corridor offsets,
        # while still rejecting jumps to distant sections of the route.
        if abs(delta) > 2*(ds + self.corridor) + 1e-6:
            self.invalid_reason = 'discontinuous_progress'
            return False
        if not on_course:
            self.invalid_reason = 'off_course'
            return False
        self.progress += delta
        if not self.track.closed:
            return False
        (gx, gy), (tx, ty) = self.gate
        before, after = (px-gx)*tx + (py-gy)*ty, (x-gx)*tx + (y-gy)*ty
        if not before < 0 <= after:
            return False
        fraction = -before / (after-before)
        cx, cy = px + fraction*(x-px), py + fraction*(y-py)
        lateral = abs((cx-gx)*ty - (cy-gy)*tx)
        crossed_at = stamp + fraction*(now-stamp)
        crossed_progress = self.progress - (1-fraction)*delta
        if (lateral <= self.gate_half_width
                and crossed_progress >= self.min_fraction*self.track.length
                and crossed_at - self.started_at >= self.min_lap_time):
            self.completed_at = crossed_at
            return True
        return False
