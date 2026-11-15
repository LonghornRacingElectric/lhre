"""READY waits for a steady safety heartbeat, so safety never arms on a late sensor."""

from geometry_msgs.msg import PoseStamped
from lhr_mission_manager.mission_manager_node import HEARTBEAT_GAP_S, MissionManager
from nav_msgs.msg import Path
import pytest
import rclpy
from std_msgs.msg import UInt32


@pytest.fixture
def make():
    nodes = []

    def build(require):
        rclpy.init(args=['--ros-args',
                         '-p', f'require_safety_heartbeat:={str(require).lower()}',
                         '-p', 'safety_settle_s:=2.0'])
        node = MissionManager()
        clock = [100.0]
        node._now = lambda: clock[0]  # drive time by hand
        nodes.append(node)
        return node, clock
    try:
        yield build
    finally:
        # Always, so one failure cannot poison every later rclpy test.
        for node in nodes:
            node.destroy_node()
        if nodes:
            rclpy.shutdown()


def _path(node):
    path = Path()
    path.poses.append(PoseStamped())
    node._centerline_cb(path)


def _beat(node, clock, until, step=0.05):
    while clock[0] < until:
        clock[0] += step
        node._heartbeat_cb(UInt32())


def test_without_safety_ready_follows_the_path(make):
    mm, _ = make(False)
    _path(mm)
    mm._tick()
    assert mm._state == 'READY'


def test_no_heartbeat_keeps_the_car_off(make):
    # A safety check that never passes must hold OFF, never arm and trip.
    mm, clock = make(True)
    _path(mm)
    for _ in range(50):
        clock[0] += 0.1
        mm._tick()
    assert mm._state == 'OFF'


def test_ready_only_after_the_settle_time(make):
    mm, clock = make(True)
    _path(mm)
    _beat(mm, clock, 101.5)
    mm._tick()
    assert mm._state == 'OFF'
    _beat(mm, clock, 102.1)
    mm._tick()
    assert mm._state == 'READY'


def test_a_gap_restarts_the_settle_time(make):
    # A sensor that drops out during startup stops the heartbeat; the wait
    # starts over rather than arming into the next dropout.
    mm, clock = make(True)
    _path(mm)
    _beat(mm, clock, 101.9)
    clock[0] += HEARTBEAT_GAP_S + 0.1
    mm._heartbeat_cb(UInt32())
    _beat(mm, clock, clock[0] + 1.5)
    mm._tick()
    assert mm._state == 'OFF'
    _beat(mm, clock, clock[0] + 0.6)
    mm._tick()
    assert mm._state == 'READY'


def test_a_stale_heartbeat_does_not_count(make):
    # Settled once, then silent: the check is live, not a memory.
    mm, clock = make(True)
    _path(mm)
    _beat(mm, clock, 102.5)
    clock[0] += HEARTBEAT_GAP_S + 0.1
    mm._tick()
    assert mm._state == 'OFF'
