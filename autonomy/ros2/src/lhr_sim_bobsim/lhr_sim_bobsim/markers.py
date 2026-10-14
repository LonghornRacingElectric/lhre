"""RViz markers of the car and its tire forces from ``FmuPlant``, in base_link."""

import math

from geometry_msgs.msg import Point
from std_msgs.msg import ColorRGBA
from visualization_msgs.msg import Marker, MarkerArray

# Arrow length per newton: 1 m per kN.
M_PER_N = 0.001
GREY = ColorRGBA(r=0.6, g=0.6, b=0.6, a=1.0)
WHITE = ColorRGBA(r=1.0, g=1.0, b=1.0, a=1.0)
RED = ColorRGBA(r=0.95, g=0.25, b=0.2, a=1.0)
GREEN = ColorRGBA(r=0.3, g=0.85, b=0.35, a=0.9)


def _outline(corners):
    """LINE_LIST points for a closed polygon."""
    points = []
    for (x0, y0), (x1, y1) in zip(corners, corners[1:] + corners[:1]):
        points += [Point(x=x0, y=y0), Point(x=x1, y=y1)]
    return points


class ForceMarkers:
    """
    Publish the car outline and each tire's forces on ``/lhr/vehicle/forces``.

    Red: the tire's force in the road plane, where it pushes the car. Green:
    its vertical load. The label gives the load and the friction it uses,
    horizontal force over load.
    """

    def __init__(self, node, frame_id: str = 'base_link'):
        self._pub = node.create_publisher(MarkerArray, '/lhr/vehicle/forces', 10)
        self._frame = frame_id

    def _marker(self, stamp, ns, marker_id, kind, color, width):
        m = Marker()
        m.header.frame_id = self._frame
        m.header.stamp = stamp
        m.ns, m.id, m.type, m.action = ns, marker_id, kind, Marker.ADD
        m.pose.orientation.w = 1.0
        m.scale.x = width
        m.color = color
        return m

    def publish(self, plant, stamp):
        if plant.tire_forces is None:
            return
        msg = MarkerArray()
        # The car as the FMU has it: wheel centres FL, FR, RL, RR and the wheel size.
        front_left, front_right, rear_left, rear_right = plant.wheels
        body = self._marker(stamp, 'body', 0, Marker.LINE_LIST, GREY, 0.03)
        body.points = _outline([rear_right, front_right, front_left, rear_left])
        msg.markers.append(body)

        steer = (*plant.steer_angles, 0.0, 0.0)
        r, w = plant.wheel_radius, plant.wheel_width / 2.0
        for k, ((wx, wy), delta, (fx, fy, fz)) in enumerate(
                zip(plant.wheels, steer, plant.tire_forces)):
            c, s = math.cos(delta), math.sin(delta)
            wheel = self._marker(stamp, 'wheels', k, Marker.LINE_LIST, WHITE, 0.03)
            wheel.points = _outline([(wx + c * px - s * py, wy + s * px + c * py)
                                     for px, py in ((-r, -w), (r, -w), (r, w), (-r, w))])
            shear = self._marker(stamp, 'tire force', k, Marker.ARROW, RED, 0.06)
            shear.scale.y = shear.scale.z = 0.14
            shear.points = [Point(x=wx, y=wy), Point(x=wx + fx * M_PER_N, y=wy + fy * M_PER_N)]
            load = self._marker(stamp, 'vertical load', k, Marker.ARROW, GREEN, 0.06)
            load.scale.y = load.scale.z = 0.14
            load.points = [Point(x=wx, y=wy), Point(x=wx, y=wy, z=fz * M_PER_N)]
            label = self._marker(stamp, 'label', k, Marker.TEXT_VIEW_FACING, WHITE, 0.0)
            label.scale.z = 0.12
            label.pose.position = Point(x=wx, y=wy, z=fz * M_PER_N + 0.2)
            label.text = (f'{fz:.0f}N\nmu={math.hypot(fx, fy) / fz:.2f}' if fz > 1.0
                          else 'lifted')
            msg.markers += [wheel, shear, load, label]
        self._pub.publish(msg)
