"""Acquisition view and limits reach real node startup in both perception modes."""

import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters
from lhr_control.pursuit_node import PurePursuit
from lhr_perception.cone_map import update_cone_map
from lhr_perception.lidar_cone_detector import LidarConeDetector
from lhr_sensor_sim.sensor_sim_node import SensorSim
from lhr_track_builder.skidpad_acquisition import ACQUISITION_DEFAULTS, SkidpadAcquisition
import numpy as np
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
    monkeypatch.setattr(module, 'get_package_share_directory',
                        lambda package: str(Path(__file__).parents[1]))
    context = LaunchContext()
    context.launch_configurations.update(
        mission='skidpad', perception=perception,
        enable_stereo_camera=str(perception == 'lidar').lower(),
        enable_camera_lidar_fusion=str(perception == 'lidar').lower())
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    expected = dict(ACQUISITION_DEFAULTS, fov_deg=240.0, max_range_m=15.0,
                    require_camera_colors=perception == 'lidar',
                    cone_topic='/lhr/perception/cones_verified' if perception == 'lidar'
                    else '/lhr/sensor/cones_detected')
    if override:
        expected.update(acquisition_speed=0.25, acquisition_distance=3.0,
                        fit_stable_frames=3, fov_deg=180.0, max_range_m=12.0,
                        fit_min_chord_m=3.0, fit_max_centers=10,
                        fit_max_hypotheses=4, fit_heading_tolerance_deg=35.0, skidpad_speed=2.0,
                        skidpad_max_cte=0.5, skidpad_exit_distance=10.0,
                        cone_topic='/test/observed_cones', require_camera_colors=True)
        context.launch_configurations.update(
            lidar_cluster_radius='0.12', lidar_dedup_radius='0.25',
            fusion_sample_height_m='0.12', fusion_pixel_margin='1')
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
    assert control_params['closed_path'] is False
    if perception == 'lidar':
        fusion = next(node for node in nodes if node.node_executable == 'camera_lidar_fusion')
        fusion_params = evaluate_parameters(context, fusion._Node__parameters)[0]
        assert fusion_params['sample_height_m'] == (.12 if override else .1)
        assert fusion_params['pixel_margin'] == (1. if override else 2.)
    if perception == 'sim':
        sensor = next(node for node in nodes if node.node_package == 'lhr_sensor_sim')
        sensor_params = evaluate_parameters(context, sensor._Node__parameters)[0]
        assert sensor_params['accumulate_detections'] is False
        assert sensor_params['fov_deg'] == expected['fov_deg']
        assert sensor_params['max_range_m'] == expected['max_range_m']
    else:
        sensor = next(node for node in nodes if node.node_executable == 'lidar_cone_detector')
        sensor_params = evaluate_parameters(context, sensor._Node__parameters)[0]
        assert sensor_params['max_range'] == expected['max_range_m']
        assert sensor_params['dedup_radius'] == (.25 if override else .3)
        assert sensor_params['cluster_radius'] == (.12 if override else .15)
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
            if cls is LidarConeDetector:
                points = np.array([(1.5 + dx, y, .2) for y in (-.5, 0., .5)
                                   for dx in (-.02, .02)])
                clusters = node._cluster(points)
                assert len(clusters) == 3
                mapped = []
                update_cone_map([cluster.mean(axis=0)[:2] for cluster in clusters],
                                mapped, node._dedup_radius)
                assert len(mapped) == 3
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
    root = ET.fromstring(output.read_text())
    gate_y = [float(cone.find('pose').text.split()[1])
              for cone in root.findall('.//include')
              if cone.find('uri').text == 'model://cone_orange_large']
    assert sorted(gate_y) == [-0.5, -0.5, 0.5, 0.5]


@pytest.mark.parametrize('override', [
    {'enable_camera_lidar_fusion': 'false'}, {'require_camera_colors': 'false'},
])
def test_skidpad_lidar_rejects_missing_color_contract(monkeypatch, override):
    """Reject startup instead of silently waiting for simulated labels."""
    spec = importlib.util.spec_from_file_location(
        'skidpad_guard', Path(__file__).parents[1] / 'launch/gazebo_demo.launch.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_find_world', lambda _: '/tmp/skidpad.sdf')
    monkeypatch.setattr(module, 'get_package_share_directory',
                        lambda _: str(Path(__file__).parents[1]))
    context = LaunchContext()
    context.launch_configurations.update(
        mission='skidpad', perception='lidar', enable_stereo_camera='true',
        enable_camera_lidar_fusion='true')
    context.launch_configurations.update(override)
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    with pytest.raises(RuntimeError, match='Skidpad LiDAR requires'):
        module._launch_setup(context)
