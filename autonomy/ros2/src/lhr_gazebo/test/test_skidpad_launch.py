"""Acquisition view and limits reach real node startup in both perception modes."""

import importlib.util
from pathlib import Path

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters
from lhr_control.pursuit_node import PurePursuit
from lhr_perception.lidar_cone_detector import LidarConeDetector
from lhr_sensor_sim.sensor_sim_node import SensorSim
from lhr_track_builder.skidpad_acquisition import ACQUISITION_DEFAULTS, SkidpadAcquisition
import pytest
import rclpy


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
@pytest.mark.parametrize('override', [False, True])
def test_acquisition_launch(perception, override, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        'gazebo_skidpad', Path(__file__).parents[1] / 'launch/gazebo_demo.launch.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_find_world', lambda context: '/tmp/skidpad.sdf')
    monkeypatch.setattr(module, 'get_package_share_directory', lambda package: '/tmp/gazebo')
    context = LaunchContext()
    context.launch_configurations.update(mission='skidpad', perception=perception)
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    expected = dict(ACQUISITION_DEFAULTS, fov_deg=240.0, max_range_m=15.0)
    if override:
        expected.update(acquisition_speed=0.25, acquisition_distance=3.0,
                        fit_stable_frames=3, fov_deg=180.0, max_range_m=12.0,
                        fit_min_chord_m=3.0, fit_max_centers=10,
                        fit_max_hypotheses=4, fit_heading_tolerance_deg=35.0)
        for name, value in expected.items():
            context.launch_configurations[name] = str(value)
    actions = module._launch_setup(context)
    nodes = [action for action in actions if isinstance(action, Node)]
    planners = [node for node in nodes if node.node_package == 'lhr_track_builder']
    assert len(planners) == 1
    params = evaluate_parameters(context, planners[0]._Node__parameters)[0]
    for name, value in expected.items():
        assert params[name] == value
        assert type(params[name]) is type(value)
    control = next(node for node in nodes if node.node_package == 'lhr_control')
    control_params = evaluate_parameters(context, control._Node__parameters)[0]
    assert control_params['require_planner_limit'] is True
    assert control_params['path_closed'] is False
    if perception == 'sim':
        sensor = next(node for node in nodes if node.node_package == 'lhr_sensor_sim')
        sensor_params = evaluate_parameters(context, sensor._Node__parameters)[0]
        assert sensor_params['accumulate_detections'] is False
        assert sensor_params['fov_deg'] == expected['fov_deg']
        assert sensor_params['max_range_m'] == expected['max_range_m']
    else:
        sensor = next(node for node in nodes if node.node_package == 'lhr_perception')
        sensor_params = evaluate_parameters(context, sensor._Node__parameters)[0]
        assert sensor_params['max_range'] == expected['max_range_m']
    sensor_class = SensorSim if perception == 'sim' else LidarConeDetector
    for cls, parameters in ((SkidpadAcquisition, params), (PurePursuit, control_params),
                            (sensor_class, sensor_params)):
        args = ['--ros-args']
        for name, value in parameters.items():
            args.extend(['-p', f'{name}:={str(value).lower()}'])
        rclpy.init(args=args)
        node = cls()
        try:
            for name, value in parameters.items():
                assert node.get_parameter(name).value == value
        finally:
            node.destroy_node()
            rclpy.shutdown()


def test_skidpad_world_regenerates_with_lanes_and_staged_vehicle(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        'skidpad_world', Path(__file__).parents[1] / 'scripts/generate_world.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / 'skidpad.sdf'
    monkeypatch.setattr('sys.argv', [
        'generate_world.py', '--style', 'skidpad', '--output', str(output)])
    module.main()
    committed = Path(__file__).parents[1] / 'worlds/skidpad_seed1.sdf'
    assert output.read_text() == committed.read_text()
    assert output.read_text().count('<uri>model://cone_orange_small</uri>') == 42
    assert output.read_text().count('<uri>model://cone_orange_large</uri>') == 4
