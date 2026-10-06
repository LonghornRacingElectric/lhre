#!/usr/bin/env python3
"""Draw Orion from vehicle.yaml, so a 3D viewer shows a car and not a grid."""

import math

from lhr_vehicle import load_vehicle
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from visualization_msgs.msg import Marker, MarkerArray

BASE_FRAME = 'base_link'
TOPIC = '/lhr/vehicle/body'

# Burnt orange, and a dark tyre. Picked so the car reads as a car at a
# glance against the dark viewer background and the turbo point cloud,
# which already owns the blue-to-red end of the range.
BODY_RGBA = (0.75, 0.34, 0.0, 0.9)
WHEEL_RGBA = (0.12, 0.12, 0.14, 1.0)


def _wheel_orientation() -> tuple:
    """Return the quaternion that lays a cylinder marker on its side."""
    # A CYLINDER marker's axis is z and a wheel turns about y, so the
    # whole difference is a quarter turn about x.
    half = math.pi / 4.0
    return (math.sin(half), 0.0, 0.0, math.cos(half))


class VehicleVizNode(Node):
    """Publishes the car's shape as markers in base_link, once."""

    def __init__(self):
        super().__init__('vehicle_viz')

        vehicle = load_vehicle()

        # Latched. The car's shape never changes, so publishing it on a
        # timer would be a marker array per tick in every bag for no
        # information. Transient-local instead means a viewer that
        # connects late still gets it.
        qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._pub = self.create_publisher(MarkerArray, TOPIC, qos)
        self._pub.publish(self._markers(vehicle))

        self.get_logger().info(
            f'{vehicle.name}: {vehicle.wheelbase_m:.3f} m wheelbase, '
            f'{vehicle.track_m:.3f} m track, '
            f'{vehicle.wheel_radius_m:.3f} m wheel radius')

    def _marker(self, marker_id: int, kind: int, rgba: tuple) -> Marker:
        """Start a marker with the fields every one of ours shares."""
        m = Marker()
        m.header.frame_id = BASE_FRAME
        # Deliberately left at time zero, which tf2 reads as 'the latest
        # transform'. A part of the car wants exactly that, and stamping
        # it with the clock would date it before the first /clock tick
        # on a sim-time run.
        m.ns = 'vehicle'
        m.id = marker_id
        m.type = kind
        m.action = Marker.ADD
        m.pose.orientation.w = 1.0
        # The car is defined in base_link, so it has to move with it
        # rather than being baked into the world at publish time.
        m.frame_locked = True
        m.color.r, m.color.g, m.color.b, m.color.a = rgba
        return m

    def _markers(self, vehicle) -> MarkerArray:
        """Build the chassis box and the four wheels."""
        out = MarkerArray()

        body = self._marker(0, Marker.CUBE, BODY_RGBA)
        body.pose.position.x = vehicle.chassis_center_x_m
        body.pose.position.z = vehicle.chassis_z_m
        body.scale.x = vehicle.body_length_m
        body.scale.y = vehicle.body_width_m
        body.scale.z = vehicle.body_height_m
        out.markers.append(body)

        qx, qy, qz, qw = _wheel_orientation()
        # base_link is the rear axle at ground level, so the rear wheels
        # sit at x=0 and the fronts a wheelbase ahead.
        corners = (
            (0.0, vehicle.half_track_m),
            (0.0, -vehicle.half_track_m),
            (vehicle.wheelbase_m, vehicle.half_track_m),
            (vehicle.wheelbase_m, -vehicle.half_track_m),
        )
        for i, (x, y) in enumerate(corners, start=1):
            wheel = self._marker(i, Marker.CYLINDER, WHEEL_RGBA)
            wheel.pose.position.x = x
            wheel.pose.position.y = y
            wheel.pose.position.z = vehicle.wheel_radius_m
            wheel.pose.orientation.x = qx
            wheel.pose.orientation.y = qy
            wheel.pose.orientation.z = qz
            wheel.pose.orientation.w = qw
            # A cylinder's x and y scales are diameters, not radii.
            wheel.scale.x = 2.0 * vehicle.wheel_radius_m
            wheel.scale.y = 2.0 * vehicle.wheel_radius_m
            wheel.scale.z = vehicle.wheel_width_m
            out.markers.append(wheel)

        return out


def main():
    """Entry point."""
    rclpy.init()
    node = VehicleVizNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.try_shutdown()


if __name__ == '__main__':
    main()
