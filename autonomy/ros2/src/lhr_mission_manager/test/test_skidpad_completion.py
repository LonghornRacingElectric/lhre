"""Skidpad completion comes from the ordered planner and requires standstill."""

from lhr_mission_manager.mission_manager_node import (
    MissionManager, STATE_DRIVING, STATE_EMERGENCY, STATE_FINISHED, STATE_READY)
import pytest
import rclpy
from std_msgs.msg import Bool


@pytest.fixture
def mission():
    rclpy.init(args=['--ros-args', '-p', 'mission:=skidpad'])
    node = MissionManager()
    yield node
    node.destroy_node()
    rclpy.shutdown()


def test_only_driving_skidpad_completion_can_finish(mission):
    mission._state = STATE_READY
    mission._skidpad_complete_cb(Bool(data=True))
    assert not mission._check_mission_complete()
    mission._state = STATE_DRIVING
    mission._lap_complete_cb(Bool(data=True))
    assert not mission._check_mission_complete()
    mission._skidpad_complete_cb(Bool(data=True))
    mission._current_speed = 1.0
    mission._tick_driving()
    assert mission._state == STATE_DRIVING
    mission._current_speed = 0.0
    mission._tick_driving()
    assert mission._state == STATE_FINISHED


def test_emergency_reset_clears_skidpad_completion(mission):
    mission._state = STATE_DRIVING
    mission._skidpad_complete_cb(Bool(data=True))
    mission._state = STATE_EMERGENCY
    mission._reset_received = True
    mission._tick_emergency()
    assert not mission._check_mission_complete()
