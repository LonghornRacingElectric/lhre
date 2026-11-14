"""Warnings on scenario files, and python3 -m vcusil.check.

A warning marks a firmware trap: a value too close to a threshold to rely
on, an RTD press the firmware never sees or may refuse, the pedal and the
brake together. Where the firmware's past decides, the rules run fwmodel.py's
models of it, one control tick per 3 ms sample: the RTD switch's debounce,
the brake and pedal filters, the brake-pressed hysteresis and the shutdown
loop's taps. test_check_firmware.py holds each model to the library. Fault drives
cross the traps on purpose, so a run still goes ahead. A file's allow:
waives one with a reason; --strict (CI) fails on any other. It needs only
PyYAML beyond the standard library: no Docker, no build and no FMU.
"""

from __future__ import annotations

import importlib.util
import sys

# Before scenario_file imports yaml, so a bare python3 gets the fix, not a
# traceback. 3.11 for sys.get_int_max_str_digits. python -m vcusil imports
# this too, so the message is for both; a system python3 may refuse pip
# install (PEP 668), and uv fetches both for the one command.
if sys.version_info < (3, 11) or importlib.util.find_spec('yaml') is None:
    print('vcusil needs Python 3.11 or newer with PyYAML; start the command with uv run '
          '--no-project --python 3.12 --with PyYAML, which fetches both for it, or use '
          'boards/VCU/Sil/run.sh, which runs vcusil in the SIL container', file=sys.stderr)
    raise SystemExit(2)

import argparse
import math
from dataclasses import dataclass

from .cosim import BRAKE_PSI_AT_FULL_PLANT_BRAKE, STEP_MS
from .firmware import HVC_ENERGIZED, HVC_STATES
from .fwmodel import (CONTACTOR_STATUS_PERIOD_MS, HARD_BRAKE_PSI, LOOP_HIGH_V, LOOP_LOW_V,
                      LOOP_MS, RTD_MS, SURE_PEDAL, SURE_PSI, THRESHOLDS, Change, debounced, ema,
                      rtd_switch, shutdown_loop)
from .scenario import DEFAULTS
from .scenario_file import (Block, Problem, Ramp, ScenarioError, ScenarioFile, _count, _yaml,
                            first_step_ms, last_step_ms, load, misnamed_paths,
                            scenario_paths)

_T = THRESHOLDS

# A count of the 12-bit ADC is about 0.9 psi and 7 mV of loop, so these
# margins leave rounding no say. A ramp passing through a band is fine:
# only a value held for a sample decides a state.
BRAKE_MARGIN_PSI = 25.0
LOOP_MARGIN_V = 0.5
BRAKE_BANDS = (
    (_T['BseParams::brake_light_psi'] - BRAKE_MARGIN_PSI,
     _T['BseParams::brake_light_psi'] + BRAKE_MARGIN_PSI,
     f'the {_T["BseParams::brake_light_psi"]:g} psi RTD brake (brake_light_psi)'),
    (_T['BseParams::pressed_off_psi'] - BRAKE_MARGIN_PSI,
     _T['BseParams::pressed_on_psi'] + BRAKE_MARGIN_PSI,
     f'the brake-pressed hysteresis, on at {_T["BseParams::pressed_on_psi"]:g} and off under '
     f'{_T["BseParams::pressed_off_psi"]:g} psi of filtered brake (inside '
     f'{_T["BseParams::pressed_off_psi"]:g}..{_T["BseParams::pressed_on_psi"]:g} the previous '
     'state decides)'),
    (HARD_BRAKE_PSI - BRAKE_MARGIN_PSI, HARD_BRAKE_PSI + BRAKE_MARGIN_PSI,
     f'the hard-brake comparators at about {HARD_BRAKE_PSI:.0f} psi (hard_brake_pin_v)'),
)
# Rounded to the 10 mV the messages print, so the band printed is the band
# used; either edge is still about 0.5 V (some 75 counts) from its threshold.
LOOP_BAND = (round(LOOP_LOW_V - LOOP_MARGIN_V, 2), round(LOOP_HIGH_V + LOOP_MARGIN_V, 2))
_LOOP_WHY = (f'within {LOOP_MARGIN_V:g} V of the loop\'s low ({LOOP_LOW_V:.2f} V) or high '
             f'({LOOP_HIGH_V:.2f} V) threshold, or between them, where it reads what it read '
             'before')
