"""Keep the driving graph identical when replacing its raw cloud source."""

import importlib.util
import json
from pathlib import Path

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch.utilities import perform_substitutions
from launch_ros.utilities import evaluate_parameters, normalize_parameters
from lhr_lidar_sim.lidar_sim_node import LidarSimNode
from lhr_vehicle import load_vehicle
import pytest
import rclpy

WORLD = Path(__file__).parents[1] / 'worlds/autocross_seed1.sdf'


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
        or str(WORLD))
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
    assert sensor['parameters'][0]['world_sdf'] == str(WORLD)
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


@pytest.mark.parametrize('change', [
    lambda s: s.replace('</world>', '<model name="wall"/></world>'),
    lambda s: s.replace('model://cone_blue', 'model://unknown_obstacle'),
    lambda s: '<sdf>',
])
def test_unsupported_world_fails_before_any_node_is_constructed(monkeypatch, tmp_path, change):
    world = tmp_path / 'unsupported.sdf'
    world.write_text(change(WORLD.read_text()))
    constructed = []
    monkeypatch.setattr(NodeConfig, '__init__', lambda self, **kw: constructed.append(kw))
    with pytest.raises(ValueError, match='Unsupported Mid-360 world'):
        launch_nodes(monkeypatch, perception='lidar', lidar_source='mid360', world=str(world))
    assert not constructed


def test_caster_world_validation_does_not_restrict_gpu_worlds(monkeypatch, tmp_path):
    world = tmp_path / 'with_wall.sdf'
    world.write_text(WORLD.read_text().replace('</world>', '<model name="wall"/></world>'))
    nodes = launch_nodes(monkeypatch, perception='lidar', world=str(world))
    assert 'lidar_cone_detector' in nodes
    with pytest.raises(ValueError, match='Unsupported Mid-360 world'):
        launch_nodes(monkeypatch, perception='lidar', lidar_source='mid360',
                     world=str(tmp_path / 'missing.sdf'))


def test_explicit_missing_world_does_not_fall_back(tmp_path):
    path = Path(__file__).parents[1] / 'launch/gazebo_demo.launch.py'
    spec = importlib.util.spec_from_file_location('world_resolution', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = LaunchContext()
    context.launch_configurations['world'] = str(tmp_path / 'missing.sdf')
    with pytest.raises(ValueError, match='World file does not exist'):
        module._find_world(context)


@pytest.mark.parametrize('estimator', ['truth', 'ekf'])
@pytest.mark.parametrize('fault', ['odom_nan', 'odom_spike'])
def test_odometry_faults_leave_caster_on_clean_plant_pose(monkeypatch, estimator, fault):
    nodes = launch_nodes(monkeypatch, perception='lidar', lidar_source='mid360',
                         estimator=estimator, fault=fault)
    clean = '/lhr/vehicle/odom_truth' if estimator == 'ekf' else '/lhr/vehicle/odom_raw'
    assert ('/lhr/vehicle/odom', clean) in nodes['lidar_sim']['remappings']
    owner = 'ekf_node' if estimator == 'ekf' else 'ros_gz_bridge'
    assert ('/lhr/vehicle/odom', '/lhr/vehicle/odom_raw') in nodes[owner]['remappings']
    assert nodes['fault_injector']['parameters'][0]['fault'] == fault
    assert nodes['lidar_cone_detector'].get('remappings', []) == []


@pytest.mark.parametrize('estimator', ['truth', 'ekf'])
@pytest.mark.parametrize('override', [False, True])
def test_caster_launch_defaults_and_overrides_reach_startup(monkeypatch, estimator, override):
    world = Path(__file__).parents[1] / 'worlds/autocross_seed1.sdf'
    values = {'frame_rate_hz': '8', 'point_rate_hz': '160000', 'max_range_m': '30',
              'range_noise_std_m': '0.0075', 'dropout_rate': '0.1',
              'elevation_profile': 'uniform', 'return_profile': 'acceptance_overcast'}
    options = {f'mid360_{key}': value for key, value in values.items()} if override else {}
    nodes = launch_nodes(monkeypatch, perception='lidar', estimator=estimator,
                         lidar_source='mid360', world=str(world), seed='9' if override else '1',
                         **options)
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
        defaults = {'frame_rate_hz': 10., 'point_rate_hz': 200000., 'max_range_m': 40.,
                    'range_noise_std_m': load_vehicle().lidar_range_noise_std_m,
                    'dropout_rate': 0., 'elevation_profile': 'livox', 'return_profile': 'baseline'}
        for name, default in defaults.items():
            convert = str if isinstance(default, str) else float
            expected = convert(values[name]) if override else default
            actual = getattr(sensor._sensor.config, name)
            assert actual == expected
            assert type(params[name]) is type(expected)
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


@pytest.mark.parametrize('perception, source', [
    ('sim', 'gazebo'), ('lidar', 'gazebo'), ('lidar', 'mid360'),
])
def test_foxglove_only_adds_viewer_nodes(monkeypatch, perception, source):
    baseline = launch_nodes(monkeypatch, perception=perception, lidar_source=source)
    selected = launch_nodes(monkeypatch, perception=perception, lidar_source=source,
                            foxglove='true', foxglove_port='8767')
    assert set(selected) - set(baseline) == {'foxglove_bridge', 'vehicle_viz', 'world_viz'}
    assert all(selected[name] == config for name, config in baseline.items())
    assert selected['vehicle_viz']['parameters'][0]['use_mesh'] is False
    assert selected['world_viz']['parameters'][0]['world_sdf'] == str(WORLD)
    assert 'publish_cones' not in selected or perception == 'sim'


def test_foxglove_rejects_misspelled_boolean():
    path = Path(__file__).parents[1] / 'launch/gazebo_demo.launch.py'
    spec = importlib.util.spec_from_file_location('viewer_choices', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = LaunchContext()
    context.launch_configurations['foxglove'] = 'ture'
    with pytest.raises(RuntimeError):
        for action in module.generate_launch_description().entities:
            if isinstance(action, DeclareLaunchArgument):
                action.execute(context)


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
def test_caster_settings_do_not_change_other_sources(monkeypatch, perception):
    baseline = launch_nodes(monkeypatch, perception=perception)
    overridden = launch_nodes(monkeypatch, perception=perception,
                              mid360_max_range_m='30', mid360_range_noise_std_m='0')
    assert baseline == overridden
