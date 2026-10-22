"""The acceleration event is wired end to end: mission, pairing width, path."""

import importlib.util
from pathlib import Path

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters
from lhr_track_builder.track_builder_node import TrackBuilder
from lhr_trackgen.publish_cones import (
    ACCEL_EXIT_M, ACCEL_LENGTH_M, generate_acceleration_track, pairing_width_m)
import pytest
import rclpy


def _launch(monkeypatch, **overrides):
    path = Path(__file__).parents[1] / 'launch/gazebo_demo.launch.py'
    spec = importlib.util.spec_from_file_location('gazebo_demo', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_find_world', lambda context: '/tmp/test.sdf')
    monkeypatch.setattr(module, 'get_package_share_directory',
                        lambda package: '/tmp/lhr_gazebo')
    context = LaunchContext()
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    assert set(overrides) <= set(context.launch_configurations)
    context.launch_configurations.update(overrides)
    nodes = [a for a in module._launch_setup(context) if isinstance(a, Node)]
    return context, nodes


def _params(context, nodes, exe):
    (node,) = [n for n in nodes if n.node_executable == exe]
    return evaluate_parameters(context, node._Node__parameters)[0]


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
def test_acceleration_track_runs_its_mission_at_its_width(monkeypatch, perception):
    context, nodes = _launch(
        monkeypatch, track_style='acceleration', perception=perception)
    assert _params(context, nodes, 'mission_manager')['mission'] == 'acceleration'
    # Metrics judges a FINISHED autocross without a lap as invalid, so it
    # must see the forced mission too, not the raw argument.
    assert _params(context, nodes, 'metrics_node')['mission'] == 'acceleration'
    assert _params(context, nodes, 'track_builder')['track_width'] == pytest.approx(
        pairing_width_m('acceleration'))


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
def test_other_tracks_keep_their_mission_and_width(monkeypatch, perception):
    context, nodes = _launch(monkeypatch, perception=perception, mission='skidpad')
    assert _params(context, nodes, 'mission_manager')['mission'] == 'skidpad'
    assert _params(context, nodes, 'metrics_node')['mission'] == 'skidpad'
    assert _params(context, nodes, 'track_builder')['track_width'] == pytest.approx(3.5)


def test_acceleration_mission_needs_the_acceleration_track(monkeypatch):
    with pytest.raises(RuntimeError, match='needs track_style:=acceleration'):
        _launch(monkeypatch, mission='acceleration')


@pytest.fixture
def builder():
    width = pairing_width_m('acceleration')
    rclpy.init(args=['--ros-args', '-p', f'track_width:={width}'])
    node = None
    try:
        node = TrackBuilder()
        yield node
    finally:
        # Always, so one failure cannot poison every later rclpy test.
        if node is not None:
            node.destroy_node()
        rclpy.shutdown()


def test_track_builder_pairs_the_whole_course(builder):
    # At the default 3.5 m width these cones never paired: no centerline,
    # so the mission never left OFF.
    left, right = generate_acceleration_track()
    builder._left_cones = dict(enumerate(left))
    builder._right_cones = dict(enumerate(right))
    builder._veh_x, builder._veh_y, builder._veh_yaw = -2.17, 0.0, 0.0
    builder._have_odom = True
    path = builder._pair_boundary()
    xs = [x for x, _ in path]
    assert min(xs) < 0.0
    assert max(xs) > ACCEL_LENGTH_M + ACCEL_EXIT_M - 5.0
    assert all(abs(y) < 0.1 for _, y in path)
