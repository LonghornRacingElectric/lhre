"""Checks on ray casting against the ground and cones."""

from lhr_lidar_sim.scene import (
    cast, CONE_BASE_RADIUS_M, CONE_HEIGHT_M, cone_ranges, ground_ranges)
import numpy as np
import pytest

SENSOR_Z = 0.55


def _dirs(vectors):
    arr = np.asarray(vectors, dtype=np.float64)
    return arr / np.linalg.norm(arr, axis=1, keepdims=True)


def _inside_cone(point, cone_x, cone_y, sensor_z):
    """
    Report whether a point lies inside the cone, independently.

    Deliberately does not reuse the quadratic: a brute-force march on
    this is what validates the analytic solve rather than restating it.
    """
    world_z = point[2] + sensor_z
    if world_z < 0.0 or world_z > CONE_HEIGHT_M:
        return False
    allowed = CONE_BASE_RADIUS_M * (1.0 - world_z / CONE_HEIGHT_M)
    return np.hypot(point[0] - cone_x, point[1] - cone_y) <= allowed


def test_ray_straight_down_measures_the_mount_height():
    ranges, hit = ground_ranges(_dirs([[0.0, 0.0, -1.0]]), SENSOR_Z)
    assert hit[0]
    assert ranges[0] == pytest.approx(SENSOR_Z)


def test_level_and_upward_rays_never_reach_the_ground():
    dirs = _dirs([[1.0, 0.0, 0.0], [1.0, 0.0, 0.5], [0.0, 0.0, 1.0]])
    _, hit = ground_ranges(dirs, SENSOR_Z)
    assert not hit.any()


def test_ground_range_grows_as_the_ray_flattens():
    # Only the bottom 7 degrees of the field of view can see the
    # ground at all, so this is a short reach: a few metres.
    dirs = _dirs([[np.cos(a), 0.0, np.sin(a)]
                  for a in np.radians([-7.0, -3.0, -1.0])])
    ranges, hit = ground_ranges(dirs, SENSOR_Z)
    assert hit.all()
    assert ranges[0] < ranges[1] < ranges[2]
    assert ranges[0] == pytest.approx(SENSOR_Z / np.sin(np.radians(7.0)))


def test_ray_aimed_at_the_apex_hits_it():
    cone_x, cone_y = 10.0, 0.0
    apex_z = CONE_HEIGHT_M - SENSOR_Z
    dirs = _dirs([[cone_x, cone_y, apex_z]])
    ranges, hit = cone_ranges(dirs, np.array([[cone_x, cone_y]]), SENSOR_Z)
    assert hit[0]
    # Tolerance in microns, not machine epsilon: the apex is a double
    # root, so the quadratic formula resolves it through the square
    # root of a clamped near-zero discriminant and loses precision
    # there. 1e-7 m on a 10 m range is not worth defending against.
    assert ranges[0] == pytest.approx(np.hypot(cone_x, apex_z), abs=1e-5)


def test_cone_directly_below_is_hit_at_the_apex():
    apex_z = CONE_HEIGHT_M - SENSOR_Z
    dirs = _dirs([[0.0, 0.0, -1.0]])
    ranges, hit = cone_ranges(dirs, np.array([[0.0, 0.0]]), SENSOR_Z)
    assert hit[0]
    assert ranges[0] == pytest.approx(abs(apex_z))


def test_analytic_hit_matches_a_brute_force_march():
    # The real validation: march along each ray in 1 mm steps and find
    # the first step inside the cone, using the cone's definition
    # rather than the quadratic. The two must agree to within a step.
    cone_x, cone_y = 6.0, 0.0
    cones = np.array([[cone_x, cone_y]])
    step = 0.001

    # Aim at points that are actually on the cone. Rays are built from
    # a target height and a lateral offset inside the cone's radius at
    # that height, otherwise they sail over a 0.325 m obstacle and the
    # test proves only that misses miss.
    rays = []
    for target_z in (0.05, 0.10, 0.15, 0.20, 0.25):
        radius_here = CONE_BASE_RADIUS_M * (1.0 - target_z / CONE_HEIGHT_M)
        for frac in (-0.5, 0.0, 0.5):
            rays.append([cone_x, cone_y + frac * radius_here,
                         target_z - SENSOR_Z])
    dirs = _dirs(rays)

    ranges, hit = cone_ranges(dirs, cones, SENSOR_Z)

    checked = 0
    for i in range(dirs.shape[0]):
        march = None
        for n in range(1, int(12.0 / step)):
            t = n * step
            if _inside_cone(dirs[i] * t, cone_x, cone_y, SENSOR_Z):
                march = t
                break
        assert hit[i] == (march is not None), f'ray {i} disagrees on hit'
        if march is not None:
            assert ranges[i] == pytest.approx(march, abs=2.0 * step)
            checked += 1
    # Guard against the case passing because nothing was hit at all.
    # Every ray was aimed at the cone, so every ray must have hit it.
    assert checked == dirs.shape[0]


def test_no_hit_above_the_apex_or_mirrored_below_the_ground():
    # An infinite double cone would answer both of these; a real cone
    # does not. This is what the z bounds in the solver are for.
    cones = np.array([[8.0, 0.0]])
    upward = _dirs([[8.0, 0.0, 2.0]])
    _, hit_up = cone_ranges(upward, cones, SENSOR_Z)
    assert not hit_up.any()

    steep = _dirs([[8.0, 0.0, -8.0]])
    ranges, hit_down = cone_ranges(steep, cones, SENSOR_Z)
    if hit_down.any():
        point = steep[0] * ranges[0]
        assert point[2] + SENSOR_Z >= -1e-9


def test_a_cone_occludes_the_ground_behind_it():
    # This ray would reach the ground at about 10 m, but passes through
    # the cone at 5 m on the way, so the cone has to win.
    cones = np.array([[5.0, 0.0]])
    dirs = _dirs([[10.0, 0.0, -SENSOR_Z]])

    ground_r, ground_hit = ground_ranges(dirs, SENSOR_Z)
    assert ground_hit[0]

    ranges, hit, on_cone = cast(dirs, cones, SENSOR_Z)
    assert hit[0]
    assert on_cone[0], 'the cone must be the surface that won'
    assert ranges[0] < ground_r[0]
    assert ranges[0] == pytest.approx(5.0, abs=0.3)


def test_cones_beyond_max_range_are_skipped():
    cones = np.array([[50.0, 0.0]])
    apex_z = CONE_HEIGHT_M - SENSOR_Z
    dirs = _dirs([[50.0, 0.0, apex_z]])
    _, hit = cone_ranges(dirs, cones, SENSOR_Z, max_range=40.0)
    assert not hit.any()


def test_no_cones_means_ground_only():
    dirs = _dirs([[10.0, 0.0, -SENSOR_Z]])
    ranges, hit, on_cone = cast(dirs, np.empty((0, 2)), SENSOR_Z)
    ground_r, _ = ground_ranges(dirs, SENSOR_Z)
    assert hit[0]
    assert not on_cone.any()
    assert ranges[0] == pytest.approx(ground_r[0])
