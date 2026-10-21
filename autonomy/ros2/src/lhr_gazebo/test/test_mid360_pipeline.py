"""Feed real caster clouds through the unchanged cone detector."""

from pathlib import Path
from unittest.mock import Mock

from lhr_lidar_sim.lidar_sim_node import LidarSimNode
from lhr_lidar_sim.world import load_world_cones
from lhr_perception.lidar_cone_detector import LidarConeDetector
from nav_msgs.msg import Odometry
import numpy as np
import pytest
import rclpy
from rclpy.parameter import Parameter
from sensor_msgs_py import point_cloud2


def test_world_clouds_reach_existing_detector_without_labels(tmp_path, monkeypatch):
    """Check stamps, paused truth, mount ownership, and actual geometric detections."""
    world = tmp_path / 'custom.sdf'
    world.write_text("""<sdf version="1.9"><world name="test">
      <model name="ground_plane"><link name="link"><collision name="ground">
        <geometry><plane><normal>0 0 1</normal></plane></geometry>
      </collision></link></model>
      <include><uri>model://cone_blue</uri><pose>8 1.75 .1625 0 0 0</pose></include>
      <include><uri>model://cone_yellow</uri><pose>8 -1.75 .1625 0 0 0</pose></include>
      </world></sdf>""")
    rclpy.init(args=['--ros-args', '-p', f'world_sdf:={world}',
                     '-p', 'range_noise_std_m:=0.0', '-p', 'classify_sides:=false',
                     '-p', 'seed:=9', '-p', 'sensor_transform_source:=vehicle_config',
                     '-p', 'height_filter_frame:=sensor', '-p', 'ground_z_min:=-0.4',
                     '-p', 'min_cluster_points:=1', '-p', 'stack_window_sec:=0.0'])
    sensor = LidarSimNode()
    detector = LidarConeDetector()
    clouds, transforms = [], []
    monkeypatch.setattr(sensor._cloud_pub, 'publish', clouds.append)
    monkeypatch.setattr(sensor._tf, 'sendTransform', transforms.append)
    try:
        assert sensor._sensor.seed == 9
        subscriptions = [sub.topic_name for sub in sensor.subscriptions]
        assert '/lhr/track/cones' not in subscriptions
        sensor._sensor.frame_labeled = Mock(wraps=sensor._sensor.frame_labeled)
        # The pattern has scan gaps; replay its full four-second table.
        for index in range(40):
            odom = Odometry()
            odom.header.frame_id, odom.child_frame_id = 'map', 'base_link'
            odom.pose.pose.orientation.w = 1.
            odom.header.stamp.sec, tenth = divmod(index, 10)
            odom.header.stamp.sec += 1
            odom.header.stamp.nanosec = tenth * 100_000_000
            sensor._odom_cb(odom)
            detector._odom_cb(odom)
            sensor._tick()
            before = len(clouds)
            sensor._tick()
            assert len(clouds) == before, 'Paused truth must not create another scan'
            cloud = clouds[-1]
            assert cloud.header.stamp == odom.header.stamp == transforms[-1].header.stamp
            assert cloud.header.frame_id == 'lidar'
            assert np.isfinite(point_cloud2.read_points_numpy(cloud)).all()
            detector._cloud_cb(cloud)
            detector._process()
        assert len(clouds) == 40
        assert sensor._sensor.frame_labeled.call_count == 40
        assert len(detector._cone_map) == 2
        actual = np.array([c[:2] for c in detector._cone_map])
        expected = np.array([[8., 1.75], [8., -1.75]])
        assert np.linalg.norm(actual[:, None] - expected, axis=2).min(axis=0).max() < .15
        for name, value in [('mount_pitch_rad', .25), ('motion_distortion', True),
                            ('clutter_profile', 'trackside')]:
            result = sensor.set_parameters([Parameter(name, value=value)])[0]
            assert not result.successful
        odom.header.stamp.sec = 0
        odom.header.stamp.nanosec = 0
        odom.pose.pose.position.z = .08
        odom.pose.pose.orientation.x = np.sin(.02)
        odom.pose.pose.orientation.w = np.cos(.02)
        sensor._odom_cb(odom)
        sensor._tick()
        assert len(clouds) == 41, 'A simulation reset must resume scanning'
        forwarded = sensor._sensor.frame_labeled.call_args.kwargs
        assert forwarded['vehicle_z'] == .08
        assert forwarded['vehicle_roll'] == pytest.approx(.04)
    finally:
        detector.destroy_node()
        sensor.destroy_node()
        rclpy.shutdown()


@pytest.mark.parametrize('style', ['autocross', 'oval'])
def test_bundled_worlds_are_supported_without_track_regeneration(style):
    world = Path(__file__).parents[1] / f'worlds/{style}_seed1.sdf'
    cones = load_world_cones(world)
    assert cones.shape[1] == 4
    assert len(cones) > 20
