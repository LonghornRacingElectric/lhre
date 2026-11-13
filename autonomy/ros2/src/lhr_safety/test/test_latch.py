"""safety_node's arm / latch / heartbeat / reset decisions."""

from lhr_safety.latch import SafetyLatch
from lhr_safety.monitors import Fault


def test_healthy_ticks_beat_and_never_trip():
    latch = SafetyLatch()
    for i in range(1, 4):
        d = latch.step(Fault.NONE, 'DRIVING')
        assert (d.emergency, d.heartbeat, d.shown) == (False, i, Fault.NONE)


def test_fault_before_arming_stops_the_heartbeat_but_does_not_trip():
    latch = SafetyLatch()
    d = latch.step(Fault.LIDAR_STALE, 'OFF')
    assert not d.emergency and not d.newly_tripped
    assert d.heartbeat is None
    assert d.shown == Fault.LIDAR_STALE
    d = latch.step(Fault.NONE, 'OFF')
    assert d.heartbeat == 1 and not d.emergency


def test_trip_in_ready_latches():
    latch = SafetyLatch()
    d = latch.step(Fault.ODOM_STALE, 'READY')
    assert d.newly_tripped and d.emergency and d.heartbeat is None


def test_latch_holds_after_the_fault_clears_and_repeats_the_emergency():
    latch = SafetyLatch()
    latch.step(Fault.CMD_STALE, 'DRIVING')
    for state in ('DRIVING', 'EMERGENCY', 'EMERGENCY'):
        d = latch.step(Fault.NONE, state)
        assert d.emergency and not d.newly_tripped
        assert d.heartbeat is None
        assert d.shown == Fault.CMD_STALE


def test_first_fault_stays_latched():
    latch = SafetyLatch()
    latch.step(Fault.EKF_DIVERGED, 'DRIVING')
    d = latch.step(Fault.GEOFENCE, 'DRIVING')
    assert d.shown == Fault.EKF_DIVERGED and not d.newly_tripped


def test_reset_clears_and_heartbeat_resumes():
    latch = SafetyLatch()
    latch.step(Fault.CMD_STALE, 'DRIVING')
    assert latch.reset() == Fault.CMD_STALE
    d = latch.step(Fault.NONE, 'OFF')
    assert not d.emergency and d.heartbeat is not None


def test_fault_still_present_after_reset_retrips_once_armed():
    latch = SafetyLatch()
    latch.step(Fault.LIDAR_STALE, 'DRIVING')
    latch.reset()
    d = latch.step(Fault.LIDAR_STALE, 'OFF')
    assert not d.emergency
    d = latch.step(Fault.LIDAR_STALE, 'READY')
    assert d.newly_tripped and d.emergency


def test_reset_with_nothing_latched_is_a_no_op():
    latch = SafetyLatch()
    assert latch.reset() == Fault.NONE
    assert latch.step(Fault.NONE, 'DRIVING').heartbeat == 1


def test_arming_edge_fires_once_per_arming():
    latch = SafetyLatch()
    edges = [latch.step(Fault.NONE, s).newly_armed
             for s in ('OFF', 'READY', 'DRIVING', 'EMERGENCY', 'OFF', 'READY')]
    assert edges == [False, True, False, False, False, True]
