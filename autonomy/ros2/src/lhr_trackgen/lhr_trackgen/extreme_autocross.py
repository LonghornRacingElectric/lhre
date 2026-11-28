"""Fixed legal autocross courses at the edges of the DD.4.4.1 envelope."""

import bisect
from functools import lru_cache
import math

from lhr_trackgen.rules_autocross import BASE, check_envelope

SCENARIOS = (
    'extreme_narrow', 'extreme_hairpin', 'extreme_decreasing',
    'extreme_slalom_chicane', 'extreme_doubleback', 'extreme_500m', 'extreme_combined',
)


def _profile(x, features):
    y = slope = 0.
    for start, span, amplitude, cycles in features:
        t = (x-start)/span
        if not 0 < t < 1:
            continue
        s, c = math.sin(math.pi*t), math.cos(math.pi*t)
        wave = math.sin(2*math.pi*cycles*t) if cycles else 1.
        derivative = 2*math.pi*cycles*math.cos(2*math.pi*cycles*t) if cycles else 0.
        y += amplitude*s**4*wave
        slope += amplitude*(4*math.pi*s**3*c*wave+s**4*derivative)/span
    return y, math.atan(slope)


def _lane(length, features):
    # Preserve every feature endpoint, including the exact 80 m approach.
    knots = sorted({0., length, *(start for start, *_ in features),
                    *(start+span for start, span, *_ in features)})
    points = []

    def sample(a, b):
        mid = (a+b)/2
        ya, ha = _profile(a, features)
        yb, hb = _profile(b, features)
        ym, hm = _profile(mid, features)
        if (math.hypot(b-a, yb-ya) > 1.5 or max(abs(ha-hm), abs(hb-hm)) > .04
                or abs(ym-(ya+yb)/2) > .01):
            sample(a, mid)
            sample(mid, b)
        else:
            points.append((a, ya, ha))

    for a, b in zip(knots, knots[1:]):
        sample(a, b)
    return points + [(length, 0., 0.)]


def _turn(radius, decreasing=False):
    # A symmetric curvature profile reverses direction at the same x.
    mean_k = (.75 if decreasing else 1.)/radius
    length = math.pi/mean_k
    count = 2*math.ceil(length/(radius*.10)/2)
    ds = length/count
    x = y = heading = 0.
    points = [(x, y, heading)]
    for i in range(count):
        t = (i+.5)/count
        k = (1-.5*abs(2*t-1))/radius if decreasing else 1/radius
        angle = k*ds
        x += (math.sin(heading+angle)-math.sin(heading))/k
        y += (math.cos(heading)-math.cos(heading+angle))/k
        heading += angle
        points.append((x, y, heading))
    return points


def _pieces(name, length):
    if name == 'extreme_hairpin':
        return [], [(0., length, 20., 0)]
    if name == 'extreme_slalom_chicane':
        return [(25., 50., 3.1, 2)], [(30., 40., 2.5, 1)]
    if name == 'extreme_combined':
        return [(30., 40., 1., 1)], [(15., 50., 1.5, 2), (90., 40., 1.5, 1)]
    if name == 'extreme_500m':
        return ([(length/3-16, 32., 1., 1), (2*length/3-16, 32., 1., 2)],
                [(length/3-16, 32., 1., 2), (2*length/3-16, 32., 1., 1)])
    return [(length/2-18., 36., .8, 1)], [(length/2-18., 36., .8, 1)]


def _poses(name, length, *, anchors=False):
    tight = ('extreme_hairpin', 'extreme_doubleback', 'extreme_combined', 'extreme_decreasing')
    radius = 4.5 if name in tight else 9.
    turn = _turn(radius, decreasing=name == 'extreme_decreasing')
    height = turn[-1][1]
    back_turn = _turn(height/2)
    bottom, top = _pieces(name, length)
    lower, upper = _lane(length, bottom)[:-1], _lane(length, top)[:-1]
    sections = [lower, [(length+x, y, h) for x, y, h in turn[:-1]],
                [(length-x, height+y, math.pi-h) for x, y, h in upper],
                [(-x, height-y, math.pi+h) for x, y, h in back_turn[:-1]]]
    knots = [0, len(lower), len(lower)+len(turn)-1,
             len(lower)+len(turn)-1+len(upper)]
    for lane, features, base in ((lower, bottom, 0), (upper, top, knots[2])):
        endpoints = {start for start, *_ in features} | {
            start+span for start, span, *_ in features}
        knots.extend(base+i for i, (x, _, _) in enumerate(lane) if x in endpoints)
    points = [point for section in sections for point in section]
    origin = [(min(p[k] for p in points)+max(p[k] for p in points))/2 for k in (0, 1)]
    points = [(x-origin[0], y-origin[1], h) for x, y, h in points]
    return (points, sorted(set(knots))) if anchors else points


