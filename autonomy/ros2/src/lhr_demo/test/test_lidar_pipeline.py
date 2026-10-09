"""Exercise vendor beams through detection, boundary planning, and control."""

import importlib.util
import math
from pathlib import Path

from geometry_msgs.msg import TransformStamped
from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters
from lhr_control.pursuit_node import PurePursuit
from lhr_lidar_sim.lidar_sim_node import yaw_pitch_roll_to_quat
from lhr_lidar_sim.mid360 import Mid360Config
from lhr_lidar_sim.sensor import Mid360Sensor, MountPose
from lhr_perception.lidar_cone_detector import LidarConeDetector
from lhr_track_builder.track_builder_node import TrackBuilder
from lhr_trackgen.publish_cones import generate_autocross_track
from lhr_vehicle import load_vehicle
from nav_msgs.msg import Odometry
import numpy as np
import pytest
import rclpy
from sensor_msgs_py import point_cloud2
from std_msgs.msg import Header


class Capture:
    """Keep messages at a ROS topic seam for deterministic pipeline checks."""

    def __init__(self):
        self.messages = []

    def publish(self, message):
        """Capture a publication."""
        self.messages.append(message)


@pytest.mark.parametrize(
    'mode,cloud_only', [('sim', 'false'), ('sim', 'true'), ('lidar', 'false')])
def test_launch_selects_exactly_one_cone_source(mode, cloud_only):
    path = Path(__file__).parents[1] / 'launch' / 'mvs_demo.launch.py'
    spec = importlib.util.spec_from_file_location('mvs_launch', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    description = module.generate_launch_description()
    context = LaunchContext()
    context.launch_configurations.update(perception=mode, lidar=cloud_only)
    for action in description.entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    nodes = [action for action in description.entities if isinstance(action, Node)]
    active = {node.node_executable: node for node in nodes
              if node.condition is None or node.condition.evaluate(context)}
    assert ('sensor_sim' in active) == (mode == 'sim')
    assert ('lidar_cone_detector' in active) == (mode == 'lidar')
    assert ('lidar_sim' in active) == (mode == 'lidar' or cloud_only == 'true')
    params = evaluate_parameters(context, active['track_builder']._Node__parameters)
    expected = 'boundary' if mode == 'lidar' else 'index'
    assert params[0]['pairing_strategy'] == expected
    control_params = evaluate_parameters(context, active['pursuit_node']._Node__parameters)
    assert control_params[0]['closed_path'] == (mode == 'sim')
    assert control_params[0]['v_max'] == (4.0 if mode == 'lidar' else 12.0)


def test_imported_cloud_drives_boundary_path_and_command_without_cone_ids():
    rclpy.init()
    detector = LidarConeDetector()
    builder = TrackBuilder()
    control = PurePursuit()
    control._closed_path = False
    try:
        builder._pairing_strategy = 'boundary'
        detections, paths, commands = Capture(), Capture(), Capture()
        detector._det_pub, builder._path_pub, control._cmd_pub = detections, paths, commands
        mount = MountPose(*load_vehicle().lidar_position_m, pitch_rad=0.25)
        sensor = Mid360Sensor(Mid360Config(range_noise_std_m=0.0), mount, seed=1)
        tf = TransformStamped()
        tf.header.frame_id, tf.child_frame_id = 'base_link', 'lidar'
        tf.header.stamp.sec = 10
        tf.transform.translation.x = mount.x_m
        tf.transform.translation.y = mount.y_m
        tf.transform.translation.z = mount.z_m
        q = yaw_pitch_roll_to_quat(mount.roll_rad, mount.pitch_rad, mount.yaw_rad)
        tf.transform.rotation.x, tf.transform.rotation.y = q[0], q[1]
        tf.transform.rotation.z, tf.transform.rotation.w = q[2], q[3]
        detector._tf_buffer.set_transform(tf, 'test')
        odom = Odometry()
        odom.header.frame_id, odom.child_frame_id = 'map', 'base_link'
        odom.header.stamp.sec = 10
        odom.pose.pose.orientation.w = 1.0
        detector._odom_cb(odom)
        builder._odom_cb(odom)
        control._odom_cb(odom)
        cones = np.array([[x, y] for x in (6., 8., 10., 12., 14., 16., 18.)
                          for y in (-1.75, 1.75)])
        header = Header(frame_id='lidar')
        header.stamp.sec = 10
        for frame in range(40):
            header.stamp.sec = 10 + frame // 10
            header.stamp.nanosec = (frame % 10) * 100000000
            tf.header.stamp = header.stamp
            odom.header.stamp = header.stamp
            detector._tf_buffer.set_transform(tf, 'test')
            detector._odom_cb(odom)
            points = sensor.frame(frame / 10., 0., 0., 0., cones)
            detector._cloud_cb(point_cloud2.create_cloud_xyz32(header, points))
            detector._process()
        assert detections.messages
        detected = detections.messages[-1]
        assert all(marker.ns == 'cones' for marker in detected.markers)
        # Far sparse cones need not meet three-point evidence in a 0.5 s window.
        assert 10 <= len(detected.markers) <= len(cones)
        positions = np.array([[m.pose.position.x, m.pose.position.y] for m in detected.markers])
        for cone in cones[cones[:, 0] <= 14.]:
            assert np.linalg.norm(positions - cone, axis=1).min() < 0.15
        for position in positions:
            assert np.linalg.norm(cones - position, axis=1).min() < 0.15
        builder._cones_cb(detected)
        builder._on_timer()
        assert paths.messages and len(paths.messages[-1].poses) >= 3
        assert max(abs(p.pose.position.y) for p in paths.messages[-1].poses) < 0.15
        control._path_cb(paths.messages[-1])
        control._control_loop()
        assert commands.messages[-1].drive.speed > 0.0
        assert abs(commands.messages[-1].drive.steering_angle) < 0.1
    finally:
        for node in (detector, builder, control):
            node.destroy_node()
        rclpy.shutdown()


def test_lidar_start_is_on_generated_track_and_manual_start_is_preserved():
    path = Path(__file__).parents[1] / 'launch' / 'mvs_demo.launch.py'
    spec = importlib.util.spec_from_file_location('mvs_spawn_launch', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    context = LaunchContext()
    for action in module.generate_launch_description().entities:
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
    context.launch_configurations['perception'] = 'lidar'
    params = evaluate_parameters(context, module._launch_sim(context)[0]._Node__parameters)[0]
    left, right = generate_autocross_track(seed=1)
    centers = (np.asarray(left) + np.asarray(right)) / 2.
    position = np.array([params['init_x'], params['init_y']])
    assert np.linalg.norm(centers - position, axis=1).min() < 1e-9
    index = int(np.linalg.norm(centers - position, axis=1).argmin())
    tangent = centers[(index + 1) % len(centers)] - centers[index - 1]
    heading = np.array([math.cos(params['init_yaw']), math.sin(params['init_yaw'])])
    assert float(heading @ tangent / np.linalg.norm(tangent)) > 0.999
    context.launch_configurations['start_on_track'] = 'false'
    manual = evaluate_parameters(context, module._launch_sim(context)[0]._Node__parameters)[0]
    assert (manual['init_x'], manual['init_y'], manual['init_yaw']) == (25., 0., 1.5708)
