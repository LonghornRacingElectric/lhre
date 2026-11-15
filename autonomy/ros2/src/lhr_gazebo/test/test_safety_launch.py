"""Check safety:=true wiring: defaults, overrides, types and node startup."""

import importlib.util
from pathlib import Path

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters
from lhr_safety.monitors import SafetyConfig
from lhr_safety.safety_node import SafetyNode
import pytest
import rclpy
from sensor_msgs.msg import Imu
from std_msgs.msg import String

# Launch argument -> node parameter, for every threshold the launch exposes.
ARGS = {
    'safety_lidar_timeout_s': 'lidar_timeout_s',
    'safety_odom_timeout_s': 'odom_timeout_s',
    'safety_imu_timeout_s': 'imu_timeout_s',
    'safety_wheel_timeout_s': 'wheel_timeout_s',
    'safety_mission_timeout_s': 'mission_timeout_s',
    'safety_cmd_timeout_s': 'cmd_timeout_s',
    'safety_max_position_std_m': 'max_position_std_m',
    'safety_max_speed_factor': 'max_speed_factor',
    'safety_geofence_radius_m': 'geofence_radius_m',
    'safety_steer_angle_tol_rad': 'steer_angle_tol_rad',
    'safety_steer_angle_window_s': 'steer_angle_window_s',
    'safety_steer_angle_timeout_s': 'steer_angle_timeout_s',
    'safety_steer_min_speed_mps': 'steer_min_speed_mps',
    'safety_steer_yaw_rate_tol': 'steer_yaw_rate_tol',
    'safety_steer_rel_tol': 'steer_rel_tol',
    'safety_steer_window_s': 'steer_window_s',
}


def _launch(monkeypatch, **overrides):
    """Evaluate the launch file with its declared defaults plus overrides."""
    launch_path = Path(__file__).parents[1] / 'launch/gazebo_demo.launch.py'
    spec = importlib.util.spec_from_file_location('gazebo_demo', launch_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_find_world', lambda context: '/tmp/test.sdf')
    monkeypatch.setattr(module, 'get_package_share_directory',
                        lambda package: '/tmp/lhr_gazebo')
    context = LaunchContext()
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    context.launch_configurations.update(overrides)
    nodes = [a for a in module._launch_setup(context)
             if isinstance(a, Node) and a.node_package == 'lhr_safety']
    return context, nodes


def _start(parameters):
    """Start SafetyNode on evaluated launch values, as ROS would."""
    ros_args = ['--ros-args']
    for name, value in parameters.items():
        text = str(value).lower() if isinstance(value, bool) else str(value)
        ros_args.extend(['-p', f'{name}:={text}'])
    rclpy.init(args=ros_args)
    return SafetyNode()


def test_safety_is_off_by_default(monkeypatch):
    _, nodes = _launch(monkeypatch)
    assert nodes == []


@pytest.mark.parametrize('estimator, ekf', [('truth', False), ('ekf', True)])
@pytest.mark.parametrize('perception, lidar', [('sim', False), ('lidar', True)])
def test_defaults_match_the_node(monkeypatch, perception, lidar, estimator, ekf):
    context, nodes = _launch(monkeypatch, safety='true', perception=perception,
                             estimator=estimator)
    (node,) = nodes
    params = evaluate_parameters(context, node._Node__parameters)[0]
    assert params['monitor_lidar'] is lidar
    # Only the EKF reads the IMU and the wheel speeds (#126).
    assert params['monitor_imu'] is ekf
    assert params['monitor_wheels'] is ekf
    started = _start(params)
    try:
        config = started._config
        assert config.monitor_lidar is lidar
        assert config.monitor_imu is ekf
        assert config.monitor_wheels is ekf
        names = {s.topic_name for s in started.subscriptions}
        assert ('/lhr/imu/data' in names) is ekf
        assert ('/lhr/sensor/wheel_speeds' in names) is ekf
        assert '/lhr/mission/status' in names
        defaults = SafetyConfig(wheelbase_m=config.wheelbase_m,
                                max_speed_mps=config.max_speed_mps)
        for param in ARGS.values():
            assert getattr(config, param) == getattr(defaults, param), param
    finally:
        started.destroy_node()
        rclpy.shutdown()


def test_overrides_reach_the_node_as_floats(monkeypatch):
    # Integer-looking input must still arrive as a ROS double.
    overrides = {arg: str(i + 2) for i, arg in enumerate(ARGS)}
    context, nodes = _launch(monkeypatch, safety='true', perception='lidar',
                             **overrides)
    (node,) = nodes
    params = evaluate_parameters(context, node._Node__parameters)[0]
    for arg, param in ARGS.items():
        assert params[param] == float(overrides[arg])
        assert type(params[param]) is float
    started = _start(params)
    try:
        for arg, param in ARGS.items():
            assert getattr(started._config, param) == float(overrides[arg])
    finally:
        started.destroy_node()
        rclpy.shutdown()


def test_node_feeds_the_sensors_and_mission_status_to_the_monitor(monkeypatch):
    # The callbacks are the only path from the topics to the checks (#126, #127).
    started = _start({})
    try:
        now = [10.0]
        monkeypatch.setattr(started, '_now', lambda: now[0])
        monitor = started._monitor
        started._status_cb(String(data='DRIVING'))
        assert (monitor._last_mission, monitor._mission_armed) == (10.0, True)
        now[0] = 10.5
        imu = Imu()
        # Only the yaw axis is the steering rule's gyro.
        imu.angular_velocity.x, imu.angular_velocity.y, imu.angular_velocity.z = 3.0, 2.0, 0.25
        fed = []
        on_imu = monitor.on_imu

        def record(t, yaw):
            fed.append((t, yaw))
            on_imu(t, yaw)
        monkeypatch.setattr(monitor, 'on_imu', record)
        started._imu_cb(imu)
        assert fed == [(10.5, 0.25)]
        started._wheels_cb(b'raw wheel speeds')
        assert monitor._last_imu == 10.5 and monitor._last_wheels == 10.5
        started._status_cb(String(data='FINISHED'))
        assert monitor._mission_armed is False
    finally:
        started.destroy_node()
        rclpy.shutdown()
