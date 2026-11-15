"""Run output: trace.csv, meta.json, the plot and a short text summary."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .cosim import BSPD_TRIP_MS, COLUMNS, STEP_MS, Result

# VcuStateMachine values in startup order, so the state plot climbs as the
# car powers up: (value, wire name).
_STATE_AXIS = ((0, 'INIT'), (1, 'IDLE'), (4, 'PRECHARGE'), (5, 'TS_ACTIVE'), (6, 'RTD_WAIT'),
               (2, 'DRIVE'), (3, 'FAULT'), (7, 'BSPD_TRIPPED'))


def _cell(value) -> object:
    if value is None:
        return ''
    return repr(value) if isinstance(value, float) else value


def write_trace(result: Result, path: Path) -> None:
    """One row per 3 ms step: COLUMNS, then the model truth as truth_<key>.
    Floats are written with repr, which round-trips exactly, so two traces
    compare bit for bit. A truth value the model did not produce is empty."""
    truth_keys = list(dict.fromkeys(k for t in result.truth for k in t))
    with open(path, 'w', newline='') as f:
        writer = csv.writer(f, lineterminator='\n')
        writer.writerow([*COLUMNS, *(f'truth_{k}' for k in truth_keys)])
        for i, row in enumerate(result.rows):
            truth = result.truth[i] if i < len(result.truth) else {}
            writer.writerow([*(_cell(row[c]) for c in COLUMNS),
                             *(_cell(truth.get(k)) for k in truth_keys)])


def read_trace(path: Path) -> list[dict[str, float]]:
    """Every cell as a float; an empty cell is NaN."""
    with open(path, newline='') as f:
        return [{k: float(v) if v != '' else float('nan') for k, v in row.items()}
                for row in csv.DictReader(f)]


def write_meta(meta: dict[str, object], path: Path) -> None:
    path.write_text(json.dumps(meta, indent=2, sort_keys=True) + '\n')


def plot(result: Result, path: Path, title: str) -> None:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    rows = result.rows
    t = [r['t_ms'] / 1000 for r in rows]
    fig, axes = plt.subplots(5, 1, figsize=(11, 13), sharex=True,
                             gridspec_kw={'height_ratios': [3, 3, 2, 2, 2]})
    speed, torque, power, pedals, state = axes

    speed.plot(t, [r['rearU'] for r in rows], label='car speed (model)', color='C0')
    speed.plot(t, [r['wheelSpeedRL'] * 0.2045 for r in rows], label='rear-left wheel x r',
               color='C1', lw=0.8, alpha=0.8)
    speed.set_ylabel('m/s')
    speed.legend(loc='upper left')

    torque.plot(t, [r['fw_torque_lookup_nm'] for r in rows], label='firmware: pedal map',
                color='C2', lw=0.8, ls='--')
    torque.plot(t, [r['fw_torque_request_nm'] for r in rows],
                label='firmware: INVERTER_TORQUE_COMMAND', color='C3')
    torque.plot(t, [r['motorTorqueActual'] for r in rows], label='model: motor shaft torque',
                color='C0', lw=0.8)
    torque.set_ylabel('N.m')
    torque.legend(loc='upper right')

    power.plot(t, [r['dcBusVoltage'] * r['dcBusCurrent'] / 1e3 for r in rows],
               label='model: DC bus V x I', color='C0')
    power.plot(t, [r['fw_power_w'] / 1e3 for r in rows],
               label='firmware: inverter-reported V x I (10 Hz)', color='C3', lw=0.8)
    power.set_ylabel('kW')
    power.legend(loc='upper right')

    pedals.plot(t, [r['accelerator'] for r in rows], label='accelerator (driver)', color='C2')
    pedals.plot(t, [r['fw_pedal_travel'] for r in rows], label='firmware: torque pedal input',
                color='C3', lw=0.8)
    pedals.plot(t, [r['brake_psi'] / 1000 for r in rows], label='brake (driver), 1000 psi',
                color='C4')
    pedals.set_ylabel('0..1')
    pedals.legend(loc='upper right')

    position = {value: i for i, (value, _) in enumerate(_STATE_AXIS)}
    state.step(t, [position[int(r['fw_state'])] for r in rows], where='post', color='C3')
    state.set_yticks(range(len(_STATE_AXIS)), [name for _, name in _STATE_AXIS])
    state.set_ylabel('VCU state')
    state.set_xlabel('time (s)')

    for ax in axes:
        ax.grid(alpha=0.3)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def summary_text(summary: dict[str, object], result: Result, meta: dict[str, object],
                 wall_time: bool = True) -> str:
    """The summary; without wall_time it is a function of the run alone."""
    lines = [f'scenario {result.scenario}: {result.status} after '
             f'{result.steps * STEP_MS / 1000:.3f} s simulated'
             + (f' in {result.wall_s:.1f} s' if wall_time else '')]
    if result.failure:
        kind = 'plant failure' if result.status == 'plant_failed' else 'harness error'
        lines.append(f'  {kind}: {result.failure}')
        lines += [f'    {line}' for line in result.failure_lines[:4]]
    if summary.get('steps'):
        lines += [
            f'  states: {" -> ".join(summary["states"])}',
            f'  DRIVE at {summary["first_drive_ms"]} ms'
            if summary['first_drive_ms'] is not None else '  never reached DRIVE',
            f'  speed: peak {summary["peak_speed_m_s"]:.2f} m/s, final '
            f'{summary["final_speed_m_s"]:.2f} m/s, min {summary["min_speed_m_s"]:.3f} m/s',
            f'  peak torque command {summary["peak_torque_request_nm"]:.1f} N.m, '
            f'peak DC power {summary["peak_dc_power_kw"]:.1f} kW',
            f'  faults seen: {", ".join(summary["faults_seen"]) or "none"}',
        ]
        if summary['bspd_condition_ms'] >= BSPD_TRIP_MS:
            lines.append(
                f'  WARNING: hard braking with more than 5 kW for {summary["bspd_condition_ms"]} '
                f'ms: the car\'s BSPD would have tripped; the SIL does not model it')
        spin = summary['backward_spin']
        if spin['rows']:
            lines.append(
                f'  car model: motor or wheels spun backward in {spin["rows"]} steps between '
                f'{spin["first_ms"]} and {spin["last_ms"]} ms (min motor '
                f'{summary["min_motor_speed_rad_s"]:.1f} rad/s); the BobLib driveline rings '
                f'when torque steps up from rest')
    if 'scenario_file' in meta:
        file = meta['scenario_file']
        lines.append(f'  scenario file {file["path"]} (sha256 {file["sha256"][:12]})')
        if 'scenario_parameters' in meta:
            lines.append(f'  scenario description: {meta["scenario_parameters"]["description"]}')
        active = list(dict.fromkeys(w['code'] for w in meta.get('scenario_warnings', [])))
        allowed = list(dict.fromkeys(w['code'] for w in meta.get('scenario_allowed', [])))
        lines.append(f'  scenario warnings: {", ".join(active) or "none"}'
                     + (f'; allowed: {", ".join(allowed)}' if allowed else ''))
    checks = meta.get('checks')
    if checks:
        lines.append(f'  {checks["headline"]}')
        lines += [f'    {line}' for line in checks['report']]
        lines += [f'    accepted: {what}' for what in checks['accepted']]
    repo = meta.get('repo', {})
    if repo.get('vehicle_matches_fmu') is False:
        lines.append('  WARNING: the FMU was built from a different vehicle.yml than this checkout')
    if repo.get('tires_match_fmu') is False:
        lines.append('  WARNING: the FMU was built from tire files that differ from this checkout')
    if repo.get('bobsim_pin_matches_fmu') is False:
        lines.append('  WARNING: the FMU was built from a different BobSim than this checkout pins')
    if not meta.get('fmu', {}).get('verified', True):
        lines.append('  WARNING: the FMU does not match its provenance manifest')
    return '\n'.join(lines)
