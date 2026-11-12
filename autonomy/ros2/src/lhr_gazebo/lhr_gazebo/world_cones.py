"""Physical imported cones for the simulated observation pipeline."""

from lhr_gazebo.world_viz import world_markers
from lhr_metrics.reference import world_geometry
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from visualization_msgs.msg import MarkerArray


def observation_cones(world_sdf):
    """Publish source cones only; inferred scoring gates are never observations."""
    markers = world_markers(world_sdf)
    _, _, cones = world_geometry(world_sdf)
    for marker, (_, _, _, _, kind) in zip(markers.markers, cones):
        marker.ns = {'blue': 'left_cones', 'yellow': 'right_cones'}.get(kind, 'start_finish')
        marker.text = kind
        marker.color.a = 1.
    return markers


class WorldCones(Node):
    """Supply the same latched cone input as trackgen, without regenerating geometry."""

    def __init__(self):
        super().__init__('world_cones')
        world = self.declare_parameter('world_sdf', '').value
        markers = observation_cones(world)
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self._pub = self.create_publisher(MarkerArray, '/lhr/track/cones', qos)
        self._pub.publish(markers)


def main():
    """Keep the static source alive for late subscribers."""
    rclpy.init()
    node = WorldCones()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
