"""Evaluate optional camera classification and parameter forwarding."""

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
@pytest.mark.parametrize('estimator', ['truth', 'ekf'])
@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('override', [False, True])
def test_color_classifier_launch(perception, estimator, enabled, override, monkeypatch):
    """Both perception branches must forward typed settings to node startup."""
    launch = pytest.importorskip('launch')
    pytest.importorskip('launch_ros')
    rclpy = pytest.importorskip('rclpy')
    from launch.actions import DeclareLaunchArgument
    from launch_ros.actions import Node
    from launch_ros.utilities import evaluate_parameters
    from lhr_perception.camera_color_classifier import CameraColorClassifier

    pkg = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location(
        'gazebo_color_launch', pkg / 'launch/gazebo_demo.launch.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_find_world', lambda _: '/tmp/world.sdf')
    monkeypatch.setattr(module, 'get_package_share_directory', lambda _: str(pkg))
    context = launch.LaunchContext()
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    context.launch_configurations.update(
        perception=perception, estimator=estimator,
        enable_camera_color_classifier=str(enabled).lower())
    if override:
        context.launch_configurations.update(
            cone_color_min_region_area='100',
            cone_color_blue_hsv='[95, 80, 40, 135, 255, 255]',
            cone_color_yellow_hsv='[22, 90, 40, 38, 255, 255]',
            cone_color_orange_hsv='[2, 90, 40, 19, 255, 255]')
    # Classification requires explicit camera enablement, never a silent idle node.
    if enabled:
        with pytest.raises(RuntimeError, match='enable_stereo_camera'):
            module._launch_setup(context)
        context.launch_configurations['enable_stereo_camera'] = 'true'
    actions = module._launch_setup(context)
    nodes = [n for n in actions if isinstance(n, Node)
             and n.node_executable == 'camera_color_classifier']
    assert len(nodes) == int(enabled)
    if not enabled:
        return
    params = evaluate_parameters(context, nodes[0]._Node__parameters)[0]
    expected = {
        'min_region_area': 100 if override else 25,
        'blue_hsv': [95, 80, 40, 135, 255, 255] if override
        else [100, 100, 50, 130, 255, 255],
        'yellow_hsv': [22, 90, 40, 38, 255, 255] if override
        else [20, 100, 50, 35, 255, 255],
        'orange_hsv': [2, 90, 40, 19, 255, 255] if override
        else [5, 100, 50, 19, 255, 255],
    }
    assert type(params['min_region_area']) is int
    assert params['use_sim_time'] is True
    ros_args = ['--ros-args']
    for name, value in params.items():
        # Launch evaluates arrays as tuples; ROS CLI values need YAML sequences.
        ros_args.extend(['-p', f'{name}:={json.dumps(value)}'])
    rclpy.init(args=ros_args)
    node = None
    try:
        node = CameraColorClassifier()
        for name, value in expected.items():
            actual = node.get_parameter(name).value
            if isinstance(value, list):
                assert list(params[name]) == value
                assert list(actual) == value
            else:
                assert params[name] == value
                assert actual == value
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.shutdown()
