"""
Checks on the checked-in CAD mesh and its agreement with vehicle.yaml.

The mesh is the one input here that nothing else validates. It arrives as
opaque binary from a CAD export, and the ways it goes wrong are quiet: an
export in millimetres, a part-local origin instead of the assembly's, a
Y-up tool convention. None of those raise anything. They just draw a car
in the wrong place, or a car 1000 times too big, and the viewer is the
first thing to notice.

So these tests re-derive the frame from the triangles rather than trusting
the file, and compare it against the numbers vehicle_viz actually uses to
place it.
"""

import struct
from pathlib import Path

from lhr_vehicle import load_vehicle
import pytest

MESH = Path(__file__).resolve().parents[1] / 'meshes' / 'orion.stl'

# Only the four wheels are compared this tightly, and they are appended to
# the mesh at full resolution, so their vertices are exact CAD values. The
# body around them is quantized to a 20 mm grid, which is the reason the
# frame checks below are built on the wheels and not on the bodywork.
TOL_M = 0.001


def _triangles():
    """Parse the binary STL into a list of three-vertex tuples."""
    raw = MESH.read_bytes()
    count = struct.unpack('<I', raw[80:84])[0]
    assert len(raw) == 84 + 50 * count, 'truncated or ASCII STL'
    out = []
    for i in range(count):
        base = 84 + 50 * i + 12          # skip the per-facet normal
        v = struct.unpack('<9f', raw[base:base + 36])
        out.append((v[0:3], v[3:6], v[6:9]))
    return out


@pytest.fixture(scope='module')
def tris():
    return _triangles()


@pytest.fixture(scope='module')
def bounds(tris):
    pts = [p for t in tris for p in t]
    return (
        tuple(min(p[i] for p in pts) for i in range(3)),
        tuple(max(p[i] for p in pts) for i in range(3)),
    )


