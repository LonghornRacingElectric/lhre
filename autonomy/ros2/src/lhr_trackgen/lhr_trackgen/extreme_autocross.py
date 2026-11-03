"""Fixed legal autocross courses at the edges of the DD.4.4.1 envelope."""

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


def _poses(name, length):
    tight = ('extreme_hairpin', 'extreme_doubleback', 'extreme_combined', 'extreme_decreasing')
    radius = 4.5 if name in tight else 9.
    turn = _turn(radius, decreasing=name == 'extreme_decreasing')
    height = turn[-1][1]
    back_turn = _turn(height/2)
    bottom, top = _pieces(name, length)
    points = (_lane(length, bottom)[:-1]
              + [(length+x, y, h) for x, y, h in turn[:-1]]
              + [(length-x, height+y, math.pi-h) for x, y, h in _lane(length, top)[:-1]]
              + [(-x, height-y, math.pi+h) for x, y, h in back_turn[:-1]])
    origin = [(min(p[k] for p in points)+max(p[k] for p in points))/2 for k in (0, 1)]
    return [(x-origin[0], y-origin[1], h) for x, y, h in points]


def _length(poses):
    return sum(math.hypot(a[0]-b[0], a[1]-b[1])
               for a, b in zip(poses, poses[1:]+poses[:1]))


@lru_cache(maxsize=len(SCENARIOS))
def _layout(name):
    if name not in SCENARIOS:
        raise ValueError(f'unknown extreme course {name!r}')
    length = {'extreme_hairpin': 80., 'extreme_slalom_chicane': 100.,
              'extreme_doubleback': 95., 'extreme_combined': 150.}.get(name, 85.)
    if name == 'extreme_500m':
        # Target the actual polyline that metrics scores, just inside 500 m.
        low, high = 180., 240.
        for _ in range(50):
            length = (low+high)/2
            if _length(_poses(name, length)) < 500-1e-5:
                low = length
            else:
                high = length
        length = low
    poses = _poses(name, length)
    width = 3. if name in ('extreme_narrow', 'extreme_combined') else 3.5
    # Analytic tangents, with <=0.10 rad heading intervals on circular turns.
    # The reserve keeps the conservative clear width within 1 cm of nominal.
    half = (width+BASE)/(2*math.cos(.05))+.0001
    left, right = [], []
    for x, y, heading in poses:
        nx, ny = -math.sin(heading), math.cos(heading)
        left.append((x+half*nx, y+half*ny))
        right.append((x-half*nx, y-half*ny))
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
