"""Repeated geometry must retain lap identity and require real forward progress."""

import math

from lhr_track_builder.skidpad_route import ROUTE_DEFAULTS, SkidpadRoute
from lhr_trackgen.skidpad_geometry import generate_skidpad_lanes
import numpy as np
import pytest


def _route(layout=(0, 0, 0)):
    small, large = generate_skidpad_lanes()
    c, s = math.cos(layout[2]), math.sin(layout[2])
    rotation = np.array([[c, -s], [s, c]])
    position = np.array([0, -13.5]) @ rotation.T + layout[:2]
    pose = np.r_[position, layout[2] + math.pi / 2]
    route = SkidpadRoute(np.array(layout), pose, np.array(small) @ rotation.T + layout[:2],
                         np.array(large) @ rotation.T + layout[:2], 1.875, 0.682,
                         ROUTE_DEFAULTS)
    return route


@pytest.mark.parametrize('layout', [(0, 0, 0), (20, -10, 1.3)])
def test_ordered_route_completes_four_laps_and_stops(layout):
    route = _route(layout)
    now = 0.0
    phases = []
    for index, point in enumerate(route.points):
        delta = route.points[min(index + 1, len(route.points) - 1)] - point
        heading = math.atan2(delta[1], delta[0]) if np.linalg.norm(delta) else math.pi / 2
        pose = np.r_[point @ route.rotation.T + layout[:2], heading + layout[2]]
        now += 0.1
        route.update(pose, 2.0, 0.1, now, generate_skidpad_lanes()[0]
                     if layout == (0, 0, 0) else np.array(generate_skidpad_lanes()[0])
                     @ route.rotation.T + layout[:2])
        phases.append(route.labels[route.segment])
        assert not route.fault
    assert route.crossings == ['ENTRY', 'RIGHT_1', 'RIGHT_2', 'LEFT_1', 'LEFT_2']
    assert route.laps == 4
    assert set(phases) == {'ENTRY', 'RIGHT_1', 'RIGHT_2', 'LEFT_1', 'LEFT_2', 'EXIT'}
    for _ in range(12):
        now += 0.1
        route.update(pose, 0.0, 0.1, now, np.array(generate_skidpad_lanes()[0])
                     @ route.rotation.T + layout[:2])
    assert route.complete
    assert route.labels[route.segment] == 'STOPPED'
    assert route.update(pose, 0, 0.1, now + 0.1, []) == 0


def test_shared_coordinates_have_distinct_arc_lengths_and_local_paths():
    route = _route()
    assert np.all(np.diff(route.arc) > 0)
    first = np.argmin(abs(route.arc - route.ends[0]))
    second = np.argmin(abs(route.arc - route.ends[1]))
    assert route.points[first] == pytest.approx(route.points[second], abs=1e-10)
    route.progress = route.ends[1] + 5
    path = route.forward_path()
    assert len(path) < 50
    assert np.linalg.norm(path[-1] - path[0]) < 9


def test_crossing_jitter_and_backward_motion_do_not_count_laps():
    route = _route()
    now = 0.0
    for y in np.arange(-13.5, 0.3, 0.2):
        now += 0.1
        route.update([0, y, math.pi / 2], 2, 0.1, now, generate_skidpad_lanes()[0])
    assert route.segment == 1
    for y in [0.1, -0.1, 0.1, -0.2, 0.2] * 4:
        now += 0.1
        route.update([0, y, math.pi / 2], 2, 0.1, now, generate_skidpad_lanes()[0])
    assert route.laps == 0
    assert route.segment == 1
    assert not route.fault


def test_route_rejects_missing_gate_or_lane_and_latches_position_jump():
    small, large = generate_skidpad_lanes()
    with pytest.raises(ValueError):
        SkidpadRoute([0, 0, 0], [0, -13.5, math.pi / 2], small, [], 1.875, 0.682,
                     ROUTE_DEFAULTS)
    with pytest.raises(ValueError):
        SkidpadRoute([0, 0, 0], [0, -13.5, math.pi / 2], [], large, 1.875, 0.682,
                     ROUTE_DEFAULTS)
    route = _route()
    assert route.update([0, 0, math.pi / 2], 2, 0.1, 0.1, small) == 0
    assert route.fault
    assert route.update([0, -13.5, math.pi / 2], 0, 0.1, 0.2, small) == 0
