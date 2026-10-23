"""Mission completion: brake in DRIVING, FINISHED only once stopped."""

import math

from geometry_msgs.msg import PoseStamped
from lhr_mission_manager.mission_manager_node import (
    ACCEL_FINISH_M, MissionManager)
from nav_msgs.msg import Odometry, Path
import pytest
import rclpy
from std_msgs.msg import Bool


@pytest.fixture
def make():
    nodes = []

    def build(mission):
        rclpy.init(args=['--ros-args', '-p', f'mission:={mission}',
                         '-p', 'ready_hold_sec:=0.0'])
        nodes.append(MissionManager())
        return nodes[-1]
    try:
        yield build
    finally:
        # Always, so one failure cannot poison every later rclpy test.
        for node in nodes:
            node.destroy_node()
        if nodes:
            rclpy.shutdown()


def _odom(node, x, y, yaw=0.0, speed=0.0):
    msg = Odometry()
    msg.pose.pose.position.x = x
    msg.pose.pose.position.y = y
    msg.pose.pose.orientation.z = math.sin(yaw / 2.0)
    msg.pose.pose.orientation.w = math.cos(yaw / 2.0)
    msg.twist.twist.linear.x = speed
    node._odom_cb(msg)


def _drive(node):
    path = Path()
    path.poses.append(PoseStamped())
    node._centerline_cb(path)
    node._tick()
    node._tick()
    assert node._state == 'DRIVING'


def test_acceleration_brakes_at_the_finish_then_finishes_stopped(make):
    mm = make('acceleration')
    _odom(mm, -2.0, 0.0)
    _drive(mm)
    _odom(mm, -2.0 + ACCEL_FINISH_M - 0.1, 0.3, speed=20.0)
    mm._tick()
    assert not mm._complete
    _odom(mm, -2.0 + ACCEL_FINISH_M + 0.1, -0.3, speed=20.0)
    mm._tick()
    assert mm._complete
    assert mm._state == 'DRIVING'  # still moving, so not FINISHED yet
    _odom(mm, -2.0 + ACCEL_FINISH_M + 30.0, 0.0, speed=0.2)
    mm._tick()
    assert mm._state == 'FINISHED'


def test_acceleration_distance_is_along_the_start_heading(make):
    mm = make('acceleration')
    _odom(mm, 10.0, 5.0, yaw=math.pi / 2)
    _drive(mm)
    _odom(mm, 10.0 + ACCEL_FINISH_M, 5.0, yaw=math.pi / 2, speed=20.0)
    mm._tick()
    assert not mm._complete  # sideways is not down the course
    _odom(mm, 10.0, 5.0 + ACCEL_FINISH_M, yaw=math.pi / 2, speed=20.0)
    mm._tick()
    assert mm._complete


def test_acceleration_with_odometry_after_the_go(make):
    mm = make('acceleration')
    _drive(mm)
    _odom(mm, 3.0, 0.0)
    mm._tick()
    _odom(mm, 3.0 + ACCEL_FINISH_M, 0.0, speed=20.0)
    mm._tick()
    assert mm._complete


def test_autocross_lap_brakes_before_finished(make):
    mm = make('autocross')
    _drive(mm)
    _odom(mm, 0.0, 0.0, speed=5.0)
    mm._lap_complete_cb(Bool(data=True))
    mm._tick()
    assert mm._complete and mm._state == 'DRIVING'
    _odom(mm, 1.0, 0.0, speed=0.0)
    mm._tick()
    assert mm._state == 'FINISHED'


def test_reset_clears_completion(make):
    mm = make('autocross')
    _drive(mm)
    _odom(mm, 0.0, 0.0, speed=5.0)
    mm._lap_complete_cb(Bool(data=True))
    mm._tick()
    mm._emergency_cb(Bool(data=True))
    mm._tick()
    mm._reset_cb(Bool(data=True))
    mm._tick()
    assert mm._state == 'OFF' and not mm._complete
