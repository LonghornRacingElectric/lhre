"""Viewer-only truth cones from the same SDF that Gazebo loads."""

import math

from geometry_msgs.msg import PoseStamped
from lhr_metrics.reference import world_centerline, world_geometry
from lhr_trackgen.cone_geometry import CONE_SPECS
from nav_msgs.msg import Path
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from visualization_msgs.msg import Marker, MarkerArray


def world_markers(world_sdf, acceleration=False):
    """Build nominal cone envelopes without feeding the perception graph."""
    _, _, cones = world_geometry(world_sdf, acceleration=acceleration)
    markers = []
    for index, (name, x, y, yaw, kind) in enumerate(cones):
        spec = CONE_SPECS[kind]
        marker = Marker()
        marker.header.frame_id = 'map'
        marker.ns, marker.id = 'truth', index
        marker.type = Marker.CYLINDER
        marker.pose.position.x, marker.pose.position.y = x, y
        marker.pose.position.z = spec.height_m / 2
        marker.pose.orientation.z = math.sin(yaw / 2)
        marker.pose.orientation.w = math.cos(yaw / 2)
        marker.scale.x = marker.scale.y = spec.base_width_m
        marker.scale.z = spec.height_m
        marker.color.r, marker.color.g, marker.color.b = spec.color
        marker.color.a = 0.4
        marker.text = name
        markers.append(marker)
    return MarkerArray(markers=markers)


def reference_path(world_sdf, acceleration=False):
    """Show the fixed truth route separately from the planner's forward path."""
    points = world_centerline(world_sdf, acceleration=acceleration)
    if not acceleration:
        points = [*points, points[0]]
    path = Path()
    path.header.frame_id = 'map'
    for x, y in points:
        pose = PoseStamped()
        pose.header.frame_id = 'map'
        pose.pose.position.x, pose.pose.position.y = x, y
        pose.pose.orientation.w = 1.
        path.poses.append(pose)
    return path


class WorldViz(Node):
    """Publish static truth on a topic used only by the viewer."""

    def __init__(self):
        super().__init__('world_viz')
        world = self.declare_parameter('world_sdf', '').value
        acceleration = self.declare_parameter('acceleration', False).value
        markers = world_markers(world, acceleration)
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self._pub = self.create_publisher(MarkerArray, '/lhr/viewer/cones_truth', qos)
        self._pub.publish(markers)
        self._path_pub = self.create_publisher(Path, '/lhr/viewer/centerline_truth', qos)
        self._path_pub.publish(reference_path(world, acceleration))


def main():
    """Keep the static publisher alive for late subscribers."""
    rclpy.init()
    node = WorldViz()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
