"""The FMU must own all motion/truth in both Gazebo perception branches."""

import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument, RegisterEventHandler, SetEnvironmentVariable
from launch.events import Shutdown
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters
from lhr_gazebo.fmu_launch import world_spawn
import pytest
import yaml

PKG = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
@pytest.mark.parametrize('estimator', ['truth', 'ekf'])
@pytest.mark.parametrize('stereo', ['false', 'true'])
@pytest.mark.parametrize('override', [False, True])
def test_fmu_launch_owns_truth_and_sensor_model(
        perception, estimator, stereo, override, monkeypatch):
    spec = importlib.util.spec_from_file_location('fmu_launch', PKG/'launch/gazebo_demo.launch.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'get_package_share_directory', lambda _: str(PKG))
    context = LaunchContext()
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    context.launch_configurations.update(plant='fmu', perception=perception, estimator=estimator,
                                         enable_stereo_camera=stereo)
    if override:
        context.launch_configurations.update(fmu_path='/tmp/a.fmu', fmu_pedal_gain='1',
                                             fmu_command_timeout_sec='2', fmu_pose_timeout_sec='9')
    actions = module._launch_setup(context)
    nodes = {n._Node__node_name: n for n in actions if isinstance(n, Node)}
    assert 'joint_cmd_adapter' not in nodes
    plant = evaluate_parameters(context, nodes['sim_fmu']._Node__parameters)[0]
    nodes['sim_fmu']._perform_substitutions(context)
    remaps = nodes['sim_fmu'].expanded_remapping_rules
    expected_truth = '/lhr/vehicle/odom_truth' if estimator == 'ekf' else '/lhr/vehicle/odom'
    assert ('/lhr/vehicle/odom', expected_truth) in remaps
    assert (('/tf', '/tf_truth') in remaps) == (estimator == 'ekf')
    assert plant['external_clock'] is True and plant['publish_clock'] is False
    assert plant['use_sim_time'] is True
    assert plant['pedal_gain'] == (1. if override else .1)
    assert type(plant['pedal_gain']) is float
    assert plant['command_timeout_sec'] == (2. if override else .5)
    assert plant['fmu_path'] == ('/tmp/a.fmu' if override else '')
    # Start the real ROS node with an injected plant to verify declared types;
    # this is interface testing, never a substitute physics acceptance run.
    from types import SimpleNamespace
    from lhr_sim_fmu.sim_node import SimFmu
    import rclpy
    monkeypatch.setattr('lhr_sim_fmu.sim_node.FmuPlant', lambda *a, **k: SimpleNamespace(
        provenance={}, time=0., close=lambda: None))
    args = ['--ros-args']
    for name, value in plant.items():
        args.extend(['-p', f'{name}:={json.dumps(value)}'])
    rclpy.init(args=args)
    node = SimFmu()
    try:
        for name in ('pedal_gain', 'command_timeout_sec', 'fmu_path', 'external_clock'):
            assert node.get_parameter(name).value == plant[name]
        assert node._timer.is_canceled()
    finally:
        node.destroy_node()
        rclpy.shutdown()
    pose = evaluate_parameters(context, nodes['fmu_pose']._Node__parameters)[0]
    assert pose['request_timeout_sec'] == (9. if override else 5.)
    assert pose['truth_topic'] == ('/lhr/vehicle/odom_truth' if estimator == 'ekf'
                                   else '/lhr/vehicle/odom')
    metrics = evaluate_parameters(context, nodes['metrics_node']._Node__parameters)[0]
    assert metrics['plant'] == 'fmu' and metrics['fmu_pedal_gain'] == plant['pedal_gain']
    bridge = evaluate_parameters(context, nodes['ros_gz_bridge']._Node__parameters)[0]
    mappings = yaml.safe_load(Path(bridge['config_file']).read_text())
    assert {m['ros_topic_name'] for m in mappings} == {
        '/clock', '/lhr/lidar/points', '/lhr/sim/render_pose'}
    if estimator == 'ekf':
        sensor = evaluate_parameters(context, nodes['inertial_sim']._Node__parameters)[0]
        assert sensor['publish_imu'] is True
    for action in actions:
        if isinstance(action, SetEnvironmentVariable):
            action.execute(context)
    model_path = Path(context.environment['GZ_SIM_RESOURCE_PATH'].split(':')[0])
    model = ET.parse(model_path/'fsae_vehicle/model.sdf')
    assert model.findtext('model/static') == 'true'
    assert len(model.findall(".//sensor[@type='camera']")) == (2 if stereo == 'true' else 0)
    for action in actions:
        if isinstance(action, RegisterEventHandler):
            # Only run the cleanup handler; process-exit guards need a process event.
            from launch.event_handlers import OnShutdown
            if isinstance(action.event_handler, OnShutdown):
                action.event_handler.handle(Shutdown(reason='test'), context)


def test_spawn_is_the_saved_world_pose():
    world, pose = world_spawn(PKG/'worlds/autocross_seed1.sdf')
    assert world == 'fsae_world'
    assert pose == pytest.approx((18.1095, -14.7442, .9405))
