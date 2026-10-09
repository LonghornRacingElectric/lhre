"""Keep mesh geometry intact without applying detection noise to the scene."""

from geometry_msgs.msg import Point
from lhr_sensor_sim.sensor_sim_node import SensorSim
import rclpy
from std_msgs.msg import ColorRGBA
from visualization_msgs.msg import Marker


class Capture:
    """Capture published markers."""

    def publish(self, message):
        """Remember the most recent message."""
        self.message = message


def test_detection_preserves_triangles_and_does_not_move_ground_truth():
    rclpy.init()
    node = SensorSim()
    try:
        marker = Marker()
        marker.ns, marker.id = 'left_cones', 7
        marker.type = Marker.TRIANGLE_LIST
        marker.pose.position.x = 5.
        marker.points = [Point(x=0., y=0., z=.325)] * 3
        marker.colors = [ColorRGBA(r=0., g=.2, b=1., a=1.)] * 3
        node._all_cones, node._have_odom = [marker], True
        node._noise_std = .1
        node._detect()
        detected = node._accumulated[('left_cones', 7)]
        assert detected.points == marker.points and detected.colors == marker.colors
        assert marker.pose.position.x == 5.
        assert detected.pose.position.x != marker.pose.position.x
        node._viz_pub = Capture()
        node._publish_cones_viz()
        assert node._viz_pub.message.markers[0].points == marker.points
    finally:
        node.destroy_node()
        rclpy.shutdown()
