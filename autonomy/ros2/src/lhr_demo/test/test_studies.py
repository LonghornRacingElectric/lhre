"""Check lap, halt and off-track scoring independently of the planner."""

import math

from lhr_demo.studies import StudyProbe
from nav_msgs.msg import Odometry
import pytest
import rclpy
from visualization_msgs.msg import Marker, MarkerArray


def test_probe_requires_full_forward_lap_and_counts_stops_and_offtrack():
    rclpy.init()
    probe = StudyProbe()
    try:
        cones = MarkerArray()
        for i in range(80):
            angle = 2 * math.pi * i / 80
            for side, radius in (('left_cones', 8.25), ('right_cones', 11.75)):
                marker = Marker()
                marker.ns, marker.id = side, i
                marker.pose.position.x = radius * math.cos(angle)
                marker.pose.position.y = radius * math.sin(angle)
                cones.markers.append(marker)
        probe.cones(cones)
        initial = Odometry()
        initial.pose.pose.position.x = 10.
        for i in range(6):
            initial.header.stamp.sec = i + 1
            probe.odom(initial)
        assert probe.halts == 0 and probe.halt_seconds == 0
        for i in range(101):
            angle = 2 * math.pi * i / 100
            odom = Odometry()
            odom.header.stamp.sec = i + 7
            odom.pose.pose.position.x = 10 * math.cos(angle)
            odom.pose.pose.position.y = 10 * math.sin(angle)
            odom.twist.twist.linear.x = 4.
            probe.odom(odom)
            if i < 100:
                assert not probe.lap
        assert probe.lap and probe.offtrack_distance == 0.
        assert probe.result('lap', 100.)['max_error_m'] < .02
        for i in range(4):
            odom.header.stamp.sec += 1
            odom.twist.twist.linear.x = 0.
            probe.odom(odom)
        assert probe.halts == 1 and probe.longest_halt == pytest.approx(4.)
        odom.header.stamp.sec += 1
        odom.pose.pose.position.x = 15.
        probe.odom(odom)
        assert probe.offtrack_distance > 4.
    finally:
        probe.destroy_node()
        rclpy.shutdown()
