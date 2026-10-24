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
from lhr_sim_kinematic.sim_node import SimKinematic
from lhr_state_estimation.ekf_node import EkfNode
from lhr_trackgen.publish_cones import ConePublisher
import pytest
import rclpy
import yaml

# Executable -> (node class, launch arguments it gets under the same name).
NODES = {
    'publish_cones': (ConePublisher, ['seed', 'track_style', 'num_waypoints']),
    'sensor_sim': (SensorSim, ['fov_deg', 'max_range_m']),
    'sim_node': (SimKinematic, ['init_x', 'init_y', 'init_yaw']),
    'inertial_sim': (InertialSim, ['gyro_bias', 'wheel_scale_error']),
    'ekf_node': (EkfNode, ['init_x', 'init_y', 'init_yaw']),
    'pursuit_node': (PurePursuit, [
        'lookahead_dist', 'a_lat_max', 'v_min', 'v_max', 'max_accel',
        'max_decel']),
    'mission_manager': (MissionManager, ['mission', 'auto_go', 'ready_hold_sec']),
}

# Argument -> (command-line text, value the node must see). Each differs from
# both the launch default and the node's own, and every double is written as
# a whole number: untyped, "8" arrived as an INTEGER and killed the node (#130).
OVERRIDES = {
    'seed': ('7', 7),
    'track_style': ('oval', 'oval'),
    'num_waypoints': ('12', 12),
    'fov_deg': ('360', 360.0),
    'max_range_m': ('30', 30.0),
    'init_x': ('3', 3.0),
    'init_y': ('4', 4.0),
    'init_yaw': ('1', 1.0),
    'gyro_bias': ('1', 1.0),
    'wheel_scale_error': ('2', 2.0),
    'lookahead_dist': ('6', 6.0),
    'a_lat_max': ('4', 4.0),
    'v_min': ('3', 3.0),
    'v_max': ('8', 8.0),
    'max_accel': ('3', 3.0),
    'max_decel': ('4', 4.0),
    'mission': ('skidpad', 'skidpad'),
    'auto_go': ('false', False),
    'ready_hold_sec': ('0', 0.0),
}

# Conditions pick among these at run time, so the description always holds
# all of them. The plant and estimator nodes come from _launch_sim instead.
TOP_LEVEL = ['vehicle_viz', 'publish_cones', 'sensor_sim', 'track_builder',
             'pursuit_node', 'metrics_node', 'mission_manager', 'lidar_sim',
             'lidar_cone_detector', 'foxglove_bridge']
PLANT = {'truth': ['sim_node'],
         'ekf': ['sim_node', 'inertial_sim', 'ekf_node']}

# Launch configurations that between them reach every branch.
CONFIGS = [
    {},
    {'perception': 'lidar', 'estimator': 'ekf'},
    {'plant': 'bobsim', 'estimator': 'ekf'},
]


def _launch(**overrides):
    """Evaluate the launch arguments with overrides; return the nodes."""
    path = Path(__file__).parents[1] / 'launch/mvs_demo.launch.py'
    spec = importlib.util.spec_from_file_location('mvs_demo', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    entities = module.generate_launch_description().entities
    context = LaunchContext()
    for action in entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    # A misspelled or renamed argument would otherwise be silently ignored.
    assert set(overrides) <= set(context.launch_configurations)
    context.launch_configurations.update(overrides)
    nodes = [a for a in entities if isinstance(a, Node)]
    nodes += [a for a in module._launch_sim(context) if isinstance(a, Node)]
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


def _evaluate(context, nodes, exe):
    """Evaluate the parameters of every node running exe."""
    found = [evaluate_parameters(context, n._Node__parameters)[0]
             for n in nodes if n.node_executable == exe]
    assert found, exe
    return found


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


@pytest.mark.parametrize('config', CONFIGS)
def test_every_forwarded_argument_is_typed(config):
    context, nodes = _launch(**config)
    estimator = context.launch_configurations['estimator']
    assert Counter(n.node_executable for n in nodes) == Counter(
        TOP_LEVEL + PLANT[estimator])
    # Per node, not per executable, so no node can hide behind another.
    untyped = [(n.node_executable, _untyped(n)) for n in nodes]
    assert [(exe, names) for exe, names in untyped if names] == []
    # _launch_sim reads its arguments with perform() and hands over plain
    # values, which the check above cannot see. None of them is text, and
    # the plant really is the one asked for.
    plant = context.launch_configurations['plant']
    for node in nodes:
        if node.node_executable not in PLANT[estimator]:
            continue
        if node.node_executable == 'sim_node':
            assert node.node_package == 'lhr_sim_' + plant
        params = evaluate_parameters(context, node._Node__parameters)[0]
        text = [name for name, value in params.items() if isinstance(value, str)]
        assert text == [], node.node_executable


@pytest.mark.parametrize('spelling', ['True', '1'])
def test_sim_time_reaches_the_plant_as_the_other_nodes_read_it(spelling):
    # Compared against 'true', these left the plant off /clock while every
    # node typed through _b waited on it.
    context, nodes = _launch(use_sim_time=spelling, estimator='ekf')
    (sim,) = _evaluate(context, nodes, 'sim_node')
    assert sim['publish_clock'] is True
    for exe in ('inertial_sim', 'ekf_node'):
        (params,) = _evaluate(context, nodes, exe)
        assert params['use_sim_time'] is True, exe


def test_defaults_are_unchanged():
    # Untyped, launch_ros YAML-parsed each default; the types must agree.
    # The EKF branch is the one that starts every node class below.
    # Raw init arguments are forwarded only in explicit manual-spawn mode.
    context, nodes = _launch(estimator='ekf', start_on_track='false')
    for exe, (_, args) in NODES.items():
        for params in _evaluate(context, nodes, exe):
            for arg in args:
                expected = yaml.safe_load(context.launch_configurations[arg])
                assert params[arg] == expected, arg
                assert type(params[arg]) is type(expected), arg
                assert OVERRIDES[arg][1] != expected, \
                    f'{arg} override is the default'


@pytest.mark.parametrize('exe', sorted(NODES))
def test_overrides_start_the_node_with_their_types(exe):
    context, nodes = _launch(
        estimator='ekf', start_on_track='false',
        **{arg: text for arg, (text, _) in OVERRIDES.items()})
    node_class, args = NODES[exe]
    defaults = _start(node_class, {}, args)
    for params in _evaluate(context, nodes, exe):
        for arg in args:
            assert params[arg] == OVERRIDES[arg][1], arg
            assert type(params[arg]) is type(OVERRIDES[arg][1]), arg
        reached = _start(node_class, params, args)
        for arg in args:
            want = OVERRIDES[arg][1]
            assert defaults[arg] != want, f'{arg} override is the node default'
            assert reached[arg] == want, arg
            assert type(reached[arg]) is type(want), arg
