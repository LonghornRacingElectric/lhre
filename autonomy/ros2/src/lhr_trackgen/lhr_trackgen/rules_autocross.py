"""Piece-based closed courses and geometric checks for DD.4.4.1."""

import math
import random

from lhr_trackgen.cone_geometry import CONE_SPECS

# Circumscribed cone-base diameter is conservative at every boundary heading.
BASE = math.sqrt(2)*CONE_SPECS['blue'].base_width_m
# Reserve for chords between discrete cones and four-decimal SDF serialization.
CHORD_RESERVE = .10


def _edges(points):
    return list(zip(points, points[1:] + points[:1]))


def _point_segment(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0., min(1., ((p[0]-a[0])*dx + (p[1]-a[1])*dy) / (dx*dx+dy*dy)))
    return math.hypot(p[0]-a[0]-t*dx, p[1]-a[1]-t*dy)


def segment_distance(a, b, c, d):
    def cross(p, q, r):
        return (q[0]-p[0])*(r[1]-p[1]) - (q[1]-p[1])*(r[0]-p[0])
    if (cross(a, b, c)*cross(a, b, d) <= 0
            and cross(c, d, a)*cross(c, d, b) <= 0
            and max(min(a[0], b[0]), min(c[0], d[0])) <= min(max(a[0], b[0]), max(c[0], d[0]))
            and max(min(a[1], b[1]), min(c[1], d[1])) <= min(max(a[1], b[1]), max(c[1], d[1]))):
        return 0.
    return min(_point_segment(a, c, d), _point_segment(b, c, d),
               _point_segment(c, a, b), _point_segment(d, a, b))


def measure_route(center):
    """Measure the closed reference curve using the shared envelope convention."""
    if len(center) < 6 or any(len(p) != 2 or not all(map(math.isfinite, p)) for p in center):
        raise ValueError('reference needs at least six finite XY vertices')
    edges = _edges(center)
    lengths = [math.dist(a, b) for a, b in edges]
    if min(lengths) < 1e-6:
        raise ValueError('course has duplicate centerline vertices')
    lap = sum(lengths)
    radius = math.inf
    straight = longest = 0.
    # Start at a bend so a straight spanning index zero is counted once.
    bends = []
    for i, (a, b) in enumerate(edges):
        c = edges[(i+1) % len(edges)][1]
        cross = abs((b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]))
        if cross > 1e-8:
            radius = min(radius, math.dist(a, b)*math.dist(b, c)*math.dist(a, c)/(2*cross))
        bends.append(cross / (lengths[i]*lengths[(i+1) % len(edges)]) > 1e-5)
    start = next((i for i, bend in enumerate(bends) if bend), 0)
    for k in range(len(edges)):
        i = (start+1+k) % len(edges)
        straight += lengths[i]
        longest = max(longest, straight)
        if bends[i]:
            straight = 0.
    return {'lap_length_m': lap, 'longest_straight_m': longest, 'minimum_radius_m': radius}


def check_envelope(left, right):
    """
    Measure a closed paired layout; raise ValueError outside the rules envelope.

    Width is the minimum between boundary segments, less the circumscribed cone-base diameter.
    Radius is the centerline circumradius. Remote same-side segments also need
    a 0.5 m gap between cone bases; this separation is a simulator guard, not
    an additional event rule. All measurements include the closing edges.
    """
    if len(left) != len(right) or len(left) < 6:
        raise ValueError('course needs complete paired boundaries')
    if any(len(p) != 2 or not all(map(math.isfinite, p)) for p in left + right):
        raise ValueError('course coordinates must be finite XY pairs')
    center = [((a[0]+b[0])/2, (a[1]+b[1])/2) for a, b in zip(left, right)]
    measured = measure_route(center)
    lap, longest, radius = (measured[k] for k in (
        'lap_length_m', 'longest_straight_m', 'minimum_radius_m'))
    lengths = [math.dist(a, b) for a, b in _edges(center)]
    if not 200 <= lap <= 500:
        raise ValueError(f'lap length {lap:.3f} m outside 200..500')
    if longest > 80 + 1e-6:
        raise ValueError(f'straight {longest:.3f} m exceeds 80')
    if radius < 4.5 - 1e-6:
        raise ValueError(f'turn radius {radius:.3f} m below 4.5')
    boundary_edges = [_edges(left), _edges(right)]
    width = min(math.dist(a, b) for a, b in zip(left, right)) - BASE
    # A sweep discards distant edge pairs without approximating their distance.
    indexed = []
    arc = [0.]
    for length in lengths:
        arc.append(arc[-1]+length)
    for side, items in enumerate(boundary_edges):
        for i, (a, b) in enumerate(items):
            if math.dist(a, b) < 1e-6:
                raise ValueError('course has duplicate boundary vertices')
            indexed.append((min(a[0], b[0]), max(a[0], b[0]), side, i, a, b))
    indexed.sort()
    search_gap = width + BASE
    for k, (_, xmax, side, i, a, b) in enumerate(indexed):
        for xmin2, _, other, j, c, d in indexed[k+1:]:
            if xmin2 > xmax + search_gap:
                break
            if min(c[1], d[1]) > max(a[1], b[1])+search_gap or \
                    min(a[1], b[1]) > max(c[1], d[1])+search_gap:
                continue
            if side == other:
                delta = abs(arc[i]-arc[j])
                if min(delta, lap-delta) <= 2*max(3., width+BASE):
                    # Adjacent edges share endpoints; every other crossing is invalid.
                    if (i-j) % len(left) not in (0, 1, len(left)-1):
                        if segment_distance(a, b, c, d) < 1e-8:
                            raise ValueError('boundary crosses itself')
                    continue
                if segment_distance(a, b, c, d) < .5 + BASE:
                    raise ValueError('remote boundaries crowd each other')
            else:
                width = min(width, segment_distance(a, b, c, d)-BASE)
    if width < 3 - 1e-6:
        raise ValueError(f'clear width {width:.3f} m below 3 (or boundaries cross)')
    return {'lap_length_m': lap, 'longest_straight_m': longest,
            'minimum_radius_m': radius, 'minimum_clear_width_m': width}


