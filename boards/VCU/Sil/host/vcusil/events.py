"""Event markers for the run log: the moments a reader wants to jump to."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from . import cosim
from .firmware import SATURATION_BITS

# foxglove.Log levels.
INFO, WARNING, ERROR, FATAL = 2, 3, 4, 5

# VcuStateMachine values a driver must never see by surprise.
_BAD_STATES = {3, 7}  # FAULT, BSPD_TRIPPED


@dataclass(frozen=True)
class Event:
    t_ms: int
    level: int
    name: str  # what raised it: vcu.state, vcu.fault, rig.saturation, ...
    message: str


def derive(result: cosim.Result, state_name: Callable[[int], str],
           fault_name: Callable[[int], str],
           transition_reason: Callable[[int], str],
           marks: Iterable[tuple[str, int, int]] = ()) -> list[Event]:
    """Events in time order: state changes, faults raised and cleared,
    saturation, car-model backward spin, the BSPD condition, the scenario's
    marks ((name, t_ms, first sample), shown at the sample), the end."""
    events = [Event(0, INFO, 'run', f'scenario {result.scenario} starts')]
    prev_state = prev_faults = prev_saturated = None
    spinning = False
    bspd_ms = 0
    for i, row in enumerate(result.rows):
        t = int(row['t_ms'])
        snap = result.snapshots[i] if i < len(result.snapshots) else {}
        state, faults, saturated = int(row['fw_state']), int(row['fw_faults']), int(row['fw_saturated'])
        tick = int(snap.get('now_ms', row['fw_control_ms']))
        if state != prev_state:
            reason = transition_reason(int(snap['last_transition_id'])) if snap else ''
            before = state_name(prev_state) if prev_state is not None else 'start'
            events.append(Event(tick, ERROR if state in _BAD_STATES else INFO, 'vcu.state',
                                f'{before} -> {state_name(state)}' + (f' ({reason})' if reason else '')))
        for bit in range(32):
            mask = 1 << bit
            was = bool((prev_faults or 0) & mask)
            if bool(faults & mask) != was:
                verb = 'cleared' if was else 'raised'
                events.append(Event(tick, INFO if was else WARNING, 'vcu.fault',
                                    f'fault {verb}: {fault_name(bit + 1)}'))
        for name, mask in SATURATION_BITS.items():
            was = bool((prev_saturated or 0) & mask)
            if bool(saturated & mask) != was:
                events.append(Event(t, INFO if was else WARNING, 'rig.saturation',
                                    f'{name} {"back in range" if was else "clamped to its sensor or field"}'))
        spin = (row['motorSpeed'] < cosim.BACKWARD_MOTOR_RAD_S
                or any(row[w] < cosim.BACKWARD_WHEEL_RAD_S for w in cosim.WHEELS))
        if spin != spinning:
            events.append(Event(t, WARNING if spin else INFO, 'model.backward_spin',
                                'car model: motor or wheels turning backward' if spin
                                else 'car model: no longer turning backward'))
            spinning = spin
        hard = row.get('fw_hard_braking') and row['dcBusVoltage'] * row['dcBusCurrent'] > cosim.BSPD_POWER_W
        before = bspd_ms
        bspd_ms = bspd_ms + cosim.STEP_MS if hard else 0
        if before < cosim.BSPD_TRIP_MS <= bspd_ms:
            events.append(Event(t, ERROR, 'bspd',
                                'hard braking with more than 5 kW for 500 ms: the car\'s BSPD '
                                'would trip here (not modelled)'))
        prev_state, prev_faults, prev_saturated = state, faults, saturated
    end = int(result.rows[-1]['t_ms']) if result.rows else 0
    # A mark after the last row (a run cut short) never happened. Marks go
    # after the derived events, so a sort keeps those first at an equal ms.
    if result.rows:
        events += [Event(step, INFO, 'scenario', f'mark {name} (t_ms {t_ms})')
                   for name, t_ms, step in marks if step <= end]
    if result.status == 'plant_failed':
        events.append(Event(end, FATAL, 'model.failure', f'car model failed: {result.failure}'))
    elif result.status == 'harness_error':
        events.append(Event(end, ERROR, 'harness', f'harness error: {result.failure}'))
    else:
        events.append(Event(end, INFO, 'run', f'scenario {result.scenario} completed'))
    # Stable: the start stays first and the end last.
    return sorted(events, key=lambda e: e.t_ms)
