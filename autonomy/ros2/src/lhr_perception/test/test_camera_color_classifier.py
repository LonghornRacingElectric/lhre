"""Check the ROS image boundary of the camera classifier."""

import importlib
import importlib.util
from types import SimpleNamespace

import pytest


def test_camera_node_exists():
    """The installed entry point needs a real node module."""
    assert importlib.util.find_spec('lhr_perception.camera_color_classifier') is not None


def test_image_callback_preserves_header_and_drops_bad_frames():
    """Output must retain source timing and malformed input must not publish."""
    rclpy = pytest.importorskip('rclpy')
    from sensor_msgs.msg import Image
    module = importlib.import_module('lhr_perception.camera_color_classifier')
    rclpy.init(args=[])
    node = module.CameraColorClassifier()
    published = []
    node._debug_pub = SimpleNamespace(publish=published.append)
    try:
        msg = Image()
        msg.header.frame_id = 'zed2i_left_camera_optical_frame'
        msg.header.stamp.sec = 123
        msg.header.stamp.nanosec = 456
        msg.width, msg.height, msg.step = 20, 20, 60
        msg.encoding = 'bgr8'
        msg.data = bytes([255, 0, 0] * 400)
        node._image_cb(msg)
        output = published[0]
        assert output.header == msg.header
        assert (output.width, output.height, output.step) == (20, 20, 60)
        assert output.encoding == 'rgb8'
        assert bytes(output.data[990:993]) == bytes([0, 0, 255])
        msg.encoding = 'mono8'
        node._image_cb(msg)
        assert len(published) == 1
    finally:
        node.destroy_node()
        rclpy.shutdown()
