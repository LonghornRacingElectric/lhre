"""Timeline: steps, ramps and holds, exactly as the README's Scenarios
section defines them."""

import itertools

import pytest

from vcusil.scenario import Driver, RestOfCar, Segment, Timeline


def timeline(**tracks):
    return Timeline('t', 10_000, tuple((name, tuple(segments)) for name, segments in
                                       tracks.items()))


def test_steps_hold_until_changed():
    tl = timeline(brake_psi=[Segment(0, 0.0, 350.0), Segment(1500, 350.0, 0.0)],
                  rtd_pressed=[Segment(1000, False, True)],
                  hvc_state=[Segment(200, 0, 1), Segment(600, 1, 2)])
    assert tl.value('brake_psi', 1499) == 350.0 and tl.value('brake_psi', 1500) == 0.0
    assert tl.value('rtd_pressed', 999) is False and tl.value('rtd_pressed', 1000) is True
    assert [tl.value('hvc_state', t) for t in (199, 200, 599, 600, 9999)] == [0, 1, 1, 2, 2]
    # A signal no segment sets keeps its default; before the first segment
    # every signal is its default.
    driver, car = tl.at(5000)
    assert driver.steering_deg == 0.0 and car.shutdown_loop_v == 24.0
    assert tl.at(0)[1] == RestOfCar()
    assert timeline(accelerator=[Segment(50, 0.0, 1.0)]).at(49) == (Driver(), RestOfCar())


def test_ramp_values():
    up = timeline(accelerator=[Segment(2000, 0.0, 0.4, 300)])
    assert up.value('accelerator', 1999) == 0.0
    assert up.value('accelerator', 2000) == 0.0  # exactly the held value
    assert up.value('accelerator', 2001) == pytest.approx(0.4 / 300)
    assert up.value('accelerator', 2150) == 0.2
    assert up.value('accelerator', 2299) == pytest.approx(0.4 * 299 / 300)
    assert up.value('accelerator', 2299) < 0.4
    assert up.value('accelerator', 2300) == 0.4 and up.value('accelerator', 9000) == 0.4
    down = timeline(brake_psi=[Segment(0, 0.0, 350.0), Segment(1500, 350.0, 0.0, 150)])
    assert down.value('brake_psi', 1500) == 350.0
    # Exactly a + (b - a) * ((t - T) / R), so runs repeat bit for bit: the
    # same line as a * (1 - f) + b * f gives 244.99999999999997 here.
    assert down.value('brake_psi', 1545) == 245.0
    assert down.value('brake_psi', 1575) == 175.0
    assert down.value('brake_psi', 1650) == 0.0
    # From a held non-zero value: 0.4 -> 0.6 is 0.5 halfway.
    held = timeline(accelerator=[Segment(2000, 0.0, 0.4, 300), Segment(4000, 0.4, 0.6, 200)])
    assert held.value('accelerator', 3999) == 0.4
    assert held.value('accelerator', 4000) == 0.4
    assert held.value('accelerator', 4100) == 0.5
    assert held.value('accelerator', 4200) == 0.6
    # Back to back, the second ramp starts where the first one ends.
    chained = timeline(accelerator=[Segment(0, 0.0, 0.5, 10), Segment(10, 0.5, 0.2, 10)])
    values = [chained.value('accelerator', t) for t in range(25)]
    assert values[10] == 0.5 and values[20] == 0.2
    assert all(abs(b - a) <= 0.05 + 1e-12 for a, b in itertools.pairwise(values))


def test_ramp_stays_in_range():
    ends = (0.0, 0.1, 0.3, 1 / 3, 0.7, 1.0, 24.0, 27.0, 3000.0, -180.0, 180.0)
    for a, b in itertools.product(ends, repeat=2):
        for ramp_ms in (1, 2, 3, 7, 100, 150, 300, 997):
            tl = timeline(accelerator=[Segment(0, a, b, ramp_ms)])
            for t in range(ramp_ms + 2):
                v = tl.value('accelerator', t)
                assert min(a, b) <= v <= max(a, b), (a, b, ramp_ms, t, v)
            assert tl.value('accelerator', ramp_ms) == b


def test_at_rejects_negative_time():
    with pytest.raises(ValueError, match='time starts at 0'):
        timeline().at(-1)
    with pytest.raises(ValueError):
        timeline().value('accelerator', -3)