def _sample_layout(name, length, width):
    """Space physical cones along the longer boundary, keeping exact piece junctions."""
    poses, anchors = _poses(name, length, anchors=True)
    # Outer-boundary stations stay below 2 m; the reserve protects the road
    # between their chords, including the 4.5 m turns on the narrow fixture.
    # The narrow-only fixture has 9 m turns; the others can approach 4.5 m.
    half_angle = .10 if name == 'extreme_narrow' else .17
    half = (width+BASE)/(2*math.cos(half_angle))+.0001
    dense = [[(x-sign*half*math.sin(h), y+sign*half*math.cos(h))
              for x, y, h in poses] for sign in (1, -1)]
    arc = [0.]
    for i in range(len(poses)):
        arc.append(arc[-1]+max(math.dist(side[i], side[(i+1) % len(poses)]) for side in dense))
    left, right = [], []
    for start, end in zip(anchors, anchors[1:]+[len(poses)]):
        count = math.ceil((arc[end]-arc[start])/1.95)
        for station in range(count):
            target = arc[start]+station*(arc[end]-arc[start])/count
            i = min(len(poses)-1, bisect.bisect_right(arc, target)-1)
            fraction = (target-arc[i])/(arc[i+1]-arc[i])
            a, b = poses[i], poses[(i+1) % len(poses)]
            angle = math.atan2(math.sin(b[2]-a[2]), math.cos(b[2]-a[2]))
            x, y = (a[k]+fraction*(b[k]-a[k]) for k in (0, 1))
            if abs(angle) > 1e-5:
                # Circular interpolation preserves the analytic hairpin radius;
                # linear interpolation would cut inside it at every station.
                dx, dy = b[0]-a[0], b[1]-a[1]
                scale = 1/(2*math.tan(angle/2))
                cx, cy = (a[0]+b[0])/2-dy*scale, (a[1]+b[1])/2+dx*scale
                c, s = math.cos(fraction*angle), math.sin(fraction*angle)
                x, y = cx+c*(a[0]-cx)-s*(a[1]-cy), cy+s*(a[0]-cx)+c*(a[1]-cy)
            heading = a[2]+fraction*angle
            nx, ny = -math.sin(heading), math.cos(heading)
            left.append((x+half*nx, y+half*ny))
            right.append((x-half*nx, y-half*ny))
    return left, right


def _length(poses):
    return sum(math.hypot(a[0]-b[0], a[1]-b[1])
               for a, b in zip(poses, poses[1:]+poses[:1]))


@lru_cache(maxsize=len(SCENARIOS))
def _layout(name):
    if name not in SCENARIOS:
        raise ValueError(f'unknown extreme course {name!r}')
    length = {'extreme_hairpin': 80., 'extreme_slalom_chicane': 100.,
              'extreme_doubleback': 95., 'extreme_combined': 150.}.get(name, 85.)
    width = 3. if name in ('extreme_narrow', 'extreme_combined') else 3.5
    if name == 'extreme_500m':
        # Target the actual polyline that metrics scores, just inside 500 m.
        low, high = 180., 240.
        for _ in range(50):
            length = (low+high)/2
            left, right = _sample_layout(name, length, width)
            center = [((a[0]+b[0])/2, (a[1]+b[1])/2) for a, b in zip(left, right)]
            if _length(center) < 500-1e-5:
                low = length
            else:
                high = length
        length = low
    left, right = _sample_layout(name, length, width)
    check_envelope(left, right)
    return tuple(left), tuple(right)


def generate_extreme_track(name, seed=1, **_kw):
    """Return fresh lists for a fixed named world; seed 1 is its only identity."""
    if seed != 1:
        raise ValueError('named extreme courses require seed 1')
    left, right = _layout(name)
    return list(left), list(right)


def extreme_pairing_width(name):
    """Return the nominal cone-center separation of the fixed course."""
    left, right = _layout(name)
    return math.dist(left[0], right[0])


def extreme_spawn(name):
    """Stage before the complete first feature, rather than midway through it."""
    left, right = _layout(name)
    return ((left[0][0]+right[0][0])/2, (left[0][1]+right[0][1])/2, 0.)