# The car model reverses the car near its full brake (README).
BRAKE_HIGH_FRACTION = 2.0 / 3.0
# At or under PEDAL_NONE the pedal asks for no torque; from PEDAL_FULL on,
# for full-pedal torque.
PEDAL_NONE = _T['AppsParams::min_travel_deadzone']
PEDAL_FULL = _T['AppsParams::max_travel_deadzone']
# Brake-over-throttle latches on brake-pressed with the raw pedal over 25 %
# and holds until the pedal is under 5 %. Brake-pressed is a hysteresis on
# the filtered brake (on at 300, off under 250 psi); the lint's brake "may be
# pressed" from 275 until under 225 psi of its model of the filter, so it
# covers every tick the firmware's is.
BOT_PEDAL = _T['BseParams::bot_pedal_on'] - 0.02
BOT_ON_PSI = _T['BseParams::pressed_on_psi'] - BRAKE_MARGIN_PSI
BOT_OFF_PSI = _T['BseParams::pressed_off_psi'] - BRAKE_MARGIN_PSI
# RTD is decided at the debounced press edge: filtered brake of 30 psi or
# more and a filtered pedal inside the 9 % deadzone (rtd_max_pedal 0), in
# RTD_WAIT. The lint warns within these margins of both. An edge outside
# RTD_WAIT is used up, and RTD_WAIT trails the file by the 10 ms HVC frames,
# the loop debounce and a tick, so the lint wants ready, with the loop above
# the band, from RTD_WINDOW_MS before the press through the edge: a margin,
# not the firmware's own test.
RTD_BRAKE_PSI = _T['BseParams::brake_light_psi'] + BRAKE_MARGIN_PSI
RTD_PEDAL = round(PEDAL_NONE - 0.04, 6)
RTD_WINDOW_MS = 50
RTD_LOOP_V = LOOP_BAND[1]
DISAGREE_MS = 100


def _pct(fraction: float) -> str:
    return f'{fraction * 100:g} %'


# A change must hold on every sample through the one its debounce passes on,
# so the next change can come this long after it at the earliest.
_RTD_HOLD_MS = debounced(RTD_MS) + STEP_MS

RULES = {
    'brake_near_threshold': f'a held brake_psi within {BRAKE_MARGIN_PSI:g} psi of 30, 250, 300 '
                            f'or {HARD_BRAKE_PSI:.0f} psi: too close to the threshold to rely on '
                            '(inside the 250..300 hysteresis the previous state decides)',
    'brake_high': 'a held brake_psi over 2/3 of --brake-psi-full: the car model reverses near '
                  'full brake',
    'pedal_in_deadzone': f'a held accelerator in the {_pct(PEDAL_NONE)} or {_pct(PEDAL_FULL)} '
                         'deadzone, which asks for no torque or for full-pedal torque',
    'loop_near_threshold': f'a held shutdown_loop_v in {LOOP_BAND[0]:.2f}..{LOOP_BAND[1]:.2f} V: '
                           + _LOOP_WHY,
    'brake_over_throttle': f'accelerator over {_pct(BOT_PEDAL)} while the modelled filtered brake '
                           f'may hold brake-pressed (from {BOT_ON_PSI:g} psi until under '
                           f'{BOT_OFF_PSI:g}; the firmware\'s is 300/250): torque is cut until '
                           'the pedal is under 5 %',
    'rtd_at_boot': f'an RTD press before the switch has read released for {RTD_MS} ms since '
                   'boot: it starts as pressed, so the firmware never sees the press',
    'rtd_too_short': f'an rtd_pressed change that does not hold for the {RTD_MS} ms debounce (it '
                     'changes back, or the run ends, first): the firmware never sees it',
    'rtd_without_brake': f'the modelled filtered brake at a debounced RTD press edge under '
                         f'{RTD_BRAKE_PSI:g} psi: RTD needs '
                         f'{_T["BseParams::brake_light_psi"]:g} psi (brake_light_psi)',
    'rtd_with_pedal': f'the modelled filtered accelerator at a debounced RTD press edge over '
                      f'{_pct(RTD_PEDAL)}: RTD needs it at or under the {_pct(PEDAL_NONE)} '
                      'deadzone (min_travel_deadzone)',
    'rtd_before_ready': f'an RTD press without hvc_state ENERGIZED, contactors_closed true and '
                        f'shutdown_loop_v over {RTD_LOOP_V:.2f} V from {RTD_WINDOW_MS} ms before '
                        'it to its debounced edge: RTD_WAIT trails them, so the edge may come '
                        'outside RTD_WAIT, which uses it up',
    'hvc_contactors_disagree': f'hvc_state ENERGIZED without contactors_closed, or the reverse, '
                               f'for over {DISAGREE_MS} ms of the file\'s time',
    'unused_allow': 'an allow entry that never fires: the drive no longer tests what it claims',
}

