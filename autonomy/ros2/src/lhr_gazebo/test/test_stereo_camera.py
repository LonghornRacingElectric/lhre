"""Check stereo frames, bridge calibration contracts, and launch selection."""

from dataclasses import replace
import importlib
import importlib.util
import math
from pathlib import Path

from lhr_vehicle import load_vehicle
import numpy as np
import pytest
from scipy.spatial.transform import Rotation
import yaml

PKG = Path(__file__).resolve().parents[1]


def _camera_module():
    spec = importlib.util.find_spec('lhr_gazebo.stereo_camera')
    assert spec is not None, 'Stereo integration module is missing'
    return importlib.import_module('lhr_gazebo.stereo_camera')


def test_optical_axes_and_rotated_mount():
    """Rig yaw must rotate baseline with the car and keep optical axes correct."""
    camera = replace(load_vehicle().stereo_camera, position_m=(1., 2., 3.),
                     rpy_rad=(0., 0., math.pi / 2))
    by_child = {t['child']: t for t in _camera_module().camera_transforms(camera)}
    assert len(by_child) == 5
    rig = by_child['zed2i_link']
    assert rig['parent'] == 'base_link'
    assert rig['translation'] == (1., 2., 3.)
    rig_rotation = Rotation.from_euler('xyz', rig['rpy']).as_matrix()
    for side, expected_x in (('left', .94), ('right', 1.06)):
        frame = by_child[f'zed2i_{side}_camera_frame']
        assert frame['parent'] == 'zed2i_link'
        world_position = np.array(rig['translation']) + rig_rotation @ frame['translation']
        assert world_position == pytest.approx([expected_x, 2., 3.])
        optical = by_child[f'zed2i_{side}_camera_optical_frame']
        assert optical['parent'] == frame['child']
        axes = Rotation.from_euler('xyz', optical['rpy']).as_matrix()
        assert axes @ [0, 0, 1] == pytest.approx([1, 0, 0], abs=1e-12)
        assert axes @ [1, 0, 0] == pytest.approx([0, -1, 0], abs=1e-12)
        assert axes @ [0, 1, 0] == pytest.approx([0, 0, -1], abs=1e-12)


def test_bridge_topics_match_rendered_sensors():
    """Every rendered image/calibration stream must reach a unique ROS topic."""
    path = PKG / 'config' / 'ros_gz_stereo_bridge.yaml'
    assert path.exists()
    mappings = yaml.safe_load(path.read_text())
    assert len(mappings) == 4
    for side in ('left', 'right'):
        for stream, msg in (('image_rect', 'Image'), ('camera_info', 'CameraInfo')):
            ros_topic = f'/lhr/sensor/zed2i/{side}/{stream}'
            matched = [m for m in mappings if m['ros_topic_name'] == ros_topic]
            assert len(matched) == 1
            mapping = matched[0]
            assert mapping['gz_topic_name'] == f'/zed2i/{side}/{stream}'
            assert mapping['ros_type_name'] == f'sensor_msgs/msg/{msg}'
            assert mapping['gz_type_name'] == f'gz.msgs.{msg}'
            assert mapping['direction'] == 'GZ_TO_ROS'
            assert mapping['qos_profile'] == 'SENSOR_DATA'
            assert mapping['lazy'] is True


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
@pytest.mark.parametrize('estimator', ['truth', 'ekf'])
@pytest.mark.parametrize('enabled', [None, 'false', 'true'])
def test_launch_camera_actions_and_rviz(perception, estimator, enabled, monkeypatch):
    """Stereo is independently opt-in and never redirects its TF to truth TF."""
    pytest.importorskip('launch_ros')
    launch = pytest.importorskip('launch')
    from launch.actions import DeclareLaunchArgument
    from launch.utilities import normalize_to_list_of_substitutions, perform_substitutions
    from launch_ros.actions import Node
    from launch_ros.utilities import evaluate_parameters
    spec = importlib.util.spec_from_file_location(
        'gazebo_stereo_launch', PKG / 'launch' / 'gazebo_demo.launch.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_find_world', lambda _: '/tmp/world.sdf')
    monkeypatch.setattr(module, 'get_package_share_directory', lambda _: str(PKG))
    context = launch.LaunchContext()
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    context.launch_configurations.update(perception=perception, estimator=estimator)
    if enabled is not None:
        context.launch_configurations['enable_stereo_camera'] = enabled
    actions = module._launch_setup(context)
    nodes = [a for a in actions if isinstance(a, Node)]
    camera_nodes = [n for n in nodes if n._Node__node_name.startswith('zed2i_')]
    assert len(camera_nodes) == (6 if enabled == 'true' else 0)
    if camera_nodes:
        tf_nodes = [n for n in camera_nodes if n.node_package == 'tf2_ros']
        assert len(tf_nodes) == 5
        assert all(n.node_executable == 'static_transform_publisher' for n in tf_nodes)
        bridge = next(n for n in camera_nodes if n.node_package == 'ros_gz_bridge')
        params = evaluate_parameters(context, bridge._Node__parameters)[0]
        assert params['config_file'] == str(PKG / 'config' / 'ros_gz_stereo_bridge.yaml')
        assert params['use_sim_time'] is True
        assert all(not n._Node__remappings for n in tf_nodes)
    rviz = next(n for n in nodes if n.node_package == 'rviz2')
    arguments = [
        perform_substitutions(context, normalize_to_list_of_substitutions(arg))
        for arg in rviz._Node__arguments
    ]
    assert arguments[-1] == str(PKG / 'config' / (
        'stereo.rviz' if enabled == 'true' else 'default.rviz'))


def test_stereo_rviz_preserves_existing_displays():
    """Adding image panels must preserve the existing driving visualizations."""
    path = PKG / 'config' / 'stereo.rviz'
    assert path.exists()
    stereo = yaml.safe_load(path.read_text())['Visualization Manager']['Displays']
    original = yaml.safe_load((PKG / 'config' / 'default.rviz').read_text())
    images = [d for d in stereo if d['Class'] == 'rviz_default_plugins/Image']
    driving = [d for d in stereo if d['Class'] != 'rviz_default_plugins/Image']
    assert driving == original['Visualization Manager']['Displays']
    assert [d['Topic']['Value'] for d in images] == [
        '/lhr/sensor/zed2i/left/image_rect', '/lhr/sensor/zed2i/right/image_rect',
        '/lhr/perception/cone_colors/debug_image']
    assert all(d['Topic']['Reliability Policy'] == 'Best Effort' for d in images)
