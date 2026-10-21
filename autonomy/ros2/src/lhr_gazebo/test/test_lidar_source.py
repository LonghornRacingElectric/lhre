"""Keep the driving graph identical when replacing its raw cloud source."""

import importlib.util
import json
from pathlib import Path

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch.utilities import perform_substitutions
from launch_ros.utilities import evaluate_parameters, normalize_parameters
from lhr_lidar_sim.lidar_sim_node import LidarSimNode
import pytest
import rclpy


class NodeConfig:
    """Capture launch wiring without launching processes."""

    def __init__(self, **kwargs):
        self.config = kwargs


def launch_nodes(monkeypatch, **overrides):
    """Evaluate the real launch defaults and requested overrides."""
    path = Path(__file__).parents[1] / 'launch/gazebo_demo.launch.py'
    spec = importlib.util.spec_from_file_location('gazebo_source', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'Node', NodeConfig)
    monkeypatch.setattr(
        module, '_find_world', lambda context: context.launch_configurations.get('world')
        or '/tmp/actual_loaded_world.sdf')
    monkeypatch.setattr(module, 'get_package_share_directory', lambda _: '/tmp/share')
    context = LaunchContext()
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    context.launch_configurations.update(overrides)
    nodes = {}
    for action in module._launch_setup(context):
        if not isinstance(action, NodeConfig):
            continue
        config = action.config.copy()
        config['parameters'] = evaluate_parameters(
            context, normalize_parameters(config['parameters']))
        config['remappings'] = [
            tuple(perform_substitutions(context, [v]) if not isinstance(v, str) else v
                  for v in pair) for pair in config.get('remappings', [])]
        nodes[config['name']] = config
    return nodes


@pytest.mark.parametrize('estimator', ['truth', 'ekf'])
@pytest.mark.parametrize('classified', ['false', 'true'])
def test_source_changes_only_cloud_wiring(monkeypatch, estimator, classified):
    options = {'perception': 'lidar', 'estimator': estimator,
               'lidar_classify_sides': classified}
    baseline = launch_nodes(monkeypatch, **options)
    selected = launch_nodes(monkeypatch, lidar_source='mid360', **options)
    assert set(selected) - set(baseline) == {'lidar_sim'}
    assert not any(node['executable'] == 'static_transform_publisher'
                   for node in selected.values())
    for name in baseline.keys() - {'ros_gz_bridge'}:
        assert selected[name] == baseline[name], f'Driving node changed: {name}'
    bridge = selected['ros_gz_bridge']
    assert bridge['remappings'] == baseline['ros_gz_bridge']['remappings'] + [
        ('/lhr/lidar/points', '/lhr/lidar/gazebo_points')]
    sensor = selected['lidar_sim']
    assert sensor['parameters'][0]['world_sdf'] == '/tmp/actual_loaded_world.sdf'
    assert sensor['parameters'][0]['seed'] == 1
    assert sensor['parameters'][0]['use_sim_time'] is True
    assert sensor['remappings'] == [('/lhr/vehicle/odom',
                                     '/lhr/vehicle/odom_truth' if estimator == 'ekf'
                                     else '/lhr/vehicle/odom')]
    assert 'sensor_sim' not in selected
    assert 'publish_cones' not in selected


@pytest.mark.parametrize('port', [None, '8771'])
def test_viewer_port_and_seed_retain_integer_types(monkeypatch, port):
    overrides = {'foxglove_port': port} if port else {}
    nodes = launch_nodes(monkeypatch, perception='lidar', lidar_source='mid360',
                         seed='9', foxglove='true', **overrides)
    assert nodes['lidar_sim']['parameters'][0]['seed'] == 9
    actual_port = nodes['foxglove_bridge']['parameters'][0]['port']
    assert type(actual_port) is int
    assert actual_port == (int(port) if port else 8765)


@pytest.mark.parametrize('overrides', [
    {'lidar_source': 'unknown'}, {'lidar_source': 'mid360', 'perception': 'sim'},
])
def test_rejects_invalid_source_modes(monkeypatch, overrides):
    with pytest.raises(ValueError):
        launch_nodes(monkeypatch, **overrides)


@pytest.mark.parametrize('estimator', ['truth', 'ekf'])
@pytest.mark.parametrize('override', [False, True])
def test_caster_launch_defaults_and_overrides_reach_startup(monkeypatch, estimator, override):
    world = Path(__file__).parents[1] / 'worlds/autocross_seed1.sdf'
    nodes = launch_nodes(monkeypatch, perception='lidar', estimator=estimator,
                         lidar_source='mid360', world=str(world), seed='9' if override else '1')
    params = nodes['lidar_sim']['parameters'][0]
    args = ['--ros-args']
    for key, value in params.items():
        args += ['-p', f'{key}:={json.dumps(value)}']
    rclpy.init(args=args)
    sensor = LidarSimNode()
    try:
        assert sensor._world_sdf == str(world)
        assert sensor._sensor.seed == (9 if override else 1)
        assert sensor.get_parameter('use_sim_time').value is True
        assert '/lhr/track/cones' not in [s.topic_name for s in sensor.subscriptions]
    finally:
        sensor.destroy_node()
        rclpy.shutdown()


@pytest.mark.parametrize('source', ['gazebo', 'mid360'])
@pytest.mark.parametrize('estimator', ['truth', 'ekf'])
def test_lidar_drop_intercepts_the_selected_source(monkeypatch, source, estimator):
    nodes = launch_nodes(monkeypatch, perception='lidar', lidar_source=source,
                         estimator=estimator, fault='lidar_drop')
    raw = ('/lhr/lidar/points', '/lhr/lidar/points_raw')
    bridge = nodes['ros_gz_bridge']['remappings']
    if source == 'mid360':
        assert raw in nodes['lidar_sim']['remappings']
        assert raw not in bridge
        assert ('/lhr/lidar/points', '/lhr/lidar/gazebo_points') in bridge
    else:
        assert raw in bridge
    assert 'fault_injector' in nodes


@pytest.mark.parametrize('perception, source', [('sim', 'gazebo'), ('lidar', 'mid360')])
def test_foxglove_only_adds_viewer_nodes(monkeypatch, perception, source):
    baseline = launch_nodes(monkeypatch, perception=perception, lidar_source=source)
    selected = launch_nodes(monkeypatch, perception=perception, lidar_source=source,
                            foxglove='true', foxglove_port='8767')
    assert set(selected) - set(baseline) == {'foxglove_bridge', 'vehicle_viz'}
    assert all(selected[name] == config for name, config in baseline.items())
    assert selected['vehicle_viz']['parameters'][0]['use_mesh'] is False
