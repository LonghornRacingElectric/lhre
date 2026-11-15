"""safety_node's arm / latch / heartbeat / reset decisions."""

from lhr_safety.latch import ARMED_STATES, SafetyLatch
from lhr_safety.monitors import Fault, SafetyConfig, SafetyMonitor


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


# --- A dead mission_manager through the monitor and the latch together (#127) ---

CFG = SafetyConfig(wheelbase_m=1.5494, max_speed_mps=15.0)


def _tick(monitor, t, state):
    """Every stream fresh; state is what the manager last said (None: it said nothing)."""
    monitor.on_lidar(t)
    monitor.on_command(t, 0.0)
    monitor.on_steering(t, 0.0)
    monitor.on_odom(t, 5.0 * t, 0.0, 5.0, 0.0, 0.0, 0.01, 0.01)
    monitor.on_imu(t, 0.0)
    monitor.on_wheels(t)
    if state is not None:
        monitor.on_mission(t, state in ARMED_STATES)
    return monitor.check(t)


def _dies_driving():
    """Drive to 1 s, then the manager stops publishing; the safety node keeps its last state."""
    monitor, latch = SafetyMonitor(CFG, now=0.0), SafetyLatch()
    t = 0.0
    while t < 1.0:
        latch.step(_tick(monitor, t, 'DRIVING'), 'DRIVING')
        t += 0.05
    tripped = None
    while t < 2.0:
        decision = latch.step(_tick(monitor, t, None), 'DRIVING')
        if decision.newly_tripped:
            tripped = t
        t += 0.05
    return monitor, latch, t, tripped


def test_dead_manager_latches_and_a_restart_does_not_clear_it():
    monitor, latch, t, tripped = _dies_driving()
    assert tripped is not None and latch.tripped == Fault.MISSION_STALE
    # The manager comes back in OFF: the live fault clears, the latch holds.
    fault = _tick(monitor, t, 'OFF')
    decision = latch.step(fault, 'OFF')
    assert fault == Fault.NONE
    assert decision.emergency and decision.heartbeat is None
    assert decision.shown == Fault.MISSION_STALE
    latch.reset()
    assert latch.step(_tick(monitor, t + 0.05, 'OFF'), 'OFF').heartbeat is not None


def test_reset_while_the_manager_is_still_dead_trips_again():
    monitor, latch, t, _ = _dies_driving()
    latch.reset()
    # Still DRIVING as far as the safety node knows, and still silent.
    decision = latch.step(_tick(monitor, t, None), 'DRIVING')
    assert decision.newly_tripped and decision.shown == Fault.MISSION_STALE
