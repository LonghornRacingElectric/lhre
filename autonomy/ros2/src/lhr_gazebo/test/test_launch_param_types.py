"""Every launch argument reaches its node as the type the node declares."""

from collections import Counter
import importlib.util
import json
from pathlib import Path

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.utilities import evaluate_parameters
from lhr_control.pursuit_node import PurePursuit
from lhr_mission_manager.mission_manager_node import MissionManager
from lhr_sensor_sim.inertial_sim_node import InertialSim
from lhr_sensor_sim.sensor_sim_node import SensorSim
from lhr_trackgen.publish_cones import ConePublisher
import pytest
import rclpy
import yaml

# Executable -> (node class, launch arguments it gets under the same name).
NODES = {
    'pursuit_node': (PurePursuit, [
        'lookahead_dist', 'lookahead_min', 'lookahead_curvature_gain',
        'a_lat_max', 'v_min', 'v_max', 'max_accel', 'max_decel']),
    'mission_manager': (MissionManager, ['mission', 'auto_go', 'ready_hold_sec']),
    'inertial_sim': (InertialSim, ['gyro_bias', 'wheel_scale_error']),
    'publish_cones': (ConePublisher, ['seed', 'track_style', 'num_waypoints']),
    'sensor_sim': (SensorSim, ['fov_deg', 'max_range_m']),
}

# Argument -> (command-line text, value the node must see). Each differs from
# both the launch default and the node's own, and every double is written as
# a whole number: untyped, "8" arrived as an INTEGER and killed the node (#130).
OVERRIDES = {
    'lookahead_dist': ('6', 6.0),
    'lookahead_min': ('3', 3.0),
    'lookahead_curvature_gain': ('4', 4.0),
    'a_lat_max': ('4', 4.0),
    'v_min': ('3', 3.0),
    'v_max': ('8', 8.0),
    'max_accel': ('3', 3.0),
    'max_decel': ('4', 4.0),
    'mission': ('skidpad', 'skidpad'),
    'auto_go': ('false', False),
    'ready_hold_sec': ('0', 0.0),
    'gyro_bias': ('1', 1.0),
    'wheel_scale_error': ('2', 2.0),
    'seed': ('7', 7),
    'track_style': ('oval', 'oval'),
    'num_waypoints': ('12', 12),
    'fov_deg': ('360', 360.0),
    'max_range_m': ('30', 30.0),
}

ALWAYS = ['parameter_bridge', 'joint_cmd_adapter', 'pursuit_node',
          'mission_manager', 'metrics_node', 'rviz2']
EKF = ['inertial_sim', 'ekf_node']
SAFETY = ['safety_node', 'fault_injector']

# Every branch that adds nodes, with the executables it must start, so a
# renamed flag cannot quietly drop a branch from the coverage check.
CONFIGS = [
    ({'perception': 'sim', 'estimator': 'truth'},
     ALWAYS + ['publish_cones', 'sensor_sim', 'track_builder']),
    ({'perception': 'sim', 'estimator': 'ekf', 'safety': 'true',
      'fault': 'cmd_nan'},
     ALWAYS + EKF + SAFETY + ['publish_cones', 'sensor_sim', 'track_builder']),
    ({'perception': 'lidar', 'estimator': 'truth',
      'lidar_classify_sides': 'true'},
     ALWAYS + ['lidar_cone_detector', 'track_builder']),
    ({'perception': 'lidar', 'estimator': 'ekf', 'safety': 'true',
      'fault': 'lidar_drop', 'enable_stereo_camera': 'true',
      'enable_camera_color_classifier': 'true'},
     ALWAYS + EKF + SAFETY + ['lidar_cone_detector', 'track_builder',
                              'parameter_bridge', 'static_transform_publisher',
                              'camera_color_classifier']),
]


def _launch(monkeypatch, **overrides):
    """Evaluate the launch file with its declared defaults plus overrides."""
    pkg = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location(
        'gazebo_param_types', pkg / 'launch/gazebo_demo.launch.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_find_world', lambda context: '/tmp/test.sdf')
    monkeypatch.setattr(module, 'get_package_share_directory',
                        lambda package: str(pkg))
    context = LaunchContext()
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    # A misspelled or renamed argument would otherwise be silently ignored.
    assert set(overrides) <= set(context.launch_configurations)
    context.launch_configurations.update(overrides)
    nodes = [a for a in module._launch_setup(context) if isinstance(a, Node)]
    return context, nodes


