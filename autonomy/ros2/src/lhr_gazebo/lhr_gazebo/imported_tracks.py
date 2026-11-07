"""Preserve ordered source cones and derive a separate, checked scoring reference."""

import csv
import hashlib
import json
import math
from pathlib import Path

from lhr_metrics.course import CourseEnvelope
from lhr_trackgen.rules_autocross import BASE, measure_route, segment_distance
from lhr_vehicle import load_vehicle

COURSES = ('amz_fsg', 'amz_fsi')
KINDS = ('blue', 'yellow', 'orange_small', 'orange_large')
SCHEMA = 'lhre.imported-course.v1'
PHYSICS_NAME = 'lhr_imported_reference_v1'


def digest(path):
    """Identify exact source/world bytes, independently of file location."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_cones(path):
    """Read metres and canonical colors; per-color row order defines the boundaries."""
    with Path(path).open(newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ['x', 'y', 'color']:
            raise ValueError('cone CSV header must be x,y,color')
        result = []
        seen = set()
        for row in reader:
            x, y = float(row['x']), float(row['y'])
            kind = row['color']
            if kind not in KINDS or not all(map(math.isfinite, (x, y))):
                raise ValueError('cone needs finite metre coordinates and a known color')
            point = (round(x, 12), round(y, 12))
            if point in seen:
                raise ValueError('duplicate cone position; remove a closing vertex explicitly')
            seen.add(point)
            result.append((*point, kind))
    if any(sum(k == side for _, _, k in result) < 6 for side in ('blue', 'yellow')):
        raise ValueError('closed import needs at least six cones per boundary')
    return result


def edges(points):
    """Include the closure exactly once, without a duplicated closing cone."""
    return list(zip(points, points[1:]+points[:1]))


def _ordered(points, spawn):
    start = min(range(len(points)), key=lambda i: math.dist(points[i], spawn[:2]))
    indices = list(range(start, len(points)))+list(range(start))
    before, after = points[indices[-1]], points[indices[1]]
    if ((after[0]-before[0])*math.cos(spawn[2])
            + (after[1]-before[1])*math.sin(spawn[2])) < 0:
        indices = indices[:1]+indices[:0:-1]
    return indices


def reference_pairs(left, right, spawn):
    """Align ordered rings monotonically; repeat indices instead of inventing cones."""
    li, ri = _ordered(left, spawn), _ordered(right, spawn)
    n, m = len(left), len(right)
    cost = [[math.inf]*(m+1) for _ in range(n+1)]
    cost[0][0] = 0.
    previous = {}
    for i in range(n+1):
        for j in range(m+1):
            if i == j == 0:
                continue
            value, a, b = min((cost[a][b], a, b)
                              for a, b in ((i-1, j), (i, j-1), (i-1, j-1)) if a >= 0 and b >= 0)
            cost[i][j] = value+math.dist(left[li[i % n]], right[ri[j % m]])**2
            previous[i, j] = a, b
    states = []
    i, j = n, m
    while i or j:
        states.append([li[i % n], ri[j % m]])
        i, j = previous[i, j]
    states.append([li[0], ri[0]])
    return list(reversed(states))[:-1]


def reference_curve(left, right, pairs):
    """Resample gate midpoints at 0.5 m and smooth at a fixed 2 m spatial scale."""
    import numpy as np
    from scipy.ndimage import gaussian_filter1d

    mids = [((left[i][0]+right[j][0])/2, (left[i][1]+right[j][1])/2) for i, j in pairs]
    lengths = [math.dist(a, b) for a, b in edges(mids)]
    if min(lengths) < 1e-8:
        raise ValueError('ambiguous reference pairing has duplicate midpoints')
    arc = np.r_[0., np.cumsum(lengths)]
    samples = np.linspace(0., arc[-1], math.ceil(arc[-1]/.5), endpoint=False)
    raw = np.array([np.interp(samples, arc, [p[k] for p in mids]+[mids[0][k]])
                    for k in (0, 1)]).T
    smoothed = gaussian_filter1d(raw, 2./(arc[-1]/len(samples)), axis=0, mode='wrap')
    return [[round(float(v), 12) for v in row] for row in smoothed]


def audit(left, right, route):
    """Flag the same length/straight/radius/width envelope as rules_autocross."""
    measured = measure_route(route)
    sides = [edges(left), edges(right)]
    measured['minimum_clear_width_m'] = min(
        segment_distance(a, b, c, d) for a, b in sides[0] for c, d in sides[1])-BASE
    measured['maximum_cone_spacing_m'] = max(math.dist(a, b) for side in sides for a, b in side)
    violations = []
    if not 200 <= measured['lap_length_m'] <= 500:
        violations.append('lap length outside 200..500 m')
    if measured['longest_straight_m'] > 80+1e-6:
        violations.append('straight exceeds 80 m')
    if measured['minimum_radius_m'] < 4.5-1e-6:
        violations.append('reference turn radius below 4.5 m')
    if measured['minimum_clear_width_m'] < 3-1e-6:
        violations.append('conservative cone-base clear width below 3 m')
    for side in sides:
        if any(segment_distance(a, b, c, d) < 1e-9
               for i, (a, b) in enumerate(side) for j, (c, d) in enumerate(side)
               if j > i+1 and (i, j) != (0, len(side)-1)):
            violations.append('boundary self-intersection')
            break
        arc = [0.]
        for a, b in side:
            arc.append(arc[-1]+math.dist(a, b))
        if any(segment_distance(a, b, c, d) < .5+BASE
               for i, (a, b) in enumerate(side) for j, (c, d) in enumerate(side) if j > i
               and min(arc[j]-arc[i], arc[-1]-arc[j]+arc[i]) > 2*max(
                   3., measured['minimum_clear_width_m']+BASE)):
            violations.append('remote boundaries crowd each other')
            break
    return {'measurements': measured, 'violations': violations,
            'status': 'outside_envelope' if violations else 'within_envelope'}


def build_reference(cones, spawn):
    """Reject an unsafe inferred route; preserve every physical cone separately."""
    left, right = [[(x, y) for x, y, kind in cones if kind == side] for side in ('blue', 'yellow')]
    pairs = reference_pairs(left, right, spawn)
    route = reference_curve(left, right, pairs)
    paired = [[side[p[k]] for p in pairs] for k, side in enumerate((left, right))]
    course = CourseEnvelope(*paired, [], load_vehicle(), True)
    for a, b in edges(route):
        for i in range(max(1, math.ceil(math.dist(a, b)/.05))+1):
            fraction = i/max(1, math.ceil(math.dist(a, b)/.05))
            if not course.contains(a[0]+fraction*(b[0]-a[0]), a[1]+fraction*(b[1]-a[1])):
                raise ValueError('derived reference leaves the source road; correct the input')
    report = audit(left, right, route)
    widths = sorted(math.dist(left[i], right[j]) for i, j in pairs)
    return {'pairs': pairs, 'centerline': route, 'rules': report,
            'nominal_width_m': widths[len(pairs)//2]}


def checked_spawn(cones, reference, requested, fit=False):
    """Require the canonical car to fit; optionally align at a nearby scoring vertex."""
    sides = [[(x, y) for x, y, kind in cones if kind == side] for side in ('blue', 'yellow')]
    paired = [[side[p[k]] for p in reference['pairs']] for k, side in enumerate(sides)]
    physical = [(str(i), x, y, 0., kind) for i, (x, y, kind) in enumerate(cones)]
    course = CourseEnvelope(*paired, physical, load_vehicle(), True)
    if not fit:
        outside, hits = course.pose_check(*requested, padding=.05)
        if outside or hits:
            raise ValueError('requested spawn does not fit the car; supply --spawn or --fit-spawn')
        return list(requested)
    route = reference['centerline']
    candidates = sorted(range(len(route)), key=lambda i: math.dist(route[i], requested[:2]))
    for i in candidates:
        x, y = route[i]
        if math.dist((x, y), requested[:2]) > 5.:
            break
        before, after = route[i-1], route[(i+1) % len(route)]
        yaw = math.atan2(after[1]-before[1], after[0]-before[0])
        outside, hits = course.pose_check(x, y, yaw, padding=.05)
        if not outside and not hits:
            return [x, y, yaw]
    raise ValueError('no clear spawn within 5 m of requested pose; supply a suitable --spawn')


def course_world(name):
    """Find an installed/source fixed world without relying on working directory."""
    if name not in COURSES:
        raise ValueError(f'unknown competition course {name!r}')
    source = Path(__file__).resolve().parents[1]/'worlds'/f'{name}.sdf'
    if source.is_file():
        return source
    from ament_index_python.packages import get_package_share_directory
    return Path(get_package_share_directory('lhr_gazebo'))/'worlds'/f'{name}.sdf'


def main():
    """Emit gate audit metadata and require explicit acknowledgement of flagged layouts."""
    import argparse
    from lhr_metrics.reference import imported_reference
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('names', nargs='+', choices=COURSES)
    parser.add_argument('--allow-noncompliant', action='store_true')
    args = parser.parse_args()
    result = {}
    for name in args.names:
        data = imported_reference(course_world(name))
        if data['rules']['violations'] and not args.allow_noncompliant:
            parser.error(f'{name}: {data["rules"]["violations"]}; '
                         'acknowledge with ALLOW_NONCOMPLIANT=1')
        result[name] = data['rules']
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
