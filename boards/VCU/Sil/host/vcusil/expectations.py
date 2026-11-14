"""A scenario file's expect: items, judged against its run.

scenario_file.py parses each item and resolves its window to samples; this
judges it on the run's rows: the state and faults the VCU reported, the
torque it sent, and the car model's speed and DC power. A run cut short
judges what it can: a window it never reached is not_evaluated, not failed.
The format is in the README (Checks after the run).
"""

from __future__ import annotations

from dataclasses import dataclass

from .cosim import STEP_MS
from .firmware import VCU_FAULTS, VCU_STATES
from .fwmodel import LOOP_MS
from .scenario_file import Condition, Expectation, ScenarioFile, first_step_ms

_STATE_NAMES = {number: name for name, number in VCU_STATES.items()}
SHUTDOWN_OPEN_BIT = 1 << (VCU_FAULTS['SHUTDOWN_OPEN'] - 1)
# ShutdownLoop reports UNKNOWN, so SHUTDOWN_OPEN, until both taps have had a
# full debounce (shutdown_loop.cpp): on the ticks before LOOP_MS of every run.
# A fault condition does not see it there.
BOOT_ACCEPTED = (f'SHUTDOWN_OPEN on the ticks before {LOOP_MS} ms: the shutdown loop reads '
                 'UNKNOWN until both taps have had their debounce (shutdown_loop.cpp)')


@dataclass(frozen=True)
class Result:
    index: int
    line: int | None
    text: str  # the expectation, one line
    result: str  # 'pass', 'fail' or 'not_evaluated'
    at_ms: int | None  # the tick the verdict rests on, where there is one
    measured: str  # what the run did

    @property
    def failed(self) -> bool:
        return self.result == 'fail'

    def meta(self) -> dict[str, object]:
        return {'index': self.index, 'line': self.line, 'text': self.text,
                'result': self.result, 'at_ms': self.at_ms, 'measured': self.measured}


def _signal(row: dict[str, float | int], name: str) -> float:
    if name == 'torque_nm':
        return float(row['fw_torque_request_nm'])
    if name == 'speed_m_s':
        return float(row['rearU'])
    return float(row['dcBusVoltage']) * float(row['dcBusCurrent']) / 1e3  # dc_power_kw


def _faults(row: dict[str, float | int]) -> int:
    faults = int(row['fw_faults'])
    return faults & ~SHUTDOWN_OPEN_BIT if int(row['t_ms']) < LOOP_MS else faults


def holds(condition: Condition, row: dict[str, float | int]) -> bool:
    """Whether condition holds on row's tick."""
    if condition.kind == 'state':
        return _STATE_NAMES.get(int(row['fw_state'])) == condition.name
    if condition.kind == 'fault':
        faults = _faults(row)
        return faults != 0 if condition.name == 'any' else bool(
            faults >> (VCU_FAULTS[condition.name] - 1) & 1)
    value = _signal(row, condition.name)
    return ((condition.lo is None or value >= condition.lo)
            and (condition.hi is None or value <= condition.hi))


def _shown(condition: Condition, row: dict[str, float | int]) -> str:
    """What a tick shows for the condition's subject."""
    if condition.kind == 'state':
        return _STATE_NAMES.get(int(row['fw_state']), f'state {int(row["fw_state"])}')
    if condition.kind == 'fault':
        faults = _faults(row)
        names = [name for name, code in VCU_FAULTS.items() if faults >> (code - 1) & 1]
        return ', '.join(names) or 'no faults'
    return f'{condition.name} {_signal(row, condition.name):g}'


def evaluate(sf: ScenarioFile, rows: list[dict[str, float | int]]) -> tuple[Result, ...]:
    """Each of the file's expectations on the run's rows (one per 3 ms step
    from 0; fewer than the file's duration when the run stopped early)."""
    complete = len(rows) == -(-sf.duration_ms // STEP_MS)
    last = int(rows[-1]['t_ms']) if rows else -STEP_MS
    return tuple(_judge(e, rows, complete, last) for e in sf.expectations)


def _covers(stop_ms: int | None, complete: bool, last: int) -> bool:
    """Whether the run has every sample before stop_ms (None: the run's end)."""
    return complete if stop_ms is None else complete or last >= first_step_ms(stop_ms) - STEP_MS


def _judge(e: Expectation, rows: list[dict[str, float | int]], complete: bool,
           last: int) -> Result:
    def result(verdict: str, at_ms: int | None, measured: str) -> Result:
        return Result(e.index, e.line, e.text(), verdict, at_ms, measured)

    stopped = (f'the run stopped at {last} ms, before the window ends' if rows
               else 'the run stopped before its first sample')
    if e.verb == 'states':
        states: list[str] = []
        for row in rows:
            name = _STATE_NAMES.get(int(row['fw_state']), f'state {int(row["fw_state"])}')
            if not states or states[-1] != name:
                states.append(name)
        if not complete:
            # The states so far are the list's start, or no later state can mend them.
            if tuple(states) == e.states[:len(states)]:
                return result('not_evaluated', None, stopped)
            return result('fail', None, ' -> '.join(states) + f' ({stopped})')
        return result('pass' if tuple(states) == e.states else 'fail', None,
                      ' -> '.join(states))
    condition = e.condition
    if e.verb == 'reach':
        deadline = e.anchor_ms + e.within_ms
        first = next((row for row in rows if int(row['t_ms']) >= e.anchor_ms
                      and holds(condition, row)), None)
        if first is None:
            if not _covers(deadline + 1, complete, last):
                return result('not_evaluated', None, stopped)
            return result('fail', None, f'{condition.text()} never came after {e.anchor} '
                          f'({e.anchor_ms} ms)')
        t = int(first['t_ms'])
        late = t - e.anchor_ms
        measured = f'first at {t} ms, {late} ms after {e.anchor}'
        if late > e.within_ms:
            return result('fail', t, f'{measured}: {late - e.within_ms} ms late')
        if late < e.not_before_ms:
            return result('fail', t, f'{measured}: {e.not_before_ms - late} ms too soon')
        return result('pass', t, measured)
    window = [row for row in rows if e.start_ms <= int(row['t_ms'])
              and (e.stop_ms is None or int(row['t_ms']) < e.stop_ms)]
    covered = _covers(e.stop_ms, complete, last)
    if e.verb in ('hold', 'never'):
        want = e.verb == 'hold'
        broken = next((row for row in window if holds(condition, row) != want), None)
        if broken is not None:
            t = int(broken['t_ms'])
            return result('fail', t, f'at {t} ms: {_shown(condition, broken)}')
        if not covered:
            return result('not_evaluated', None, stopped)
        span = (f'{len(window)} samples, {int(window[0]["t_ms"])}..{int(window[-1]["t_ms"])} ms'
                if window else 'no samples')
        return result('pass', None, span)
    # peak and low judge the window's highest or lowest value.
    if not window:
        return result('not_evaluated', None, stopped)
    pick = max if e.verb == 'peak' else min
    row = pick(window, key=lambda r: _signal(r, condition.name))
    t, value = int(row['t_ms']), _signal(row, condition.name)
    extreme = 'highest' if e.verb == 'peak' else 'lowest'
    measured = f'{extreme} {value:g} at {t} ms'
    if not covered:
        # A highest value only rises and a lowest only falls, so one already
        # past that side's bound stays past it; any other may still change.
        past = (condition.hi is not None and value > condition.hi if e.verb == 'peak'
                else condition.lo is not None and value < condition.lo)
        return result('fail', t, measured) if past else result('not_evaluated', None, stopped)
    return result('pass' if holds(condition, row) else 'fail', t, measured)
