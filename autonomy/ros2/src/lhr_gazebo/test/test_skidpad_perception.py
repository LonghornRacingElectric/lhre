"""Deliver measured camera colors to skidpad over DDS without inferred-side shortcuts."""

from copy import deepcopy
import time

from geometry_msgs.msg import TransformStamped
from lhr_perception.camera_lidar_fusion import CameraLidarFusion
from lhr_track_builder.skidpad_acquisition import SkidpadAcquisition
from nav_msgs.msg import Odometry
import numpy as np
import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.time import Time
from sensor_msgs.msg import CameraInfo, Image
from std_msgs.msg import String
from visualization_msgs.msg import Marker, MarkerArray


def test_camera_image_to_skidpad_creep_and_camera_loss_stop():
    """Fresh images authorize orange-lane creep; repeated maps cannot replace the camera."""
    rclpy.init(args=['--ros-args', '-p', 'use_sim_time:=true',
                     '-p', 'require_camera_colors:=true',
                     '-p', 'cone_topic:=/lhr/perception/cones_verified',
                     '-p', 'pixel_margin:=1.0'], domain_id=97)
    fusion = planner = executor = None
    try:
        fusion, planner = CameraLidarFusion(), SkidpadAcquisition()
        executor = SingleThreadedExecutor()
        executor.add_node(planner)
        # Source LiDAR sides deliberately disagree with the physical boundary colors.
        cones = [(0, -4., -6., 'right_cones', (0, 0, 255)),
                 (1, 4., -6., 'left_cones', (255, 255, 0))]
        cones += [(i + 2, x, y, 'cones', (255, 100, 0))
                  for i, (x, y) in enumerate((x, y) for x in (-1.5, 1.5)
                                             for y in (-14., -11., -8.))]
        cmd = []
        planner._limit_pub.publish = cmd.append
        planner._status_cb(String(data='DRIVING'))

        def feed(seconds):
            stamp = Time(seconds=seconds).to_msg()
            fusion.get_clock().set_ros_time_override(Time(seconds=seconds))
            planner.get_clock().set_ros_time_override(Time(seconds=seconds))
            odom = Odometry()
            odom.header.frame_id, odom.header.stamp = 'map', stamp
            odom.pose.pose.position.y = -16.8747
            odom.pose.pose.orientation.z = odom.pose.pose.orientation.w = 2 ** -.5
            planner._odom_cb(odom)
            source = MarkerArray()
            rgb = np.zeros((800, 1200, 3), dtype=np.uint8)
            for cone_id, x, y, namespace, color in cones:
                marker = Marker(id=cone_id, ns=namespace)
                marker.header.frame_id, marker.header.stamp = 'map', stamp
                marker.pose.position.x, marker.pose.position.y = x, y
                source.markers.append(marker)
                depth = y + 16.8747
                u, v = round(600. + 600. * x / depth), round(400. + 600. * .3875 / depth)
                rgb[v-3:v+4, u-3:u+4] = color
            fusion._map_cb(source)
            info = CameraInfo(width=1200, height=800)
            info.header.frame_id, info.header.stamp = 'camera_optical', stamp
            info.p = [600., 0., 600., 0., 0., 600., 400., 0., 0., 0., 1., 0.]
            info.r = [1., 0., 0., 0., 1., 0., 0., 0., 1.]
            info.d = [0.] * 5
            image = Image(width=1200, height=800, step=3600, encoding='rgb8', data=rgb.tobytes())
            image.header = deepcopy(info.header)
            transform = TransformStamped()
            transform.header = deepcopy(info.header)
            transform.child_frame_id = 'map'
            transform.transform.rotation.x = transform.transform.rotation.w = 2 ** -.5
            transform.transform.translation.y = .55
            transform.transform.translation.z = 16.8747
            fusion._tf_buffer.set_transform(transform, 'test')
            fusion._info_cb(info)
            fusion._image_cb(image)
            fusion._process()

        for seconds in (10., 10.1, 10.2):
            feed(seconds)
        deadline = time.monotonic() + 3.
        while len(planner._orange) != 6 and time.monotonic() < deadline:
            executor.spin_once(timeout_sec=.02)
        assert len(planner._orange) == 6
        assert planner._circular_colors == [0, 1]
        planner._tick()
        assert planner._phase == 'APPROACH'
        assert 0 < cmd[-1].data <= .5
        frame = planner._frame
        for _ in range(4):
            fusion._publish_map()
            executor.spin_once(timeout_sec=.02)
        assert planner._frame == frame
        fusion.get_clock().set_ros_time_override(Time(seconds=10.8))
        planner.get_clock().set_ros_time_override(Time(seconds=10.8))
        planner._odom_time = 10.8
        fusion._process()
        deadline = time.monotonic() + 3.
        while planner._orange and time.monotonic() < deadline:
            executor.spin_once(timeout_sec=.02)
        planner._tick()
        assert not planner._orange
        assert planner._phase == 'HOLD'
        assert cmd[-1].data == 0
    finally:
        if executor is not None:
            executor.shutdown()
        if planner is not None:
            planner.destroy_node()
        if fusion is not None:
            fusion.destroy_node()
        rclpy.shutdown()
