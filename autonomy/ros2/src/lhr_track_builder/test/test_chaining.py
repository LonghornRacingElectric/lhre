"""Behavior checks for cone pairing and midpoint chaining."""

import math
from types import SimpleNamespace
from unittest.mock import Mock

from lhr_track_builder.track_builder_node import TrackBuilder
import pytest


@pytest.fixture
def builder():
    """Provide algorithm state without starting a ROS node."""
    state = SimpleNamespace(
        _min_step_m=0.3, _max_step_m=4.0,
        _max_turn_rad=math.radians(70.0),
        _distance_weight=1.0, _heading_weight=1.0,
        _have_odom=True, _veh_x=0.0, _veh_y=0.0, _veh_yaw=0.0,
        _chaining_alg='constrained_greedy', _max_points=200,
        _track_width=3.5, _track_width_tol=1.0,
        _beam_width=3, _beam_depth=10,
        _boundary_use_color=True,
    )
    for name in (
        '_constrained_greedy', '_chain_path_from_vehicle',
        '_chain_path_without_vehicle', '_get_chaining_algorithm',
        '_fixed_width_beam_search', '_chain_midpoints', '_initial_heading',
    ):
        setattr(state, name, getattr(TrackBuilder, name).__get__(state))
    state._greedy_chain_path = TrackBuilder._greedy_chain_path
    return state


def test_forward_prefix_stops_at_gap(builder):
    """Exclude points behind the car and stop before a distant continuation."""
    points = [(2.0, 0.0), (-1.0, 0.0), (0.0, 0.0), (4.0, 0.0), (9.0, 0.0)]
    assert builder._chain_path_from_vehicle(points) == [
        (0.0, 0.0), (2.0, 0.0), (4.0, 0.0),
    ]


def test_sector_rotates_around_bend(builder):
    """Follow a bend even after its points leave the original forward sector."""
    builder._max_step_m = 1.5
    points = [(0.0, 0.0), (1.0, 0.0), (2.0, 1.0), (2.0, 2.0), (1.0, 3.0)]
    assert builder._constrained_greedy(points, 0) == points


@pytest.mark.parametrize('weight, expected', [(0.0, 1), (1.0, 2)])
def test_heading_weight_changes_choice(builder, weight, expected):
    """Heading cost can favor a straight continuation over a closer zigzag."""
    builder._heading_weight = weight
    points = [(0.0, 0.0), (0.5, 0.8), (1.5, 0.0)]
    assert builder._constrained_greedy(points, 0)[1] == points[expected]


def test_sector_rejects_sharp_turn(builder):
    """A nearby point outside the sector cannot win even with no heading cost."""
    builder._heading_weight = 0.0
    points = [(0.0, 0.0), (0.0, 1.0), (2.0, 0.0)]
    assert builder._constrained_greedy(points, 0) == [points[0], points[2]]


def test_heading_wrap_and_start_are_preserved(builder):
    """Cross the angle wrap without reversing the completed path."""
    builder._veh_yaw = math.radians(179.0)
    points = [(0.0, 0.0), (-1.0, -0.02), (-2.0, -0.04)]
    assert builder._chain_path_from_vehicle(points) == points


def test_close_points_cannot_rejoin(builder):
    """Suppress near duplicates permanently, even with a full-circle sector."""
    builder._max_turn_rad = math.pi
    builder._heading_weight = 0.0
    points = [(0.0, 0.0), (0.0, 0.0), (0.1, 0.0), (1.0, 0.0)]
    assert builder._constrained_greedy(points, 0) == [points[0], points[3]]


def test_no_odometry_uses_separated_neighbor(builder):
    """Ignore coincident and sub-minimum points when initializing direction."""
    builder._have_odom = False
    points = [(0.0, 0.0), (0.0, 0.0), (0.1, 0.0), (0.0, 1.0), (0.0, 2.0)]
    assert builder._chain_path_without_vehicle(points) == [
        points[0], points[3], points[4],
    ]


def test_equal_scores_use_input_index(builder):
    """Resolve symmetric candidates deterministically."""
    points = [(0.0, 0.0), (1.0, 1.0), (1.0, -1.0)]
    assert builder._constrained_greedy(points, 0)[1] == points[1]


@pytest.mark.parametrize('points', [[], [(0.0, 0.0)]])
def test_empty_and_single_point(builder, points):
    """Return short inputs without attempting to infer a segment."""
    builder._have_odom = False
    assert builder._constrained_greedy(points, 0) == points


def test_zero_minimum_still_suppresses_duplicates(builder):
    """An exact duplicate must not produce a zero-length segment."""
    builder._min_step_m = 0.0
    points = [(0.0, 0.0), (0.0, 0.0), (1.0, 0.0)]
    assert builder._constrained_greedy(points, 0) == [points[0], points[2]]


