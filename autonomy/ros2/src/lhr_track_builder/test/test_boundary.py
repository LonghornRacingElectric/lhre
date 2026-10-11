"""Check local boundary path continuity without ground-truth pairing IDs."""

import math

from lhr_track_builder.track_builder_node import TrackBuilder
import numpy as np
import pytest
import rclpy


@pytest.fixture
def builder():
    rclpy.init()
    node = TrackBuilder()
    node._pairing_strategy = 'boundary'
    node._have_odom = True
    yield node
    node.destroy_node()
    rclpy.shutdown()


def corridor():
    return [(x, y) for x in range(-4, 25, 2) for y in (-1.75, 1.75)]


def set_cones(builder, cones):
    builder._all_cones = dict(enumerate(cones))


def test_boundary_path_stays_between_cones_and_advances_forward(builder):
    set_cones(builder, corridor())
    path = np.array(builder._pair_boundary())
    assert len(path) >= 10
    assert np.max(np.abs(path[:, 1])) < 1e-6
    assert np.all(np.diff(path[:, 0]) > 0.)
    assert np.max(np.linalg.norm(np.diff(path, axis=0), axis=1)) <= 4.


def test_shuffle_of_unclassified_ids_does_not_change_path(builder):
    points = corridor()
    set_cones(builder, points)
    original = builder._pair_boundary()
    np.random.default_rng(1).shuffle(points)
    set_cones(builder, points)
    assert np.allclose(original, builder._pair_boundary())


def test_disconnected_track_section_is_not_joined_by_a_long_jump(builder):
    points = [(x, y) for x in (0., 2., 4., 20., 22., 24.) for y in (-1.75, 1.75)]
    set_cones(builder, points)
    path = np.array(builder._pair_boundary())
    assert len(path) >= 2
    assert path[:, 0].max() <= 4.


def test_curved_corridor_follows_forward_without_a_return_tour(builder):
    points = [(r * math.cos(a), r * math.sin(a))
              for a in np.arange(-0.4, 1.61, 0.15) for r in (10.25, 13.75)]
    set_cones(builder, points)
    builder._veh_x, builder._veh_y, builder._veh_yaw = 12., 0., math.pi / 2
    path = np.array(builder._pair_boundary())
    assert len(path) >= 5
    assert np.max(np.abs(np.linalg.norm(path, axis=1) - 12.)) < 0.2
    assert np.all(np.diff(np.unwrap(np.arctan2(path[:, 1], path[:, 0]))) > 0.)