_HVC_NAMES = {number: name for name, number in HVC_STATES.items()}


@dataclass(frozen=True)
class Findings:
    active: tuple[Problem, ...]
    allowed: tuple[tuple[Problem, str], ...]  # (warning, the file's reason)


class _Lint:
    def __init__(self, sf: ScenarioFile):
        self.sf = sf
        self.times = list(range(0, sf.duration_ms, STEP_MS))
        self.values = {s: [sf.timeline.value(s, t) for t in self.times]
                       for s in ('accelerator', 'brake_psi', 'rtd_pressed', 'hvc_state',
                                 'contactors_closed', 'shutdown_loop_v')}
        # The models, tick by tick (pedals.cpp, debounce.hpp, shutdown_loop.cpp).
        self.brake = ema(self.values['brake_psi'], _T['BseParams::ema_alpha'])
        self.pedal = ema(self.values['accelerator'], _T['AppsParams::ema_alpha'])
        self.may_press = []
        pressed = False
        for f in self.brake:
            if f >= BOT_ON_PSI:
                pressed = True
            elif f < BOT_OFF_PSI:
                pressed = False
            self.may_press.append(pressed)
        self.rtd = rtd_switch(self.values['rtd_pressed'], self.times)
        self.loop = shutdown_loop(self.values['shutdown_loop_v'], self.times)
        self.found: list[Problem] = []

    def source(self, signals: tuple[str, ...], t_ms: int) -> tuple[Block | None, str]:
        """The block that last set any of signals at or before t_ms."""
        found, key = None, signals[0]
        for block in self.sf.blocks:
            if block.t_ms > t_ms:
                break
            for s in signals:
                if s in block.values:
                    found, key = block, s
        return found, key

    def warn(self, code: str, t_ms: int, message: str, block: Block | None, key: str) -> None:
        line = self.sf.lines.block(block.index, key) if block else None
        self.found.append(Problem('warning', code, f'at {t_ms} ms', message, line, t_ms))

    def held(self, signal: str):
        """(block, value, the first sample that holds it) for each step value
        or ramp end that holds for at least one sample. A ramp at T still
        shows the value before it at T, so a value is held through the start
        sample of a ramp after it."""
        last = last_step_ms(self.sf.duration_ms)
        changes = [b for b in self.sf.blocks if signal in b.values]
        for n, block in enumerate(changes):
            value = block.values[signal]
            start = block.t_ms + (value.ramp_ms if isinstance(value, Ramp) else 0)
            value = value.to if isinstance(value, Ramp) else value
            first = first_step_ms(start)
            if n + 1 < len(changes):
                after = changes[n + 1]
                end = after.t_ms + (1 if isinstance(after.values[signal], Ramp) else 0)
            else:
                end = last + 1
            if first < end:
                yield block, value, first

    def run(self, brake_psi_full: float) -> list[Problem]:
        self.held_values(brake_psi_full)
        self.brake_over_throttle()
        self.rtd_rules()
        self.disagree()
        return self.found

    def held_values(self, brake_psi_full: float) -> None:
        high = BRAKE_HIGH_FRACTION * brake_psi_full
        for block, psi, t in self.held('brake_psi'):
            for lo, hi, what in BRAKE_BANDS:
                if lo <= psi <= hi:
                    self.warn('brake_near_threshold', t, f'brake_psi holds {_yaml(psi)}, within '
                              f'{BRAKE_MARGIN_PSI:g} psi of {what}: too close to rely on; hold it '
                              f'under {lo:.0f} or over {hi:.0f}', block, 'brake_psi')
                    break
            if psi > high:
                self.warn('brake_high', t, f'brake_psi holds {_yaml(psi)}, over 2/3 of '
                          f'--brake-psi-full ({high:.0f} psi at {brake_psi_full:g}): the car '
                          'model reverses near full brake', block, 'brake_psi')
        for block, pedal, t in self.held('accelerator'):
            if 0.0 < pedal <= PEDAL_NONE:
                self.warn('pedal_in_deadzone', t, f'accelerator holds {_yaml(pedal)}, inside the '
                          f'{_pct(PEDAL_NONE)} deadzone (min_travel_deadzone): it asks for no '
                          'torque', block, 'accelerator')
            elif PEDAL_FULL <= pedal < 1.0:
                self.warn('pedal_in_deadzone', t, f'accelerator holds {_yaml(pedal)}, past the '
                          f'{_pct(PEDAL_FULL)} deadzone (max_travel_deadzone): it asks for '
                          'full-pedal torque', block, 'accelerator')
        for block, volts, t in self.held('shutdown_loop_v'):
            if LOOP_BAND[0] <= volts <= LOOP_BAND[1]:
                self.warn('loop_near_threshold', t, f'shutdown_loop_v holds {_yaml(volts)}, '
                          f'{_LOOP_WHY}; use about 24.0 for closed or 0.0 for open', block,
                          'shutdown_loop_v')

    def brake_over_throttle(self) -> None:
        # The firmware filters, then updates brake-pressed, then checks BOT,
        # all in one tick; may_press is already per tick, after the filter.
        stretch = False
        for k, t in enumerate(self.times):
            pedal = self.values['accelerator'][k]
            now = self.may_press[k] and pedal > BOT_PEDAL
            if now and not stretch:
                block, key = self.source(('accelerator', 'brake_psi'), t)
                self.warn('brake_over_throttle', t, f'accelerator {_yaml(pedal)} is over '
                          f'{_pct(BOT_PEDAL)} while the filtered brake (about '
                          f'{self.brake[k]:.0f} psi) may hold brake-pressed, on at '
                          f'{_T["BseParams::pressed_on_psi"]:g} and off under '
                          f'{_T["BseParams::pressed_off_psi"]:g} psi: brake-over-throttle cuts '
                          'torque until the pedal is under 5 %', block, key)
            stretch = now

    def rtd_rules(self) -> None:
        last = last_step_ms(self.sf.duration_ms)
        for change in self.rtd.changes:
            s = self.times[change.k]
            block, _ = self.source(('rtd_pressed',), s)
            what = 'press' if change.to else 'release'
            if change.why == 'boot':
                self.warn('rtd_at_boot', s, f'the RTD switch starts as pressed and needs '
                          f'{RTD_MS} ms released before a press counts, so the firmware never '
                          f'sees this press; hold rtd_pressed false for {_RTD_HOLD_MS} ms first',
                          block, 'rtd_pressed')
            elif change.why == 'short' and change.k > 0:  # 0 is the boot reading
                back = next(self.times[j] for j in range(change.k, len(self.times))
                            if self.values['rtd_pressed'][j] != change.to)
                self.warn('rtd_too_short', s, f'this {what} changes back at {back} ms, but the '
                          f'{RTD_MS} ms debounce needs it held through '
                          f'{s + debounced(RTD_MS)} ms, so the firmware never sees it; hold it '
                          f'for {_RTD_HOLD_MS} ms or more', block, 'rtd_pressed')
            elif change.why == 'end' and change.k > 0:
                self.warn('rtd_too_short', s, f'the last sample is at {last} ms, but the '
                          f'{RTD_MS} ms debounce needs this {what} held through '
                          f'{s + debounced(RTD_MS)} ms, so the firmware never sees it; lengthen '
                          'the run or move it earlier', block, 'rtd_pressed')
        presses = {c.seen: c for c in self.rtd.changes if c.to and c.seen is not None}
        for e in self.rtd.edges:
            s, edge = self.times[presses[e].k], self.times[e]
            block, _ = self.source(('rtd_pressed',), s)
            psi = self.brake[e]
            if psi < RTD_BRAKE_PSI:
                need = _T['BseParams::brake_light_psi']
                verdict = ('so the firmware refuses it' if psi < need - SURE_PSI else
                           f'and under {RTD_BRAKE_PSI:g} psi is too close to rely on')
                self.warn('rtd_without_brake', s, f'the filtered brake at the debounced edge '
                          f'({edge} ms) is about {psi:.0f} psi; RTD needs {need:g} psi '
                          f'(brake_light_psi), {verdict}', block, 'rtd_pressed')
            pedal = self.pedal[e]
            if pedal > RTD_PEDAL:
                verdict = ('so the firmware refuses it' if pedal > PEDAL_NONE + SURE_PEDAL else
                           f'and over {_pct(RTD_PEDAL)} is too close to rely on')
                self.warn('rtd_with_pedal', s, f'the filtered accelerator at the debounced edge '
                          f'({edge} ms) is about {pedal * 100:.1f} %; RTD needs it at or under '
                          f'{_pct(PEDAL_NONE)} (min_travel_deadzone), {verdict}', block,
                          'rtd_pressed')
            window = [j for j, t in enumerate(self.times) if s - RTD_WINDOW_MS <= t <= edge]
            unready = next((j for j in window if not self.ready(j)), None)
            if unready is not None:
                self.warn('rtd_before_ready', s, f'hvc_state ENERGIZED, contactors_closed true '
                          f'and shutdown_loop_v over {RTD_LOOP_V:.2f} should hold from '
                          f'{RTD_WINDOW_MS} ms before the press to its debounced edge '
                          f'({self.times[window[0]]}..{edge} ms), but at {self.times[unready]} ms '
                          f'{self.unready(unready)}; RTD_WAIT trails them by the 10 ms HVC frames '
                          'and the loop debounce, so the edge may come outside RTD_WAIT, which '
                          'uses it up', block, 'rtd_pressed')

    def ready(self, j: int) -> bool:
        return (self.values['hvc_state'][j] == HVC_ENERGIZED
                and self.values['contactors_closed'][j]
                and self.values['shutdown_loop_v'][j] > RTD_LOOP_V)

    def unready(self, j: int) -> str:
        why = []
        if self.values['hvc_state'][j] != HVC_ENERGIZED:
            why.append(f'hvc_state is {_HVC_NAMES[self.values["hvc_state"][j]]}')
        if not self.values['contactors_closed'][j]:
            why.append('contactors_closed is false')
        if self.values['shutdown_loop_v'][j] <= RTD_LOOP_V:
            why.append(f'shutdown_loop_v is {_yaml(self.values["shutdown_loop_v"][j])}')
        return ' and '.join(why)

    def disagree(self) -> None:
        # In the file's time: both signals only step, at their blocks' t_ms.
        hvc, closed = DEFAULTS['hvc_state'], DEFAULTS['contactors_closed']
        start = None  # (the block that starts the disagreement, hvc, closed)
        blocks = [b for b in self.sf.blocks
                  if {'hvc_state', 'contactors_closed'} & set(b.values)]
        for block in [*blocks, None]:
            if block is None:
                odd, t = False, self.sf.duration_ms
            else:
                hvc = block.values.get('hvc_state', hvc)
                closed = block.values.get('contactors_closed', closed)
                odd, t = (hvc == HVC_ENERGIZED) != closed, block.t_ms
            if odd and start is None:
                start = (block, hvc, closed)
            elif not odd and start is not None:
                began, was_hvc, was_closed = start
                lasted = t - began.t_ms
                if lasted > DISAGREE_MS:
                    key = 'hvc_state' if 'hvc_state' in began.values else 'contactors_closed'
                    self.warn('hvc_contactors_disagree', first_step_ms(began.t_ms),
                              f'hvc_state {_HVC_NAMES[was_hvc]} with contactors_closed '
                              f'{_yaml(was_closed)} for {lasted} ms; the VCU counts the HVC as '
                              'energized only with both (a forgotten contactors_closed?)',
                              began, key)
                start = None

    def note(self, block: Block, signal: str) -> str:
        """When the firmware's debounce takes what block sets for signal."""
        value = block.values[signal]
        if signal == 'rtd_pressed':
            k = first_step_ms(block.t_ms) // STEP_MS
            change = next((c for c in self.rtd.changes if c.k == k and c.to == value), None)
            return _note(change, self.times, RTD_MS, _yaml(value))
        if signal == 'shutdown_loop_v' and block.t_ms > 0:
            # At 0 it would only say the loop boots: UNKNOWN until 12 ms.
            lo, hi = self.span(block, signal)
            change = next((c for c in self.loop.changes if lo <= c.k < hi), None)
            return _note(change, self.times, LOOP_MS,
                         'the loop ' + ('closed' if change and change.to else 'open'))
        return ''

    def span(self, block: Block, signal: str) -> tuple[int, int]:
        """The sample indices whose value of signal block sets: a ramp at T
        still shows the value before it at T."""
        def lo(b: Block) -> int:
            ramp = isinstance(b.values[signal], Ramp)
            return first_step_ms(b.t_ms + 1 if ramp else b.t_ms) // STEP_MS
        after = [b for b in self.sf.blocks if signal in b.values and b.t_ms > block.t_ms]
        return lo(block), lo(after[0]) if after else len(self.times)