def _uses_argument(value):
    if isinstance(value, LaunchConfiguration):
        return True
    if isinstance(value, (list, tuple)):
        return any(_uses_argument(v) for v in value)
    return False


def _untyped(node):
    """Return the node's parameters fed by a bare LaunchConfiguration."""
    names = []
    for params in node._Node__parameters:
        if not isinstance(params, dict):
            continue  # a parameter file
        for name, value in params.items():
            if isinstance(value, ParameterValue):
                if getattr(value, 'value_type', None) is not None:
                    continue
                value = value.value
            if _uses_argument(value):
                names.append(name if isinstance(name, str) else ''.join(
                    getattr(s, 'text', '?') for s in name))
    return names


def _evaluate(context, nodes):
    """Evaluate the parameters of the NODES executables, one node each."""
    found = [n for n in nodes if n.node_executable in NODES]
    assert sorted(n.node_executable for n in found) == sorted(NODES)
    return {n.node_executable: evaluate_parameters(context, n._Node__parameters)[0]
            for n in found}


def _start(node_class, params, names):
    """Start the node on params as ROS would, and read names back."""
    ros_args = ['--ros-args']
    for name, value in params.items():
        ros_args.extend(['-p', f'{name}:={json.dumps(value)}'])
    rclpy.init(args=ros_args)
    node = None
    try:
        node = node_class()
        return {name: node.get_parameter(name).value for name in names}
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.shutdown()


def test_untyped_check_catches_a_bare_argument():
    def node(value):
        return Node(package='p', executable='e', parameters=[{'x': value}])

    assert _untyped(node(LaunchConfiguration('v_max'))) == ['x']
    assert _untyped(node(ParameterValue(
        LaunchConfiguration('v_max'), value_type=float))) == []
    assert _untyped(node(1.0)) == []


@pytest.mark.parametrize('config, expected', CONFIGS)
def test_every_forwarded_argument_is_typed(monkeypatch, config, expected):
    context, nodes = _launch(monkeypatch, **config)
    started = Counter(n.node_executable for n in nodes)
    assert not Counter(expected) - started, 'a branch did not start'
    if config.get('lidar_classify_sides') == 'true':
        (builder,) = [n for n in nodes if n.node_executable == 'track_builder']
        params = evaluate_parameters(context, builder._Node__parameters)[0]
        assert params['pairing_strategy'] == 'classified'
    # Per node, not per executable: several share one (parameter_bridge).
    untyped = [(n.node_executable, _untyped(n)) for n in nodes]
    assert [(exe, names) for exe, names in untyped if names] == []


def test_defaults_are_unchanged(monkeypatch):
    # Untyped, launch_ros YAML-parsed each default; the types must agree.
    context, nodes = _launch(monkeypatch, perception='sim', estimator='ekf')
    params = _evaluate(context, nodes)
    for exe, (_, args) in NODES.items():
        for arg in args:
            expected = yaml.safe_load(context.launch_configurations[arg])
            assert params[exe][arg] == expected, arg
            assert type(params[exe][arg]) is type(expected), arg
            assert OVERRIDES[arg][1] != expected, f'{arg} override is the default'


@pytest.mark.parametrize('exe', sorted(NODES))
def test_overrides_start_the_node_with_their_types(monkeypatch, exe):
    context, nodes = _launch(monkeypatch, perception='sim', estimator='ekf',
                             **{arg: text for arg, (text, _) in OVERRIDES.items()})
    params = _evaluate(context, nodes)[exe]
    node_class, args = NODES[exe]
    for arg in args:
        assert params[arg] == OVERRIDES[arg][1], arg
        assert type(params[arg]) is type(OVERRIDES[arg][1]), arg
    defaults = _start(node_class, {}, args)
    reached = _start(node_class, params, args)
    for arg in args:
        want = OVERRIDES[arg][1]
        assert defaults[arg] != want, f'{arg} override is the node default'
        assert reached[arg] == want, arg
        assert type(reached[arg]) is type(want), arg
