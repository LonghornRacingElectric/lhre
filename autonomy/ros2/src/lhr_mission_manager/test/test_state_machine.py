"""State machine transitions around emergency and reset."""

from geometry_msgs.msg import PoseStamped
from lhr_mission_manager.mission_manager_node import MissionManager
from nav_msgs.msg import Path
import pytest
import rclpy
from std_msgs.msg import Bool


@pytest.fixture
def mm():
    # No hold in READY, so auto-go happens on the next tick.
    rclpy.init(args=['--ros-args', '-p', 'ready_hold_sec:=0.0'])
    node = MissionManager()
    yield node
    node.destroy_node()
    rclpy.shutdown()


def _path(node):
    path = Path()
    path.poses.append(PoseStamped())
    node._centerline_cb(path)


def _drive(node):
    """Run OFF -> READY -> DRIVING."""
    _path(node)
    node._tick()
    assert node._state == 'READY'
    node._tick()
    assert node._state == 'DRIVING'


def test_emergency_in_driving(mm):
    _drive(mm)
    mm._emergency_cb(Bool(data=True))
    mm._tick()
    assert mm._state == 'EMERGENCY'


def test_run_after_reset_is_not_stopped_by_the_old_emergency(mm):
    # safety_node repeats the emergency every tick until it is reset too,
    # so some of those land after the mission has already gone to OFF.
    _drive(mm)
    mm._emergency_cb(Bool(data=True))
    mm._tick()
    mm._emergency_cb(Bool(data=True))
    mm._reset_cb(Bool(data=True))
    mm._tick()
    assert mm._state == 'OFF'
    mm._emergency_cb(Bool(data=True))
    _drive(mm)
    mm._tick()
    assert mm._state == 'DRIVING'


def test_emergency_in_ready_skips_driving(mm):
    _path(mm)
    mm._tick()
    assert mm._state == 'READY'
    mm._emergency_cb(Bool(data=True))
    mm._tick()
    assert mm._state == 'EMERGENCY'


def test_supervisor_still_tripped_after_reset_trips_ready_again(mm):
    _drive(mm)
    mm._emergency_cb(Bool(data=True))
    mm._tick()
    mm._reset_cb(Bool(data=True))
    mm._tick()
    _path(mm)
    mm._tick()
    assert mm._state == 'READY'
    mm._emergency_cb(Bool(data=True))
    mm._tick()
    assert mm._state == 'EMERGENCY'


def test_reset_outside_emergency_is_ignored(mm):
    _drive(mm)
    mm._reset_cb(Bool(data=True))
    mm._tick()
    mm._emergency_cb(Bool(data=True))
    mm._tick()
    assert mm._state == 'EMERGENCY'
    mm._tick()
    assert mm._state == 'EMERGENCY'
