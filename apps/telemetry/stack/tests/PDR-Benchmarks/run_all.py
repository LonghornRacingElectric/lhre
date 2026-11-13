"""Durable sequential job: 30-minute ingestion, large history reads, freshness."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import signal
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--legacy-root', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--history-hours', type=float, default=24)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[5]
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    state = {'pid': os.getpid(), 'started_at': datetime.now(timezone.utc).isoformat(), 'phases': []}
    status_path = output / 'status.json'
    def save():
        temp = status_path.with_suffix('.tmp')
        temp.write_text(json.dumps(state, indent=2) + '\n')
        temp.replace(status_path)
    runner = root / 'apps/telemetry/stack/tests/PDR-Benchmarks/run.sh'
    phases = [
        ('ingest', ['--legacy-root', str(args.legacy_root), '--report', str(output / 'ingest.json')]),
        ('reads', ['--history-hours', str(args.history_hours), '--report', str(output / 'reads.json')]),
        ('recent', ['--windows', '10', '--clients', '1', '--aggregates', 'max', '--report', str(output / 'freshness.json')]),
    ]
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"Signal {signum}")
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    def run(command, log):
        child = subprocess.Popen(command, cwd=root, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        state["child_pid"] = child.pid
        save()
        try:
            return child.wait()
        except BaseException:
            os.killpg(child.pid, signal.SIGINT)
            try:
                child.wait(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=15)
            raise
    try:
        for name, options in phases:
            phase = {'name': name, 'status': 'running', 'started_at': datetime.now(timezone.utc).isoformat(), 'log': str(output / (name + '.log'))}
            state['phases'].append(phase)
            save()
            with Path(phase['log']).open('w') as log:
                code = run(['bash', str(runner), name, *options], log)
            phase.update(status='passed' if code == 0 else 'failed', exit_code=code, ended_at=datetime.now(timezone.utc).isoformat())
            save()
            reports = [str(output / (n + '.json')) for n in ('ingest', 'reads', 'freshness') if (output / (n + '.json')).exists()]
            with (output / 'report.log').open('a') as log:
                code = run(['bazel', 'run', '--config=local', '//apps/telemetry/stack/tests:pdr_report', '--', *reports, '--output', str(output / 'results.md'), '--graphs-dir', str(output / 'graphs')], log)
            phase['report_exit_code'] = code
            save()
        state['status'] = 'complete' if all(p['status'] == 'passed' and not p.get('report_exit_code') for p in state['phases']) else 'completed_with_failures'
    except BaseException as exc:
        state['status'] = 'interrupted'
        state['error'] = str(exc)
        raise
    finally:
        state['ended_at'] = datetime.now(timezone.utc).isoformat()
        save()


if __name__ == '__main__':
    main()