def _lane(length, features, step=.12):
    """Straight pieces interleaved with smooth chicanes or slaloms."""
    points = []
    n = math.ceil(length/step)
    for i in range(n+1):
        x = length*i/n
        y = 0.
        for start, span, amplitude, cycles in features:
            t = (x-start)/span
            if 0 < t < 1:
                y += amplitude * math.sin(math.pi*t)**4 * math.sin(2*math.pi*cycles*t)
        points.append((x, y))
    return points


def _hairpin(radius, decreasing):
    """Integrate a symmetric half-turn, tightening toward its apex if requested."""
    # Symmetric curvature makes the end tangent reverse at the same x.
    n = 400
    mean_k = (.75 if decreasing else 1.)/radius
    ds = math.pi/mean_k/n
    x = y = heading = 0.
    points = [(x, y)]
    for i in range(n):
        t = (i+.5)/n
        k = (1 - .5*abs(2*t-1))/radius if decreasing else 1/radius
        angle = k*ds
        x += (math.sin(heading+angle)-math.sin(heading))/k
        y += (math.cos(heading)-math.cos(heading+angle))/k
        heading += angle
        points.append((x, y))
    return points


def _sample_boundaries(center, width, spacing):
    normals = []
    for i in range(len(center)):
        a, b = center[i-1], center[(i+1) % len(center)]
        dx, dy = b[0]-a[0], b[1]-a[1]
        norm = math.hypot(dx, dy)
        normals.append((-dy/norm, dx/norm))
    half = (width+BASE+CHORD_RESERVE)/2
    dense = [[(x+sign*half*nx, y+sign*half*ny)
              for (x, y), (nx, ny) in zip(center, normals)] for sign in (1, -1)]
    arc = [0.]
    for i in range(len(center)):
        arc.append(arc[-1]+max(math.dist(side[i], side[(i+1) % len(center)]) for side in dense))
    count = math.ceil(arc[-1]/min(spacing*.98, 2.))
    result = [[], []]
    j = 0
    for i in range(count):
        target = i*arc[-1]/count
        while arc[j+1] < target:
            j += 1
        fraction = (target-arc[j])/(arc[j+1]-arc[j])
        for side, out in zip(dense, result):
            a, b = side[j], side[(j+1) % len(center)]
            out.append(tuple(a[k]+fraction*(b[k]-a[k]) for k in (0, 1)))
    return result


def generate_rules_autocross_track(seed=0, width_m=3.5, cone_spacing_m=2., **_kw):
    """
    Generate a deterministic legal course, retrying rejected candidates.

    width_m is clear width, unlike the legacy autocross center spacing.
    Legacy radius/jitter/waypoint parameters do not control this style.
    """
    if not math.isfinite(width_m) or width_m < 3:
        raise ValueError('rules_autocross clear width must be at least 3 m')
    if not math.isfinite(cone_spacing_m) or cone_spacing_m <= 0:
        raise ValueError('cone spacing must be positive and finite')
    rng = random.Random(seed)
    error = ''
    for _ in range(100):
        length = rng.uniform(85., 210.)
        radius = rng.uniform(max(4.55, width_m+.5), max(10., width_m*2))
        turn = _hairpin(radius, decreasing=True)
        height = turn[-1][1]
        constant_turn = _hairpin(height/2, decreasing=False)
        lanes = []
        for _side in range(2):
            # No straight can bridge more than 80 m between features.
            pieces = []
            slots = max(2, math.ceil(length/110))
            for j in range(slots):
                span = rng.uniform(28., 36.)
                start = length*(j+.5)/slots-span/2
                pieces.append((start, span, rng.uniform(.6, 1.4), 1 if (j+_side) % 2 == 0 else 2))
            lanes.append(_lane(length, pieces))
        center = (lanes[0][:-1] + [(length+x, y) for x, y in turn[:-1]]
                  + [(length-x, height-y) for x, y in lanes[1][:-1]]
                  + [(-x, height-y) for x, y in constant_turn[:-1]])
        # Keep long courses centered under the existing origin-based geofence.
        origin = [(min(p[k] for p in center)+max(p[k] for p in center))/2 for k in (0, 1)]
        center = [(x-origin[0], y-origin[1]) for x, y in center]
        left, right = _sample_boundaries(center, width_m, cone_spacing_m)
        try:
            report = check_envelope(left, right)
            if report['minimum_clear_width_m'] < width_m:
                raise ValueError('cone chords narrow the requested clear width')
        except ValueError as rejected:
            error = str(rejected)
            continue
        return left, right
    raise ValueError(f'no valid rules_autocross course after 100 attempts: {error}')
