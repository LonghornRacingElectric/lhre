"""Acquisition detections contain only the current bounded view."""

from unittest.mock import Mock

from lhr_sensor_sim.sensor_sim_node import SensorSim
import rclpy
from visualization_msgs.msg import Marker


def test_nonaccumulating_sensor_discards_cones_after_motion():
    rclpy.init(args=['--ros-args', '-p', 'accumulate_detections:=false',
                     '-p', 'fov_deg:=240.0', '-p', 'max_range_m:=15.0'])
    node = SensorSim()
    try:
        node._have_odom = True
        for name in ('_det_pub', '_fov_pub', '_viz_pub'):
            setattr(node, name, Mock())
        for index, (x, y) in enumerate([(14.0, 0.0), (16.0, 0.0), (-5.0, 0.0)]):
            marker = Marker(id=index, ns='left_cones')
            marker.pose.position.x, marker.pose.position.y = x, y
            node._all_cones.append(marker)
        node._detect()
        assert [m.id for m in node._det_pub.publish.call_args.args[0].markers] == [0]
        node._veh_x = 2.0
        node._detect()
        assert [m.id for m in node._det_pub.publish.call_args.args[0].markers] == [0, 1]
        node._veh_x = 20.0
        node._detect()
        assert node._det_pub.publish.call_args.args[0].markers == []
    finally:
        node.destroy_node()
        rclpy.shutdown()
