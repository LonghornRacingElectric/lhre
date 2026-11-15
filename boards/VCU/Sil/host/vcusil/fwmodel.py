"""Models of how the VCU firmware reads its inputs, one control tick per 3 ms
sample: the RTD switch's debounce, the brake and pedal filters, the
brake-pressed hysteresis and the shutdown loop's taps.

check.py runs them on a scenario file before a run, to warn about traps;
invariants.py runs them on a run's inputs afterwards, as an oracle the
firmware's outputs are judged against. test_check_firmware.py holds each
model to the library, tick by tick. They need only the standard library.
"""

from __future__ import annotations

from dataclasses import dataclass

from .cosim import STEP_MS

# Firmware constants the warnings and invariants stand on, as
# boards/VCU/App/config/params.hpp sets them. Every event mode shares them:
# params.cpp changes only the pedal map and the torque table. test_check.py
# reads each back from the header, fails if params.cpp overrides one, and
# steps the library across the bands built from them.
THRESHOLDS = {
    'AnalogParams::sensor_divider_ratio': 10.0 / (5.1 + 10.0),
    'AnalogParams::shutdown_divider_ratio': 13.7 / (100.0 + 13.7),
    'AppsParams::ema_alpha': 0.35,
    'AppsParams::min_travel_deadzone': 0.09,
    'AppsParams::max_travel_deadzone': 0.74,
    'BseParams::v_at_0_psi': 0.5,
    'BseParams::v_at_full': 4.5,
    'BseParams::full_scale_psi': 3000.0,
    'BseParams::ema_alpha': 0.10,
    'BseParams::pressed_on_psi': 300.0,
    'BseParams::pressed_off_psi': 250.0,
    'BseParams::brake_light_psi': 30.0,
    'BseParams::bot_pedal_on': 0.25,
    'BseParams::bot_pedal_off': 0.05,
    'BseParams::hard_brake_pin_v': 0.80,
    'ShutdownParams::high_pin_v': 2.31,
    'ShutdownParams::low_pin_v': 2.00,
    'ShutdownParams::debounce_ms': 10,
    'DigitalParams::rtd_debounce_ms': 20,
    'DriveParams::rtd_max_pedal': 0.0,
    'InverterParams::torque_limit_nm': 230.0,
}
_T = THRESHOLDS

# The line pressure at which both BSE pins pass the hard-brake comparators
# (about 531 psi), and the loop voltages the sense taps call high and low
# (about 19.17 and 16.60 V).
HARD_BRAKE_PSI = ((_T['BseParams::hard_brake_pin_v'] / _T['AnalogParams::sensor_divider_ratio']
                   - _T['BseParams::v_at_0_psi'])
                  / (_T['BseParams::v_at_full'] - _T['BseParams::v_at_0_psi'])
                  * _T['BseParams::full_scale_psi'])
LOOP_HIGH_V = _T['ShutdownParams::high_pin_v'] / _T['AnalogParams::shutdown_divider_ratio']
LOOP_LOW_V = _T['ShutdownParams::low_pin_v'] / _T['AnalogParams::shutdown_divider_ratio']

# The models leave out ADC rounding, which moves a filter by under half a
# count (about 0.5 psi, or 0.0002 of pedal travel). So a model value is
# called past a threshold only when it is past it by more than these.
SURE_PSI = 1.0
SURE_PEDAL = 0.001
# CONTACTOR_STATUS is 100 Hz, sent in phase from 0 (rig.cpp).
CONTACTOR_STATUS_PERIOD_MS = 10
# The rig's ADCs: 12 bits, with VDDA at 3.3 V from VREFINT
# (App/test/bench.hpp). Only the loop's model rounds to counts: its explain
# note gives the tick a ramp crosses a threshold, where one count decides.
ADC_FULL_SCALE = 4095
ADC_VDDA_V = 3.3

RTD_MS = _T['DigitalParams::rtd_debounce_ms']
LOOP_MS = _T['ShutdownParams::debounce_ms']