@pytest.mark.parametrize('strategy', ['nearest', 'boundary'])
@pytest.mark.parametrize('algorithm', ['constrained_greedy', 'fixed_width_beam_search'])
def test_pairing_filters_two_midpoints(builder, strategy, algorithm):
    """Both pairing strategies enforce the sector on two-point inputs."""
    builder._veh_x = 2.0
    builder._chaining_alg = algorithm
    builder._left_cones = {0: (0.0, 1.75), 1: (2.0, 1.75)}
    builder._right_cones = {0: (0.0, -1.75), 1: (2.0, -1.75)}
    builder._unknown_cones = {}
    builder._track_width_tol = 0.1
    result = getattr(TrackBuilder, '_pair_' + strategy)(builder)
    assert result == [(2.0, 0.0)]


@pytest.mark.parametrize('strategy, cone_count, vehicle_x, step', [
    ('boundary', 111, 200, 1), ('nearest', 221, 400, 2),
])
@pytest.mark.parametrize('algorithm', ['constrained_greedy', 'fixed_width_beam_search'])
@pytest.mark.parametrize('max_points', [200, 5])
def test_pairing_caps_path_after_vehicle_start(
    builder, strategy, cone_count, vehicle_x, step, algorithm, max_points,
):
    """Keep forward candidates beyond the map cap, then limit the ordered path."""
    builder._veh_x = float(vehicle_x)
    builder._chaining_alg = algorithm
    builder._max_points = max_points
    builder._beam_depth = 40
    builder._left_cones = {i: (2.0 * i, 1.75) for i in range(cone_count)}
    builder._right_cones = {i: (2.0 * i, -1.75) for i in range(cone_count)}
    builder._unknown_cones = {}
    expected = [
        (float(x), 0.0) for x in range(vehicle_x, 2 * cone_count - 1, step)
    ]
    result = getattr(TrackBuilder, '_pair_' + strategy)(builder)
    assert result == expected[:max_points]


def test_beam_keeps_alternative_to_greedy_dead_end(builder):
    """Retain a costlier first step whose branch can continue around a corner."""
    builder._beam_width = 2
    builder._beam_depth = 3
    builder._max_step_m = 1.5
    points = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (1.0, 2.0), (1.0, 3.0)]
    assert builder._constrained_greedy(points, 0) == points[:2]
    assert builder._fixed_width_beam_search(points, 0) == [
        points[0], points[2], points[3], points[4],
    ]


def test_beam_prunes_globally_and_can_keep_two_siblings(builder):
    """Two cheap siblings displace another parent; a wider beam preserves it."""
    builder._beam_width = 2
    builder._beam_depth = 3
    builder._max_step_m = 1.5
    points = [
        (0.0, 0.0), (1.0, 0.0), (1.0, 1.0),
        (2.0, -0.2), (2.0, 0.2), (1.0, 2.0), (1.0, 3.0),
    ]
    # At depth two, both retained paths begin with point 1. Neither can extend.
    assert builder._fixed_width_beam_search(points, 0) == [
        points[0], points[1], points[3],
    ]
    builder._beam_width = 3
    assert builder._fixed_width_beam_search(points, 0) == [
        points[0], points[2], points[5], points[6],
    ]


def test_beam_uses_accumulated_cost(builder):
    """Choose the cheaper complete prefix even when another final step is shorter."""
    builder._beam_width = 4
    builder._beam_depth = 2
    builder._heading_weight = 0.0
    points = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (2.0, 1.0), (2.0, 1.4)]
    assert builder._fixed_width_beam_search(points, 0) == points[:3]


@pytest.mark.parametrize('depth', [1, 2, 10])
def test_beam_depth_counts_added_points(builder, depth):
    """Return a bounded prefix and stop naturally when every point is visited."""
    builder._beam_depth = depth
    builder._max_step_m = 1.1
    points = [(float(x), 0.0) for x in range(5)]
    assert builder._fixed_width_beam_search(points, 0) == points[:depth + 1]


@pytest.mark.parametrize('have_odom', [False, True])
@pytest.mark.parametrize('points', [
    [], [(0.0, 0.0)],
    [(0.0, 0.0), (0.0, 0.0)],
    [(0.0, 0.0), (0.1, 0.0), (1.0, 0.0), (2.0, 0.0), (7.0, 0.0)],
    [(0.0, 0.0), (1.0, 0.0), (2.0, 1.0), (2.0, 2.0), (1.0, 3.0)],
])
def test_width_one_matches_constrained_greedy(builder, have_odom, points):
    """Preserve sector, duplicate, heading, and stopping rules with one branch."""
    builder._beam_width = 1
    builder._have_odom = have_odom
    builder._max_step_m = 1.5
    original = list(points)
    assert builder._fixed_width_beam_search(points, 0) == (
        builder._constrained_greedy(points, 0)
    )
    assert points == original


