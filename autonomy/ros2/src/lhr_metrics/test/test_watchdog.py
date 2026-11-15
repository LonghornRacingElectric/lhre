"""The run watchdog counts sim time and only uses wall time to catch a stopped clock."""

from lhr_metrics.watchdog import RunWatchdog


def run(watchdog, samples):
    """Feed (clock, wall) samples; return the first ending and the wall time it fired."""
    for clock, wall in samples:
        ending = watchdog.check(clock, wall)
        if ending:
            return ending, wall
    return None, None


def test_disabled_never_fires():
    assert run(RunWatchdog(0.0, 0.0), [(0.0, w) for w in range(1000)]) == (None, None)


def test_real_time_sim_times_out_on_schedule():
    samples = [(100.0 + t, t) for t in range(200)]
    assert run(RunWatchdog(120.0, 0.0), samples) == ('timeout', 120)


def test_slow_sim_gets_the_same_sim_seconds():
    # One sixth of real time, the FMU plant on red-mesa: 120 sim seconds
    # take 720 wall seconds, and that is a timeout, not a hang.
    samples = [(10.0 + t / 6.0, float(t)) for t in range(1000)]
    assert run(RunWatchdog(120.0, 0.0), samples) == ('timeout', 720.0)


def test_stopped_clock_is_hung():
    samples = [(min(t, 30.0), float(t)) for t in range(1, 400)]
    assert run(RunWatchdog(120.0, 0.0), samples) == ('hung', 150.0)


def test_clock_that_never_starts_is_hung():
    assert run(RunWatchdog(60.0, 0.0), [(0.0, float(w)) for w in range(100)]) == ('hung', 60.0)


def test_deadline_counts_from_the_first_clock_not_from_zero():
    # Sim time starts late (the plant loading); the deadline starts with it.
    samples = [(0.0, float(w)) for w in range(20)] + [(t, 20.0 + t) for t in range(1, 100)]
    assert run(RunWatchdog(30.0, 0.0), samples) == ('timeout', 51.0)
