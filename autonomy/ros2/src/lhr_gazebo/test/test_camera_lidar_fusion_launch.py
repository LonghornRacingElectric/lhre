"""Evaluate opt-in fusion and start the node with actual launch parameters."""

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def launch_context(monkeypatch):
    """Load Gazebo launch with its real argument declarations and resources."""
    launch = pytest.importorskip('launch')
    pytest.importorskip('launch_ros')
    from launch.actions import DeclareLaunchArgument
    path = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location(
        'gazebo_fusion_launch', path / 'launch/gazebo_demo.launch.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_find_world', lambda _: '/tmp/world.sdf')
    monkeypatch.setattr(module, 'get_package_share_directory', lambda _: str(path))
    context = launch.LaunchContext()
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    return module, context


@pytest.mark.parametrize('perception', ['sim', 'lidar'])
@pytest.mark.parametrize('estimator', ['truth', 'ekf'])
def test_fusion_disabled_preserves_planner_topic(launch_context, perception, estimator):
    """Disabled fusion adds no node and preserves existing perception wiring."""
    from launch_ros.actions import Node
    from launch_ros.utilities import evaluate_parameters
    module, context = launch_context
    context.launch_configurations.update(perception=perception, estimator=estimator)
    nodes = [n for n in module._launch_setup(context) if isinstance(n, Node)]
    assert not any(n.node_executable == 'camera_lidar_fusion' for n in nodes)
    track_builder = next(n for n in nodes if n.node_executable == 'track_builder')
    params = evaluate_parameters(context, track_builder._Node__parameters)[0]
    assert params.get('cone_topic', '/lhr/sensor/cones_detected') == '/lhr/sensor/cones_detected'


@pytest.mark.parametrize('perception,stereo', [('sim', 'true'), ('lidar', 'false')])
def test_fusion_rejects_unsupported_inputs(launch_context, perception, stereo):
    """Fusion cannot silently wait forever for an absent LiDAR or camera."""
    module, context = launch_context
    context.launch_configurations.update(
        perception=perception, enable_stereo_camera=stereo, enable_camera_lidar_fusion='true')
    with pytest.raises(RuntimeError, match='perception:=lidar|enable_stereo_camera'):
        module._launch_setup(context)


@pytest.mark.parametrize('estimator', ['truth', 'ekf'])
@pytest.mark.parametrize('debug_classifier', ['false', 'true'])
@pytest.mark.parametrize('override', [False, True])
def test_fusion_parameters_reach_node(launch_context, estimator, debug_classifier, override):
    """Every typed override must reach startup even with the debug classifier off."""
    rclpy = pytest.importorskip('rclpy')
    from launch_ros.actions import Node
    from launch_ros.utilities import evaluate_parameters
    from lhr_perception.camera_lidar_fusion import CameraLidarFusion
    from lhr_perception.camera_lidar_geometry import FUSION_DEFAULTS
    module, context = launch_context
    context.launch_configurations.update(
        perception='lidar', estimator=estimator, enable_stereo_camera='true',
        enable_camera_lidar_fusion='true', enable_camera_color_classifier=debug_classifier)
    expected = dict(FUSION_DEFAULTS)
    if override:
        expected.update(process_hz=6., map_max_age_sec=.8, camera_max_age_sec=.9,
                        sample_height_m=.2,
                        max_color_range_m=12., pixel_margin=3., vote_window=7,
                        min_color_votes=4, min_color_fraction=.8, id_reset_distance_m=.7)
        context.launch_configurations.update(
            {f'fusion_{name}': str(value) for name, value in expected.items()})
        context.launch_configurations.update(
            cone_color_min_region_area='50', cone_color_blue_hsv='[95, 80, 40, 135, 255, 255]')
    nodes = [n for n in module._launch_setup(context) if isinstance(n, Node)]
    action = next(n for n in nodes if n.node_executable == 'camera_lidar_fusion')
    params = evaluate_parameters(context, action._Node__parameters)[0]
    assert params['use_sim_time'] is True
    expected.update(min_region_area=50 if override else 25,
                    blue_hsv=[95, 80, 40, 135, 255, 255] if override
                    else [100, 100, 50, 130, 255, 255])
    args = ['--ros-args']
    for name, value in params.items():
        args.extend(['-p', f'{name}:={json.dumps(value)}'])
    rclpy.init(args=args)
    node = None
    try:
        node = CameraLidarFusion()
        for name, value in expected.items():
            actual = node.get_parameter(name).value
            if isinstance(value, list):
                assert list(actual) == list(params[name]) == value
            else:
                assert type(params[name]) is type(value)
                assert actual == params[name] == value
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