def debounced(ms: int) -> int:
    """The tick a debounce of ms passes, counted from the first sample."""
    return -(-ms // STEP_MS) * STEP_MS


@dataclass(frozen=True)
class Change:
    """A change of a raw input at sample index k, and what the firmware's
    debounce makes of it: seen is the index of the tick its debounced level
    takes it. Otherwise why says: 'boot' (an RTD press before the switch has
    read released), 'short' (it changes back first), 'end' (the run ends
    first) or 'held' (the debounced level already reads it)."""
    k: int
    to: bool
    seen: int | None
    why: str = ''


def debounce(raw: list[bool], times: list[int], ms: int, initial: bool) -> list[bool]:
    """longhorn::Debouncer ticked once per sample: the level after each tick."""
    value = candidate = initial
    started, since = False, 0
    levels = []
    for t, r in zip(times, raw, strict=True):
        if not started or r != candidate:
            started, candidate, since = True, r, t
        if candidate != value and t - since >= ms:  # lhal::ElapsedMs
            value = candidate
        levels.append(value)
    return levels


def changes(raw: list[bool], reported: list[bool | None], before: bool | None,
            unarmed: list[bool] | None = None) -> list[Change]:
    """Each change of raw, and the first sample against before: what the
    reported level (None while the firmware reports nothing) does with it."""
    found = []
    for k, r in enumerate(raw):
        if k and r == raw[k - 1]:
            continue
        if r == (reported[k - 1] if k else before):
            why = 'boot' if r and unarmed is not None and unarmed[k] else 'held'
            found.append(Change(k, r, None, why))
            continue
        j = k
        while j < len(raw) and raw[j] == r and reported[j] != r:
            j += 1
        if j == len(raw):
            found.append(Change(k, r, None, 'end'))
        elif raw[j] != r:
            found.append(Change(k, r, None, 'short'))
        else:
            found.append(Change(k, r, j))
    return found


@dataclass(frozen=True)
class Switch:
    """The RTD switch as the firmware reads it: longhorn::EdgeButton."""
    levels: list[bool]  # the debounced level after each tick (din_rtd_pressed)
    edges: list[int]  # the ticks EdgeButton reports a press on
    changes: list[Change]


def rtd_switch(raw: list[bool], times: list[int]) -> Switch:
    # EdgeButton holds Debouncer{true} and last_ = true, so the switch reads
    # pressed from boot and arms only once it has read released.
    levels = debounce(raw, times, RTD_MS, True)
    armed, last = False, True
    edges, unarmed = [], []
    for k, pressed in enumerate(levels):
        if not pressed:
            armed = True
        elif not last and armed:
            edges.append(k)
        last = pressed
        unarmed.append(not armed)
    return Switch(levels, edges, changes(raw, levels, True, unarmed))


def loop_pin_v(volts: float) -> float:
    """A sense tap's pin volts as the firmware computes them from the ADC
    counts the rig sets for shutdown_loop_v (bench.hpp SetLoop, analog.cpp)."""
    counts = int(volts * _T['AnalogParams::shutdown_divider_ratio'] / ADC_VDDA_V
                 * ADC_FULL_SCALE + 0.5)
    return min(counts, ADC_FULL_SCALE) * ADC_VDDA_V / ADC_FULL_SCALE


@dataclass(frozen=True)
class Loop:
    """ShutdownLoop::Update on both taps, which the rig drives alike."""
    taps: list[bool]  # each tap's hysteresis, high or not
    reported: list[bool | None]  # closed, open, or None while UNKNOWN
    changes: list[Change]


def shutdown_loop(volts: list[float], times: list[int]) -> Loop:
    high, taps = False, []
    for v in volts:
        pin = loop_pin_v(v)
        if pin >= _T['ShutdownParams::high_pin_v']:
            high = True
        elif pin < _T['ShutdownParams::low_pin_v']:
            high = False
        taps.append(high)
    levels = debounce(taps, times, LOOP_MS, False)
    # UNKNOWN until both taps have had a full debounce from the first tick.
    reported = [level if t >= LOOP_MS else None
                for t, level in zip(times, levels, strict=True)]
    return Loop(taps, reported, changes(taps, reported, None))


def ema(samples: list[float], alpha: float) -> list[float]:
    """longhorn::Ema per tick, seeded by the first sample."""
    filtered, y = [], 0.0
    for k, x in enumerate(samples):
        y = x if k == 0 else alpha * x + (1.0 - alpha) * y
        filtered.append(y)
    return filtered


def hysteresis(values: list[float], on: float, off: float) -> list[bool]:
    """pedals.cpp's brake-pressed: on at or over on, off under off, else the
    state before, from off."""
    state, states = False, []
    for v in values:
        if v >= on:
            state = True
        elif v < off:
            state = False
        states.append(state)
    return states
