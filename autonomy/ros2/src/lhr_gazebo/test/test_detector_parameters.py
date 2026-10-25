"""Keep Gazebo's sensor-frame defaults and verify typed detector overrides."""

import importlib.util
import json
from pathlib import Path

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters
from lhr_perception.lidar_cone_detector import LidarConeDetector
from nav_msgs.msg import Odometry
import pytest
import rclpy
from sensor_msgs_py import point_cloud2
from std_msgs.msg import Header


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
@pytest.mark.parametrize('override', [False, True])
def test_gazebo_detector_defaults_and_overrides(perception, override, monkeypatch):
    path = Path(__file__).parents[1] / 'launch/gazebo_demo.launch.py'
    spec = importlib.util.spec_from_file_location('gazebo_detector_launch', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_find_world', lambda _: '/tmp/test.sdf')
    monkeypatch.setattr(module, 'get_package_share_directory', lambda _: '/tmp/lhr_gazebo')
    context = LaunchContext()
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    context.launch_configurations['perception'] = perception
    expected = {
        'ground_z_min': -0.4, 'ground_z_max': 0.5, 'min_cluster_points': 1,
        'dedup_radius': 1.5, 'max_cluster_points': 50, 'stack_window_sec': 0.0,
        'stack_max_frames': 10, 'transform_wait_sec': 0.5,
        'sensor_transform_source': 'vehicle_config', 'height_filter_frame': 'sensor',
    }
    if override:
        expected.update(
            ground_z_min=0.05, ground_z_max=0.6, min_cluster_points=2,
            dedup_radius=0.8, max_cluster_points=100, stack_window_sec=0.2,
            stack_max_frames=5, transform_wait_sec=1.0,
            sensor_transform_source='tf', height_filter_frame='base_link')
        context.launch_configurations.update({
            'lidar_' + name: str(value) for name, value in expected.items()})
    nodes = [action for action in module._launch_setup(context) if isinstance(action, Node)]
    assert not any(node.node_executable == 'static_transform_publisher' for node in nodes)
    detectors = [node for node in nodes if node.node_executable == 'lidar_cone_detector']
    if perception == 'sim':
        assert not detectors
        return
    params = evaluate_parameters(context, detectors[0]._Node__parameters)[0]
    for name, value in expected.items():
        assert params[name] == value
        assert type(params[name]) is type(value)
    args = ['--ros-args']
    # Do not bind these tests to a simulated clock that is not running.
    params['use_sim_time'] = False
    for name, value in params.items():
        args += ['-p', f'{name}:={json.dumps(value)}']
    rclpy.init(args=args)
    detector = LidarConeDetector()
    try:
        for name, value in expected.items():
            assert detector.get_parameter(name).value == value
        if not override:
            # Use Gazebo's generated frame without inserting a mount TF.
            # Ground at -0.55 is rejected; a cone at -0.30 is retained.
            odom = Odometry()
            odom.header.frame_id, odom.child_frame_id = 'map', 'base_link'
            odom.header.stamp.sec = 10
            odom.pose.pose.orientation.w = 1.0
            detector._odom_cb(odom)
            header = Header(frame_id='fsae_vehicle/chassis/lidar')
            header.stamp.sec = 10
            cloud = point_cloud2.create_cloud_xyz32(
                header, [[6., 2., -0.55], [6., -2., -0.30]])
            detector._cloud_cb(cloud)
            detector._process()
            assert len(detector._cone_map) == 1
            assert detector._cone_map[0][:2] == pytest.approx((7.8, -2.0))
    finally:
        detector.destroy_node()
        rclpy.shutdown()