@pytest.mark.parametrize('estimator', ['truth', 'ekf'])
@pytest.mark.parametrize('fusion', [False, True])
@pytest.mark.parametrize('side_argument', ['false', 'true'])
def test_fusion_wires_planner_and_enables_fallback(
        launch_context, estimator, fusion, side_argument):
    """Evaluated planner inputs must select fusion, boundary pairing and fallback together."""
    rclpy = pytest.importorskip('rclpy')
    from launch_ros.actions import Node
    from launch_ros.utilities import evaluate_parameters
    from lhr_track_builder.track_builder_node import TrackBuilder
    module, context = launch_context
    context.launch_configurations.update(
        perception='lidar', estimator=estimator,
        enable_stereo_camera=str(fusion).lower(), enable_camera_lidar_fusion=str(fusion).lower(),
        lidar_classify_sides=side_argument)
    nodes = [n for n in module._launch_setup(context) if isinstance(n, Node)]
    planner_action = next(n for n in nodes if n.node_executable == 'track_builder')
    detector_action = next(n for n in nodes if n.node_executable == 'lidar_cone_detector')
    params = evaluate_parameters(context, planner_action._Node__parameters)[0]
    detector_params = evaluate_parameters(context, detector_action._Node__parameters)[0]
    topic = '/lhr/perception/cones_classified' if fusion else '/lhr/sensor/cones_detected'
    assert params.get('cone_topic', '/lhr/sensor/cones_detected') == topic
    assert params['pairing_strategy'] == (
        'classified' if not fusion and side_argument == 'true' else 'boundary')
    assert params.get('boundary_use_color', True) is True
    assert detector_params['classify_sides'] is (fusion or side_argument == 'true')
    args = ['--ros-args']
    for name, value in params.items():
        args.extend(['-p', f'{name}:={json.dumps(value)}'])
    rclpy.init(args=args, domain_id=85)
    planner = None
    try:
        planner = TrackBuilder()
        subscriptions = list(planner.subscriptions)
        assert any(s.topic_name == topic for s in subscriptions)
        assert planner._pairing_strategy == params['pairing_strategy']
        assert planner._boundary_use_color is True
    finally:
        if planner is not None:
            planner.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def test_camera_fusion_topic_drives_color_pairing_and_recovers_from_camera_loss(launch_context):
    """Real images, TF and DDS must deliver camera/fallback labels to boundary pairing."""
    from copy import deepcopy
    import time

    from geometry_msgs.msg import TransformStamped
    from launch_ros.actions import Node
    from launch_ros.utilities import evaluate_parameters
    from lhr_perception.camera_lidar_fusion import CameraLidarFusion
    from lhr_track_builder.track_builder_node import TrackBuilder
    import numpy as np
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.time import Time
    from scipy.spatial.transform import Rotation
    from sensor_msgs.msg import CameraInfo, Image
    from visualization_msgs.msg import Marker, MarkerArray

    module, context = launch_context
    context.launch_configurations.update(
        perception='lidar', enable_stereo_camera='true', enable_camera_lidar_fusion='true')
    action = next(n for n in module._launch_setup(context)
                  if isinstance(n, Node) and n.node_executable == 'track_builder')
    params = evaluate_parameters(context, action._Node__parameters)[0]
    args = ['--ros-args']
    for name, value in params.items():
        args.extend(['-p', f'{name}:={json.dumps(value)}'])
    rclpy.init(args=args, domain_id=86)
    fusion = planner = executor = None
    # Deliberately reverse source sides so camera priority is observable.
    cones = [(7, 6., 1.75, 'right_cones', (0, 0, 255)),
             (8, 6., -1.75, 'left_cones', (255, 255, 0)),
             (9, 9.5, 1.75, 'right_cones', (0, 0, 255)),
             (10, 9.5, -1.75, 'left_cones', (255, 255, 0)),
             (11, 7.75, 0., 'cones', (255, 100, 0))]
    try:
        fusion, planner = CameraLidarFusion(), TrackBuilder()
        executor = SingleThreadedExecutor()
        executor.add_node(fusion)
        executor.add_node(planner)

        def feed(stamp, *, blank=False, conflict=False):
            fusion.get_clock().set_ros_time_override(Time(seconds=stamp))
            source = MarkerArray()
            rgb = np.zeros((80, 100, 3), dtype=np.uint8)
            for cone_id, x, y, namespace, color in cones:
                marker = Marker(id=cone_id, ns=namespace, type=Marker.SPHERE)
                marker.header.frame_id, marker.header.stamp.sec = 'map', stamp
                marker.pose.position.x, marker.pose.position.y = x, y
                marker.pose.orientation.w = 1.
                marker.scale.x = marker.scale.y = marker.scale.z = .35
                source.markers.append(marker)
                if not blank:
                    u = round(50. - 100. * y / x)
                    rgb[37:44, u-3:u+4] = (255, 255, 0) if conflict and cone_id == 7 else color
            fusion._map_cb(source)
            info = CameraInfo(width=100, height=80)
            info.header.frame_id, info.header.stamp.sec = 'camera_optical', stamp
            info.p = [100., 0., 50., 0., 0., 100., 40., 0., 0., 0., 1., 0.]
            info.r = [1., 0., 0., 0., 1., 0., 0., 0., 1.]
            info.d = [0.] * 5
            image = Image(width=100, height=80, step=300, encoding='rgb8', data=rgb.tobytes())
            image.header = deepcopy(info.header)
            transform = TransformStamped()
            transform.header = deepcopy(info.header)
            transform.child_frame_id = 'map'
            q = Rotation.from_matrix([[0., -1., 0.], [0., 0., -1.], [1., 0., 0.]]).as_quat()
            rotation = transform.transform.rotation
            rotation.x, rotation.y, rotation.z, rotation.w = map(float, q)
            transform.transform.translation.y = fusion.get_parameter('sample_height_m').value
            fusion._tf_buffer.set_transform(transform, 'test')
            fusion._info_cb(info)
            fusion._image_cb(image)
            fusion._process()

        def wait_for_sides(left, right):
            deadline = time.monotonic() + 3.
            while time.monotonic() < deadline:
                executor.spin_once(timeout_sec=.02)
                if set(planner._left_cones) == left and set(planner._right_cones) == right:
                    break
            assert set(planner._left_cones) == left
            assert set(planner._right_cones) == right
            assert 11 not in planner._unknown_cones

        for stamp in (10, 11, 12):
            feed(stamp)
        wait_for_sides({7, 9}, {8, 10})
        assert set(planner._compute_midpoints()) == {(6., 0.), (9.5, 0.)}
        feed(13, conflict=True)
        wait_for_sides({9}, {7, 8, 10})
        feed(14, blank=True)
        wait_for_sides({8, 10}, {7, 9})
        assert set(planner._compute_midpoints()) == {(6., 0.), (9.5, 0.)}
        feed(15)
        wait_for_sides({7, 9}, {8, 10})
    finally:
        if executor is not None:
            executor.shutdown()
        if planner is not None:
            planner.destroy_node()
        if fusion is not None:
            fusion.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
