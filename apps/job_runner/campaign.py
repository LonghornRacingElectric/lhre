"""Run and score one bounded gate campaign inside the installed ROS image."""

import argparse
import csv
import json
import os
import signal
from pathlib import Path
import subprocess
import sys


def campaign_environment(args, environ):
    return {**environ, 'OUT': '/results', 'TRACK_STYLE': args.course,
            'SEEDS': str(args.seed), 'REPEATS': str(args.repeats),
            'ACCEL_REPEATS': '0', 'SKIDPAD_REPEATS': '0', 'BASELINE': '0',
            'RUN_S': str(args.sim_seconds), 'SETTLE_S': '5', 'HANG_S': '60',
            'ROS_DOMAIN_ID': '42', 'GZ_PARTITION': 'lhr-campaign',
            'EXTRA': ''}


def execute_campaign(env):
    with subprocess.Popen(['/bin/bash', '/workspace/autonomy/testing/tools/gate/run_gate.sh'],
                          env=env, start_new_session=True) as process:
        def forward(signum, frame):
            os.killpg(process.pid, signum)
        previous = {sig: signal.signal(sig, forward) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            return process.wait()
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--course', choices=['autocross', 'rules_autocross'], required=True)
    parser.add_argument('--seed', type=int, choices=range(1, 6), required=True)
    parser.add_argument('--repeats', type=int, choices=range(1, 4), required=True)
    parser.add_argument('--sim-seconds', type=int, choices=range(30, 181), required=True)
    args = parser.parse_args()
    Path(os.environ['HOME']).mkdir(exist_ok=True, parents=True)
    configuration = {**vars(args), 'revision': os.environ.get('SOURCE_REVISION'),
                     'plant': 'gazebo', 'perception': 'lidar', 'estimator': 'ekf',
                     'safety': True, 'stereo_camera': True, 'camera_lidar_fusion': True}
    Path('/results/configuration.json').write_text(json.dumps(configuration, indent=2))
    print(json.dumps({'event': 'started', **configuration}), flush=True)
    returncode = execute_campaign(campaign_environment(args, os.environ))
    table = Path('/results/results.csv')
    rows = list(csv.DictReader(table.open())) if table.is_file() else []
    print(json.dumps({'template': 'gazebo', 'results': rows,
                      'passed': returncode == 0 and bool(rows)}), flush=True)
    return returncode if returncode else (0 if rows else 1)


if __name__ == '__main__':
    sys.exit(main())
