"""Check acceleration wiring, startup overrides, and world geometry."""

import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters
from lhr_control.pursuit_node import PurePursuit
from lhr_track_builder.acceleration_planner import AccelerationPlanner
from lhr_trackgen.acceleration import generate_acceleration_track
from lhr_vehicle import load_vehicle
import pytest
import rclpy


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
@pytest.mark.parametrize('override', [False, True])
def test_acceleration_launch_parameters(perception, override, monkeypatch):
    """Both perception branches must use one planner and an open-path controller."""
    launch_path = Path(__file__).parents[1] / 'launch/gazebo_demo.launch.py'
    spec = importlib.util.spec_from_file_location('acceleration_launch', launch_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_find_world', lambda context: '/tmp/acceleration.sdf')
    monkeypatch.setattr(module, 'get_package_share_directory', lambda package: '/tmp/lhr_gazebo')
    context = LaunchContext()
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    context.launch_configurations.update(track_style='acceleration', perception=perception)
    expected = {'acceleration_path_spacing_m': 0.5,
                'acceleration_stop_distance_m': 20.0, 'input_timeout_sec': 1.0}
    if override:
        context.launch_configurations.update(acceleration_path_spacing_m='1',
                                             acceleration_stop_distance_m='25',
                                             input_timeout_sec='2')
        expected = {'acceleration_path_spacing_m': 1.,
                    'acceleration_stop_distance_m': 25., 'input_timeout_sec': 2.}
    actions = [a for a in module._launch_setup(context) if isinstance(a, Node)]
    planners = [a for a in actions if a.node_package == 'lhr_track_builder']
    assert len(planners) == 1
    planner_params = evaluate_parameters(context, planners[0]._Node__parameters)[0]
    for key, value in expected.items():
        assert planner_params[key] == value
        assert type(planner_params[key]) is float
    control = next(a for a in actions if a.node_package == 'lhr_control')
    control_params = evaluate_parameters(context, control._Node__parameters)[0]
    assert control_params['path_closed'] is False
    assert control_params['input_timeout_sec'] == expected['input_timeout_sec']
    mission = next(a for a in actions if a.node_package == 'lhr_mission_manager')
    assert evaluate_parameters(context, mission._Node__parameters)[0]['mission'] == 'acceleration'
    for cls, parameters in ((AccelerationPlanner, planner_params), (PurePursuit, control_params)):
        args = ['--ros-args']
        for key, value in parameters.items():
            args.extend(['-p', f'{key}:={str(value).lower()}'])
        rclpy.init(args=args)
        node = cls()
        try:
            if cls is AccelerationPlanner:
                assert node._spacing == expected['acceleration_path_spacing_m']
                assert node._stop_distance == expected['acceleration_stop_distance_m']
                assert node._timeout == expected['input_timeout_sec']
            else:
                assert node._path_closed is False
                assert node._input_timeout == expected['input_timeout_sec']
        finally:
            node.destroy_node()
            rclpy.shutdown()


def test_acceleration_world_matches_published_cones():
    """Use identical cone positions in Gazebo and synthetic perception."""
    world = ET.parse(Path(__file__).parents[1] / 'worlds/acceleration.sdf')
    includes = world.findall('.//world/include')
    layout = generate_acceleration_track()
    for namespace, model in (('left_cones', 'cone_blue'), ('right_cones', 'cone_yellow'),
                             ('orange_small_cones', 'cone_orange_small'),
                             ('orange_large_cones', 'cone_orange_large')):
        positions = [tuple(map(float, item.findtext('pose').split()[:2]))
                     for item in includes if item.findtext('uri') == f'model://{model}']
        assert len(positions) == len(layout[namespace])
        for actual, expected in zip(positions, layout[namespace]):
            assert actual == pytest.approx(expected, abs=0.0001)
    vehicle = next(item for item in includes if item.findtext('name') == 'fsae_vehicle')
    x, y, _, _, _, yaw = map(float, vehicle.findtext('pose').split())
    parameters = load_vehicle()
    assert x + parameters.chassis_center_x_m + parameters.body_length_m / 2 == pytest.approx(-0.3)
    assert y == yaw == 0.