def _note(change: Change | None, times: list[int], ms: int, what: str) -> str:
    if change is None:
        return ''
    if change.seen is not None:
        return f' (the firmware sees it at {times[change.seen]} ms)'
    return {
        'boot': f' (the firmware never sees it: the switch starts as pressed and needs {ms} ms '
                'released first)',
        'short': f' (the firmware never sees it: it changes back before the {ms} ms debounce '
                 'passes)',
        'end': f' (the firmware never sees it: the run ends before the {ms} ms debounce passes)',
        'held': f' (the firmware already reads {what})',
    }[change.why]


def lint(sf: ScenarioFile, brake_psi_full: float = BRAKE_PSI_AT_FULL_PLANT_BRAKE) -> Findings:
    """The warnings on a file that loaded without errors."""
    found = _Lint(sf).run(brake_psi_full)
    fired = {p.code for p in found}
    for code in sf.allow:
        if code not in fired:
            found.append(Problem('warning', 'unused_allow', f'allow.{code}', f'{code} is allowed '
                                 'but never fires; the drive no longer tests what it claims '
                                 '(or delete its line under allow:)', sf.lines.key('allow', code)))
    found.sort(key=lambda p: (p.line or 0, p.t_ms or 0))
    return Findings(
        active=tuple(p for p in found if p.code not in sf.allow),
        allowed=tuple((p, sf.allow[p.code]) for p in found if p.code in sf.allow))