def test_beam_keeps_start_even_with_wide_sector(builder):
    """The vehicle wrapper must not reverse a path already chosen by the beam."""
    builder._chaining_alg = 'fixed_width_beam_search'
    builder._beam_width = 1
    builder._max_turn_rad = math.pi
    builder._heading_weight = 0.0
    points = [(0.0, 0.0), (-1.0, 0.0), (-2.0, 0.0)]
    assert builder._chain_path_from_vehicle(points) == points


def test_beam_equal_costs_use_path_indices(builder):
    """Resolve equal-cost paths consistently at the search horizon."""
    builder._beam_depth = 1
    points = [(0.0, 0.0), (1.0, 1.0), (1.0, -1.0)]
    assert builder._fixed_width_beam_search(points, 0) == points[:2]


@pytest.mark.parametrize('use_color, expected', [
    (True, []), (False, [(0.0, 0.0), (2.0, 0.0)]),
])
def test_geometry_only_boundary_retains_same_color_pairs(builder, use_color, expected):
    """The legacy width-only filter can retain edges rejected by color."""
    builder._boundary_use_color = use_color
    builder._track_width_tol = 0.1
    builder._chaining_alg = 'greedy'
    builder._left_cones = {
        0: (0.0, 1.75), 1: (0.0, -1.75),
        2: (2.0, 1.75), 3: (2.0, -1.75),
    }
    builder._right_cones = {}
    builder._unknown_cones = {}
    assert TrackBuilder._pair_boundary(builder) == expected


@pytest.mark.parametrize('use_color', [False, True])
@pytest.mark.parametrize('known_color', [False, True])
def test_boundary_accepts_unknown_color_edges(builder, use_color, known_color):
    """Unclassified and mixed inputs both retain plausible cross-track edges."""
    builder._boundary_use_color = use_color
    builder._track_width_tol = 0.1
    builder._chaining_alg = 'greedy'
    builder._left_cones = {0: (0.0, 1.75), 1: (2.0, 1.75)} if known_color else {}
    builder._right_cones = {}
    builder._unknown_cones = {0: (0.0, -1.75), 1: (2.0, -1.75)}
    if not known_color:
        builder._unknown_cones.update({2: (0.0, 1.75), 3: (2.0, 1.75)})
    assert set(TrackBuilder._pair_boundary(builder)) == {(0.0, 0.0), (2.0, 0.0)}


@pytest.mark.parametrize('points', [
    [], [(0.0, 0.0), (1.0, 0.0)], [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)],
])
def test_geometry_only_boundary_handles_degenerate_inputs(builder, points):
    """Missing or collinear cones produce no new path."""
    builder._boundary_use_color = False
    builder._left_cones = {}
    builder._right_cones = {}
    builder._unknown_cones = dict(enumerate(points))
    assert TrackBuilder._pair_boundary(builder) == []


@pytest.mark.parametrize('strategy', ['nearest', 'boundary'])
@pytest.mark.parametrize('uncolored', [False, True])
def test_uncolored_cone_warning_preserves_detections(builder, strategy, uncolored):
    """Warn with a throttle and strategy guidance only for unclassified inputs."""
    builder._pairing_strategy = strategy
    logger = Mock()
    builder.get_logger = Mock(return_value=logger)
    namespaces = ['left_cones', 'right_cones']
    if uncolored:
        namespaces.append('cones')
    markers = [
        SimpleNamespace(
            ns=namespace, id=0,
            pose=SimpleNamespace(position=SimpleNamespace(x=float(i), y=0.0)),
        )
        for i, namespace in enumerate(namespaces)
    ]
    TrackBuilder._cones_cb(builder, SimpleNamespace(markers=markers))
    assert builder._left_cones == {0: (0.0, 0.0)}
    assert builder._right_cones == {0: (1.0, 0.0)}
    assert builder._unknown_cones == ({0: (2.0, 0.0)} if uncolored else {})
    if uncolored:
        logger.warning.assert_called_once()
        message = logger.warning.call_args.args[0]
        assert '1 uncolored cones' in message
        assert ('uses geometry' if strategy == 'boundary' else 'ignores') in message
        assert logger.warning.call_args.kwargs == {'throttle_duration_sec': 5.0}
    else:
        logger.warning.assert_not_called()
