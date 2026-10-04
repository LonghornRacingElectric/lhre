"""Behavior checks for sector-constrained midpoint chaining."""

import math
from types import SimpleNamespace

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
    )
    for name in (
        '_constrained_greedy', '_chain_path_from_vehicle',
        '_chain_path_without_vehicle', '_get_chaining_algorithm',
    ):
        setattr(state, name, getattr(TrackBuilder, name).__get__(state))
    state._greedy_chain_path = TrackBuilder._greedy_chain_path
    state._fixed_width_beam_search = TrackBuilder._fixed_width_beam_search
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
def test_pairing_filters_two_midpoints(builder, strategy):
    """Both pairing strategies enforce the sector on two-point inputs."""
    builder._veh_x = 2.0
    builder._left_cones = {0: (0.0, 1.75), 1: (2.0, 1.75)}
    builder._right_cones = {0: (0.0, -1.75), 1: (2.0, -1.75)}
    builder._unknown_cones = {}
    builder._track_width_tol = 0.1
    result = getattr(TrackBuilder, '_pair_' + strategy)(builder)
    assert result == [(2.0, 0.0)]
