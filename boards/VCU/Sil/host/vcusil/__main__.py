"""python -m vcusil: run one scenario of the VCU firmware against the car model.

Exit status: 0 the run completed, 1 the car model failed, 2 the harness or
its inputs are wrong (nothing about the firmware can be concluded).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import traceback
from pathlib import Path

from . import candecode, cosim, events, firmware, mcaplog, plant, provenance, report
from .scenario import SCENARIOS

EXIT_COMPLETED, EXIT_PLANT_FAILED, EXIT_HARNESS = 0, 1, 2
EXIT_FOR_STATUS = {'completed': EXIT_COMPLETED, 'plant_failed': EXIT_PLANT_FAILED,
                   'harness_error': EXIT_HARNESS}

# host/vcusil/__main__.py -> the repository root.
REPO = Path(__file__).resolve().parents[5]

# What a run writes; a reused --out loses the previous run's copies first.
OUTPUT_FILES = ('meta.json', 'trace.csv', 'run.mcap', 'plot.png', 'summary.txt', 'console.log',
                'fmu.log')


def _fraction(text: str) -> float:
    value = float(text)
    if not 0.0 <= value <= 1.0:  # also false for nan
        raise argparse.ArgumentTypeError(f'{text} is not a fraction in [0, 1] (0.9 means 90%)')
    return value


def _positive(text: str) -> float:
    value = float(text)
    if not (math.isfinite(value) and value > 0.0):
        raise argparse.ArgumentTypeError(f'{text} is not a positive number')
    return value


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog='vcusil', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--lib', type=Path, required=True, help='libvcu_sil.so')
    p.add_argument('--fmu', type=Path, required=True, help='OrionTorqueFMI.fmu')
    p.add_argument('--out', type=Path, required=True, help='run directory (created)')
    p.add_argument('--can-table', type=Path,
                   help='vcu_sil_can.json from //boards/VCU:vcu_sil_can_table; without it '
                        'run.mcap keeps CAN frames undecoded')
    p.add_argument('--scenario', choices=sorted(SCENARIOS), default='launch')
    p.add_argument('--soc', type=_fraction, default=0.9,
                   help='initial state of charge, 0..1 (default 0.9; at 1.0 the model '
                        'accepts no regen)')
    p.add_argument('--event-mode', type=int, default=0, choices=range(5),
                   help='lhre EventMode: 0 boots like the car (ENDURANCE), 1-4 pick an event')
    p.add_argument('--brake-psi-full', type=_positive,
                   default=cosim.BRAKE_PSI_AT_FULL_PLANT_BRAKE,
                   help='line pressure that is full brake in the car model (default 1500)')
    p.add_argument('--repo', type=Path, default=REPO, help='checkout the firmware came from')
    p.add_argument('--allow-unverified-fmu', action='store_true',
                   help='run an FMU that does not match a provenance.json beside it')
    p.add_argument('--no-plot', action='store_true')
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)  # bad arguments exit 2 here, through argparse
    meta: dict[str, object] = {}
    try:
        return _run(args, meta)
    except Exception as exc:  # noqa: BLE001 anything unplanned is a harness error, reported below
        traceback.print_exc()
        print(f'vcusil: harness error: {exc}', file=sys.stderr)
        meta.update(status='harness_error', failure=f'{type(exc).__name__}: {exc}')
        try:
            report.write_meta(meta, args.out / 'meta.json')
        except OSError:
            pass
        return EXIT_HARNESS


def _run(args: argparse.Namespace, meta: dict[str, object]) -> int:
    args.out.mkdir(parents=True, exist_ok=True)
    for name in OUTPUT_FILES:
        (args.out / name).unlink(missing_ok=True)
    scenario = SCENARIOS[args.scenario]
    meta.update({
        'scenario': scenario.name,
        'scenario_parameters': vars(scenario),
        'step_ms': cosim.STEP_MS,
        'brake_psi_at_full_plant_brake': args.brake_psi_full,
        'event_mode': args.event_mode,
        'soc_start_requested': args.soc,
    })
    try:
        meta['fmu'] = provenance.fmu_provenance(args.fmu, args.allow_unverified_fmu)
        lib = firmware.load_library(args.lib)
        table = candecode.CanTable(args.can_table) if args.can_table else None
    except (provenance.ProvenanceError, firmware.FirmwareError, OSError, ValueError,
            KeyError) as exc:
        return _fail(meta, args.out, 'harness_error', str(exc))
    meta['can_table'] = ({'path': str(args.can_table), 'spec_sha256': table.spec_sha256,
                          'unsupported': table.unsupported} if table else None)
    meta['library'] = {'path': str(args.lib), 'sha256': provenance.sha256(args.lib),
                       'abi_version': firmware.ABI_VERSION}
    meta['repo'] = provenance.repo_state(args.repo, meta['fmu']['manifest'])
    meta['host'] = provenance.host_info()

    fmu_log = args.out / 'fmu.log'
    fmu_log.write_bytes(b'')
    with firmware.Firmware(lib, args.event_mode) as fw:
        meta['library']['build'] = fw.build_info()
        try:
            with plant.capture_output(fmu_log):
                with plant.Plant(args.fmu, soc_start=args.soc) as car:
                    meta['plant'] = {'soc_start': car.soc_start, 'series_cells': car.series_cells,
                                     'peak_timer': car.peak_timer, 'guid': car.guid,
                                     'truth_units': car.truth_units,
                                     'truth_missing': car.truth_missing,
                                     'slip_from_states': car.slip_from_states}
                    result = cosim.run(scenario, fw, car, args.brake_psi_full)
        except plant.InterfaceError as exc:
            return _fail(meta, args.out, 'harness_error', str(exc))
        except plant.PlantError as exc:
            return _fail(meta, args.out, 'plant_failed', f'the car model failed to start: {exc}',
                         _log_lines(fmu_log))
        (args.out / 'console.log').write_text(fw.read_console())
        summary = cosim.summarize(result, fw.state_name, fw.fault_names)
        marks = events.derive(result, fw.state_name, fw.fault_name, fw.transition_reason)

    meta.update(status=result.status, failure=result.failure, wall_s=result.wall_s,
                failure_lines=result.failure_lines + _log_lines(fmu_log) if result.failure else [],
                summary=summary,
                events=[{'t_ms': e.t_ms, 'level': e.level, 'name': e.name, 'message': e.message}
                        for e in marks],
                frames_dropped=int(result.rows[-1]['fw_frames_dropped']) if result.rows else 0)
    report.write_trace(result, args.out / 'trace.csv')
    text = report.summary_text(summary, result, meta)
    # run.mcap carries no wall-clock time, so the same run gives the same bytes.
    logged_meta = {k: v for k, v in meta.items() if k != 'wall_s'}
    meta['mcap'] = mcaplog.write(
        args.out / 'run.mcap', result, marks, fw.state_name, fw.fault_name,
        fw.transition_reason, table,
        metadata={'scenario': scenario.name, 'status': result.status,
                  'firmware': meta['library']['build']['git_describe'],
                  'firmware_dirty': meta['library']['build']['dirty'],
                  'fmu_sha256': meta['fmu']['fmu_sha256']},
        attachments={
            'summary.txt': (report.summary_text(summary, result, meta, wall_time=False)
                            + '\n').encode(),
            'meta.json': json.dumps(logged_meta, indent=2, sort_keys=True).encode()})
    report.write_meta(meta, args.out / 'meta.json')
    if not args.no_plot and result.rows:
        report.plot(result, args.out / 'plot.png',
                    f'VCU firmware {meta["library"]["build"]["git_describe"]} '
                    f'driving the car model: {scenario.name}')
    (args.out / 'summary.txt').write_text(text + '\n')
    print(text)
    return EXIT_FOR_STATUS[result.status]


def _fail(meta: dict[str, object], out: Path, status: str, failure: str,
          failure_lines: list[str] | None = None) -> int:
    print(f'vcusil: {failure}', file=sys.stderr)
    meta.update(status=status, failure=failure, failure_lines=failure_lines or [])
    report.write_meta(meta, out / 'meta.json')
    return EXIT_FOR_STATUS[status]


def _log_lines(fmu_log: Path, limit: int = 10) -> list[str]:
    """The runtime's own failure lines from fmu.log: OpenModelica prints
    the cause there, not through the FMI logger."""
    if not fmu_log.exists():
        return []
    keys = ('error', 'fail', 'violat', 'assert')
    lines = [line.strip() for line in fmu_log.read_text(errors='replace').splitlines()
             if any(k in line.lower() for k in keys)]
    return list(dict.fromkeys(lines))[-limit:]


if __name__ == '__main__':
    sys.exit(main())