def _describe(signal: str, value: object) -> str:
    if isinstance(value, Ramp):
        return f'{signal} to {_yaml(value.to)} over {value.ramp_ms} ms'
    return f'{signal} {_HVC_NAMES[value] if signal == "hvc_state" else _yaml(value)}'


def explain(sf: ScenarioFile) -> str:
    """When each block reaches the firmware: the first 3 ms sample at or
    after its t_ms (where a ramp still shows the value before it), for the
    HVC state and AIRs the tick that acts on the next CONTACTOR_STATUS frame,
    and for the RTD switch and the loop the tick their debounce passes."""
    d = sf.duration_ms
    models = _Lint(sf)
    rows = [('t_ms', 'step', 'frame', 'block', 'mark', 'sets')]
    for block in sf.blocks:
        step = first_step_ms(block.t_ms)
        frame = ''
        if {'hvc_state', 'contactors_closed'} & set(block.values):
            period = CONTACTOR_STATUS_PERIOD_MS
            frame = str(first_step_ms(-(-step // period) * period))
        sets = [_describe(signal, value) + models.note(block, signal)
                for signal, value in block.values.items()]
        rows.append((str(block.t_ms), str(step), frame, f'at[{block.index}]', block.mark or '',
                     ', '.join(sets)))
    widths = [max(len(r[i]) for r in rows) for i in range(5)]
    lines = [f'{sf.name}: {d} ms, {-(-d // STEP_MS)} steps of {STEP_MS} ms '
             f'(t = 0 .. {last_step_ms(d)} ms)']
    for row in rows:
        cells = [row[0].rjust(widths[0]), row[1].rjust(widths[1]), row[2].rjust(widths[2]),
                 row[3].ljust(widths[3]), row[4].ljust(widths[4]), row[5]]
        lines.append(('  ' + '  '.join(cells)).rstrip())
    if sf.expectations:
        lines += ['', '  expect: the samples each is judged on (README, Checks after the run)']
        lines += [f'    [{e.index}] {e.text()}: {_window(e, d)}' for e in sf.expectations]
    return '\n'.join(lines)


def _window(e, duration_ms: int) -> str:
    if e.verb == 'states':
        return 'the whole run'
    if e.verb == 'reach':
        return (f'first at or after {e.anchor_ms} ms, due from {e.anchor_ms + e.not_before_ms} '
                f'to {e.anchor_ms + e.within_ms} ms')
    last = last_step_ms(duration_ms) if e.stop_ms is None else first_step_ms(e.stop_ms) - STEP_MS
    return f'{first_step_ms(e.start_ms)}..{last} ms'


def _positive(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not (math.isfinite(value) and value > 0.0):
        raise argparse.ArgumentTypeError(f'{text} is not a positive number')
    return value


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog='python3 -m vcusil.check', formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Check scenario files without building or running anything. Exit status '
                    '0: every file can run (and, with --strict, has no warning its allow: does '
                    'not cover); 2: otherwise.',
        epilog='warnings:\n' + '\n'.join(f'  {code}: {why}' for code, why in RULES.items()))
    p.add_argument('--strict', action='store_true',
                   help="fail on any warning the file's allow: does not cover (for CI)")
    p.add_argument('--explain', action='store_true',
                   help='show when each block reaches the firmware')
    p.add_argument('--errors-only', action='store_true',
                   help='report errors only, no warnings (run.sh checks with it before a run, '
                        'which reports the warnings with its own --brake-psi-full)')
    p.add_argument('--brake-psi-full', type=_positive, default=BRAKE_PSI_AT_FULL_PLANT_BRAKE,
                   metavar='PSI', help='as for python -m vcusil (default 1500); brake_high '
                                       'warns over 2/3 of it')
    p.add_argument('scenarios', nargs='*', metavar='NAME|FILE',
                   help='a name from scenarios/ or a .yml file (default: every scenarios/*.yml)')
    args = p.parse_args(argv)
    if args.errors_only and (args.strict or args.explain):
        p.error('--errors-only reports no warnings, so it cannot go with --strict or --explain')
    status = 0
    # As paths, so a badly named file in scenarios/ gets the rename hint,
    # and a .yaml or .toml there fails rather than being skipped unseen.
    for arg in args.scenarios or [str(path) for path in scenario_paths() + misnamed_paths()]:
        try:
            sf = load(arg)
        except ScenarioError as exc:
            for problem in exc.problems:
                print(problem.format(exc.display), file=sys.stderr)
            print(f'FAIL {exc.display or arg}: {_count(len(exc.problems), "error")}, 0 warnings',
                  flush=True)
            status = 2
            continue
        size = (f'{sf.duration_ms} ms, {-(-sf.duration_ms // STEP_MS)} steps, '
                f'{_count(len(sf.blocks), "block")}')
        if args.errors_only:
            print(f'ok {sf.name}: {size}, warnings not checked. {sf.description}', flush=True)
            continue
        findings = lint(sf, args.brake_psi_full)
        for problem in findings.active:
            print(problem.format(sf.display), file=sys.stderr)
        warnings = _count(len(findings.active), 'warning')
        if args.strict and findings.active:
            print(f'FAIL {sf.display}: 0 errors, {warnings} (--strict)', flush=True)
            status = 2
        else:
            allowed = f', {len(findings.allowed)} allowed' if findings.allowed else ''
            print(f'ok {sf.name}: {size}, {warnings}{allowed}. {sf.description}', flush=True)
        if args.explain:
            print(explain(sf), flush=True)
    return status


if __name__ == '__main__':
    sys.exit(main())