def _bodies(tris):
    """Split the mesh into connected components, as vertex-position sets."""
    index = {}
    faces = []
    for tri in tris:
        faces.append([index.setdefault(tuple(round(c, 6) for c in p),
                                       len(index)) for p in tri])
    parent = list(range(len(index)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b, c in faces:
        for other in (b, c):
            ra, rb = find(a), find(other)
            if ra != rb:
                parent[rb] = ra

    groups = {}
    for point, i in index.items():
        groups.setdefault(find(i), []).append(point)
    return list(groups.values())


def test_mesh_is_present_and_binary(tris):
    assert len(tris) > 1000


def test_mesh_is_in_metres(bounds):
    """A millimetre export is the classic failure and is 1000x too big."""
    lo, hi = bounds
    length, width, height = (hi[i] - lo[i] for i in range(3))
    assert 2.0 < length < 4.0, f'car {length} long: wrong units?'
    assert 1.0 < width < 2.0, f'car {width} wide: wrong units?'
    assert 0.5 < height < 2.0, f'car {height} tall: wrong units?'


def test_mesh_is_z_up_and_sits_on_the_ground(bounds):
    """Z is height, and CAD z=0 is the ground plane the wheels touch."""
    lo, hi = bounds
    assert abs(lo[2]) < 0.05, 'the car does not sit on z=0'
    assert hi[2] - lo[2] < hi[0] - lo[0], 'taller than it is long: Y-up?'


def test_mesh_is_symmetric_about_the_centreline(bounds):
    lo, hi = bounds
    assert abs(lo[1] + hi[1]) < 0.01, 'car is not centred on y=0'


def test_cad_origin_is_the_front_axle(bounds):
    """
    The whole placement rests on this.

    vehicle_viz translates the mesh forward by exactly one wheelbase, and
    that is only correct if the CAD origin is the front axle. The car must
    therefore extend well behind x=0 and only a nose ahead of it.
    """
    veh = load_vehicle()
    lo, hi = bounds
    assert lo[0] < -veh.wheelbase_m, 'nothing behind the rear axle'
    assert 0.5 < hi[0] < 1.5, 'nose is not where a front overhang would be'


def test_wheels_agree_with_vehicle_yaml(tris):
    """
    Re-derive wheelbase and track from the mesh and compare.

    The four tyres are separate bodies with square x/z boxes, which makes
    them findable without knowing where they ought to be. If this drifts,
    the mesh and the numbers driving the sim describe different cars.
    """
    veh = load_vehicle()
    centres = []
    for body in _bodies(tris):
        size = [max(p[i] for p in body) - min(p[i] for p in body)
                for i in range(3)]
        if (abs(size[0] - size[2]) < 0.01 and size[1] < 0.3
                and 0.3 < size[0] < 0.5):
            centres.append(tuple(
                (max(p[i] for p in body) + min(p[i] for p in body)) / 2.0
                for i in range(3)))

    assert len(centres) == 4, f'expected 4 tyres, found {len(centres)}'

    xs = sorted({round(c[0], 3) for c in centres})
    ys = sorted({round(c[1], 3) for c in centres})
    assert len(xs) == 2 and len(ys) == 2, 'tyres are not on a rectangle'
    assert abs((xs[1] - xs[0]) - veh.wheelbase_m) < TOL_M
    assert abs((ys[1] - ys[0]) - veh.track_m) < TOL_M

    # The front pair is what pins the origin, so check it directly.
    assert abs(max(xs)) < TOL_M, 'front axle is not at CAD x=0'


def test_lidar_mount_clears_the_bodywork_ahead_of_it(tris):
    """
    The sensor must not be looking through its own nose.

    At the original ASSUMED height of 0.55 m it was. A ray straight up
    from the mount station crosses the bodywork at z = 0.068, 0.086,
    0.086 and 0.583, so at 0.55 an odd number of surfaces lie below the
    sensor and it is enclosed; at 0.62 it is outside.

    The Mid-360's lowest beam is -7 degrees, so a point at horizontal
    distance d and height z blocks it unless the mount is at least
    z + d*tan(7 deg). Checked over the forward arc only, since nothing
    asks a forward-looking sensor to see the ground behind the car.
    """
    import math

    veh = load_vehicle()
    mx, my, mz = veh.lidar_position_m
    tan_low = math.tan(math.radians(7.0))

    required = 0.0
    for tri in tris:
        for px, py, pz in tri:
            # into base_link, the frame the mount is given in
            bx = px + veh.wheelbase_m
            d = math.hypot(bx - mx, py - my)
            if d < 0.05:
                continue
            if bx - mx < -0.05:          # behind the sensor
                continue
            required = max(required, pz + d * tan_low)

    assert mz >= required, (
        f'lidar at z={mz} is inside or behind bodywork; '
        f'needs z >= {required:.3f} to clear the forward arc')
    # Height costs near-ground range, so flag a mount raised far past
    # what clearance needs rather than only one that is too low.
    assert mz < required + 0.15, (
        f'lidar at z={mz} is {mz - required:.3f} m above the clearance '
        f'bound; the blind radius z/tan(7 deg) grows with height')


def test_placed_mesh_puts_its_wheels_where_the_primitives_draw_them(tris):
    """
    The mesh and the fallback primitives must describe the same car.

    use_mesh flips between them at runtime, so a viewer switching modes
    should see the car stay put rather than jump.
    """
    veh = load_vehicle()
    expected = {
        (0.0, round(veh.half_track_m, 3)),
        (0.0, round(-veh.half_track_m, 3)),
        (round(veh.wheelbase_m, 3), round(veh.half_track_m, 3)),
        (round(veh.wheelbase_m, 3), round(-veh.half_track_m, 3)),
    }
    found = set()
    for body in _bodies(tris):
        size = [max(p[i] for p in body) - min(p[i] for p in body)
                for i in range(3)]
        if (abs(size[0] - size[2]) < 0.01 and size[1] < 0.3
                and 0.3 < size[0] < 0.5):
            # vehicle_viz's marker pose, applied here.
            x = (max(p[0] for p in body) + min(p[0] for p in body)) / 2.0
            y = (max(p[1] for p in body) + min(p[1] for p in body)) / 2.0
            found.add((round(x + veh.wheelbase_m, 3), round(y, 3)))
    assert found == expected
