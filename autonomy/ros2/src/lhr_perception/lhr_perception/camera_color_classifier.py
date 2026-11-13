"""Publish a labeled RGB debug image from the ZED left camera."""

from lhr_perception.cone_color import (
    annotate_regions,
    classify_regions,
    decode_image,
    DEFAULT_MIN_AREA,
    DEFAULT_THRESHOLDS,
    validate_config,
)
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image


class CameraColorClassifier(Node):
    """Identify candidate color patches without modifying the LiDAR map."""

    def __init__(self):
        """Validate startup settings and connect the camera image topics."""
        super().__init__('camera_color_classifier')
        self.declare_parameter('min_region_area', DEFAULT_MIN_AREA)
        for color, bounds in DEFAULT_THRESHOLDS.items():
            self.declare_parameter(f'{color}_hsv', list(bounds))
        self._thresholds = {
            color: self.get_parameter(f'{color}_hsv').value
            for color in DEFAULT_THRESHOLDS
        }
        self._min_area = self.get_parameter('min_region_area').value
        validate_config(self._thresholds, self._min_area)
        self._debug_pub = self.create_publisher(
            Image, '/lhr/perception/cone_colors/debug_image', qos_profile_sensor_data)
        self._image_sub = self.create_subscription(
            Image, '/lhr/sensor/zed2i/left/image_rect',
            self._image_cb, qos_profile_sensor_data)

    def _image_cb(self, msg):
        try:
            rgb = decode_image(msg.data, msg.width, msg.height, msg.step, msg.encoding)
        except ValueError as error:
            self.get_logger().warning(str(error), throttle_duration_sec=5.0)
            return
        regions = classify_regions(rgb, self._thresholds, self._min_area)
        annotated = annotate_regions(rgb, regions)
        output = Image()
        output.header = msg.header
        output.height, output.width = annotated.shape[:2]
        output.encoding = 'rgb8'
        output.is_bigendian = 0
        output.step = output.width * 3
        output.data = annotated.tobytes()
        self._debug_pub.publish(output)


def main(args=None):
    """Run the camera classifier until ROS shuts down."""
    rclpy.init(args=args)
    node = None
    try:
        node = CameraColorClassifier()
        rclpy.spin(node)
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.shutdown()
