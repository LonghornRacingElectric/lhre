"""Analytic course checks independent of planner paths, ROS clocks and wall time."""

import math

from lhr_metrics.reference import (
    CircuitProgress, paired_centerline, ReferenceTrack, world_centerline)
import pytest

SQUARE = [(0., 0.), (10., 0.), (10., 10.), (0., 10.)]


def circuit(reverse=False):
    points = SQUARE + [SQUARE[0]]
    if reverse:
        points = list(reversed(points))
    for a, b in zip(points, points[1:]):
        for index in range(10):
            t = index / 10
            yield a[0] + t*(b[0]-a[0]), a[1] + t*(b[1]-a[1])
    yield points[-1]


def test_polyline_distance_has_no_vertex_spacing_floor():
    track = ReferenceTrack(SQUARE)
    assert track.project(5., 0.)[0] == 0.0
    assert track.project(5., 1.)[0] == 1.0
    assert track.length == 40.
    assert len(track.sha256) == 64
    assert track.sha256 != ReferenceTrack([(0., 0.), (5., 0.), *SQUARE[1:]]).sha256


@pytest.mark.parametrize('points', [
    [], [(0., 0.)], [(0., 0.)]*3, [(0., 0.), (1., 0.), (2., 0.)],
    [(0., 0.), (math.nan, 0.), (1., 1.)],
])
def test_invalid_reference_is_rejected(points):
    with pytest.raises(ValueError):
        ReferenceTrack(points)


def test_repeated_vertices_and_explicit_loop_closure_are_normalized():
    assert ReferenceTrack([*SQUARE, SQUARE[0], SQUARE[0]]).sha256 == ReferenceTrack(SQUARE).sha256


def test_pairing_requires_complete_boundaries():
    with pytest.raises(ValueError):
        paired_centerline(SQUARE, SQUARE[:2])


@pytest.mark.parametrize('reverse', [False, True])
def test_only_forward_complete_circuit_counts(reverse):
    lap = CircuitProgress(ReferenceTrack(SQUARE))
    crossings = []
    for index, (x, y) in enumerate(circuit(reverse)):
        if lap.observe(x, y, 10. + index*.2, 5.):
            crossings.append(index)
    assert crossings == ([] if reverse else [40])
    if not reverse:
        assert lap.progress == pytest.approx(40.)
        assert lap.completed_at - lap.started_at == pytest.approx(8.)
        assert not lap.observe(1., 0., 18.2, 5.)


def test_near_start_revisit_does_not_count_as_a_circuit():
    lap = CircuitProgress(ReferenceTrack(SQUARE))
    for index, x in enumerate([0., 1., 2., 3., 4., 3., 2., 1., 0., -1., 0., 1.]):
        assert not lap.observe(x, 0., 10.+index, 1.)
    assert lap.completed_at is None


def test_teleport_and_missing_odometry_cannot_earn_progress():
    lap = CircuitProgress(ReferenceTrack(SQUARE))
    lap.observe(0., 0., 10., 5.)
    lap.observe(1., 0., 12., 5.)
    assert lap.invalid_reason == 'odometry_gap'
    for index, (x, y) in enumerate(circuit()):
        assert not lap.observe(x, y, 13.+index*.2, 5.)


def test_diagonal_shortcut_is_rejected_even_when_endpoints_are_on_course():
    lap = CircuitProgress(ReferenceTrack(SQUARE))
    lap.observe(10., 0., 10., 5.)
    assert not lap.observe(0., 10., 10.5, 5.)
    assert lap.invalid_reason == 'off_course_or_discontinuous_progress'


def test_waiting_and_paused_time_cannot_complete_a_lap():
    lap = CircuitProgress(ReferenceTrack(SQUARE))
    lap.observe(0., 0., 1., 0., driving=False)
    lap.observe(0., 0., 100., 0., driving=False)
    # Long pre-drive waits must not invalidate a fresh driving run either.
    lap.observe(0., 0., 100.1, 5.)
    assert lap.started_at == 100.1
    for x, y in circuit():
        assert not lap.observe(x, y, 100.1, 5.)
    assert lap.progress == 0.


def test_backward_clock_invalidates_the_run():
    lap = CircuitProgress(ReferenceTrack(SQUARE))
    lap.observe(0., 0., 10., 5.)
    lap.observe(1., 0., 9., 5.)
    assert lap.invalid_reason == 'backward_odometry_time'


def test_open_route_is_scored_without_a_fictitious_return_segment_or_lap():
    track = ReferenceTrack([(0., 0.), (10., 0.)], closed=False)
    lap = CircuitProgress(track)
    for index in range(11):
        assert not lap.observe(float(index), 0., 10.+index*.2, 5.)
    assert lap.progress == pytest.approx(10.)
    assert track.length == 10.


def test_world_reference_uses_the_actual_file_and_rejects_incomplete_cones(tmp_path):
    world = tmp_path / 'custom.sdf'
    includes = []
    for color, shift in [('blue', -1.), ('yellow', 1.)]:
        for index, (x, y) in enumerate(SQUARE):
            includes.append(f'<include><name>cone_{color}_{index}</name>'
                            f'<pose>{x} {y+shift} 0 0 0 0</pose></include>')
    world.write_text('<sdf><world>' + ''.join(includes) + '</world></sdf>')
    assert world_centerline(world) == SQUARE
    world.write_text('<sdf><world>' + ''.join(includes[:-1]) + '</world></sdf>')
    with pytest.raises(ValueError):
        world_centerline(world)


def test_offset_corner_can_switch_nearest_segments_without_a_false_jump():
    lap = CircuitProgress(ReferenceTrack(SQUARE))
    lap.observe(9., 0., 10., 5.)
    lap.observe(9., .9, 10.2, 5.)
    lap.observe(9., 1.1, 10.4, 5.)
    assert not lap.invalid_reason
    assert lap.progress == pytest.approx(2.1)


def test_jump_to_a_nearby_but_distant_course_section_is_rejected():
    track = ReferenceTrack([(0., 0.), (10., 0.), (10., .5), (0., .5)])
    lap = CircuitProgress(track)
    lap.observe(5., 0., 10., 5.)
    lap.observe(5., .5, 10.1, 5.)
    assert lap.invalid_reason == 'off_course_or_discontinuous_progress'


def test_progress_after_the_crossing_cannot_make_an_incomplete_lap_pass():
    lap = CircuitProgress(ReferenceTrack(SQUARE), min_fraction=1.)
    lap.observe(5., 0., 10., 0., driving=False)
    # The first half metre was not observed while driving and earns no credit.
    lap.observe(5.5, 0., 10.1, 5.)
    route = [(5.5, 0.), (10., 0.), (10., 10.), (0., 10.), (0., 0.), (4., 0.)]
    stamp = 10.1
    for a, b in zip(route, route[1:]):
        steps = round(math.hypot(b[0]-a[0], b[1]-a[1]) / .5)
        for index in range(1, steps+1):
            stamp += .1
            assert not lap.observe(a[0] + (b[0]-a[0])*index/steps,
                                   a[1] + (b[1]-a[1])*index/steps, stamp, 5.)
    # End-of-sample progress exceeds 40 m, but progress at the gate did not.
    assert not lap.observe(6., 0., stamp+.4, 5.)
    assert lap.progress > 40.
    assert lap.completed_at is None
