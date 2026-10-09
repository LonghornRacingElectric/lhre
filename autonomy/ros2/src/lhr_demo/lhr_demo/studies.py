"""Run isolated driving studies and score against scene truth."""

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time

from ackermann_msgs.msg import AckermannDriveStamped
from nav_msgs.msg import Odometry
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from std_msgs.msg import String
from visualization_msgs.msg import MarkerArray

CONDITIONS = {
    'clean': {'motion_distortion': 'false', 'clutter_profile': 'none',
              'return_profile': 'baseline'},
    'motion': {'motion_distortion': 'true', 'clutter_profile': 'none',
               'return_profile': 'baseline'},
    'clutter': {'motion_distortion': 'true', 'clutter_profile': 'trackside',
                'return_profile': 'baseline'},
    'outdoor': {'motion_distortion': 'true', 'clutter_profile': 'trackside',
                'return_profile': 'acceptance_overcast'},
}


class StudyProbe(Node):
    """Measure motion, progress and unlabeled detections without feeding truth back."""

    def __init__(self):
        super().__init__('study_probe')
        self.center = self.truth = None
        self.arc = None
        self.perimeter = 0.
        self.previous = None
        self.previous_s = None
        self.start_s = None
        self.start_time = self.last_time = None
        self.distance = self.progress = self.offtrack_distance = 0.
        self.errors, self.speeds = [], []
        self.halt_seconds = self.longest_halt = self.current_halt = 0.
        self.halts = 0
        self.lap = False
        self.started_moving = False
        self.target_speed = None
        self.samples = []
        self.matches = self.unmatched = self.candidates = 0
        self.provenance = {}
        qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(MarkerArray, '/lhr/track/cones', self.cones, qos)
        self.create_subscription(String, '/lhr/sim/provenance', self.metadata, qos)
        self.create_subscription(Odometry, '/lhr/vehicle/odom', self.odom, 50)
        self.create_subscription(
            AckermannDriveStamped, '/lhr/vehicle/cmd', self.command, 10)
        self.create_subscription(MarkerArray, '/lhr/sensor/cones_detected', self.detected, 10)

    def command(self, message):
        """Keep requested speed beside the measured speed in the trace."""
        self.target_speed = message.drive.speed

    def metadata(self, message):
        """Keep the dynamics provenance supplied by the plant."""
        self.provenance = json.loads(message.data)

    def cones(self, message):
        """Build the reference corridor once from paired scene markers."""
        left = sorted((m for m in message.markers if m.ns == 'left_cones'), key=lambda m: m.id)
        right = sorted((m for m in message.markers if m.ns == 'right_cones'), key=lambda m: m.id)
        if not left or len(left) != len(right):
            return
        a = np.array([[m.pose.position.x, m.pose.position.y] for m in left])
        b = np.array([[m.pose.position.x, m.pose.position.y] for m in right])
        self.center = (a + b) / 2
        self.half_width = np.linalg.norm(a - b, axis=1) / 2
        self.truth = np.concatenate((a, b))
        self.delta = np.roll(self.center, -1, axis=0) - self.center
        self.lengths = np.linalg.norm(self.delta, axis=1)
        self.arc = np.r_[0., np.cumsum(self.lengths)]
        self.perimeter = self.arc[-1]

    def odom(self, message):
        """Score the vehicle against the nearest corridor segment."""
        if self.center is None:
            return
        stamp = message.header.stamp.sec + message.header.stamp.nanosec * 1e-9
        if self.last_time is not None and stamp <= self.last_time:
            return
        position = np.array([message.pose.pose.position.x, message.pose.pose.position.y])
        fraction = np.clip(((position - self.center) * self.delta).sum(axis=1)
                           / self.lengths ** 2, 0., 1.)
        distances = np.linalg.norm(self.center + fraction[:, None] * self.delta - position, axis=1)
        index = int(distances.argmin())
        error = float(distances[index])
        s = self.arc[index] + fraction[index] * self.lengths[index]
        speed = math.hypot(message.twist.twist.linear.x, message.twist.twist.linear.y)
        if speed >= .2:
            self.started_moving = True
        if self.start_time is None:
            self.start_time, self.start_s = stamp, s
        if self.previous is not None:
            travelled = float(np.linalg.norm(position - self.previous))
            self.distance += travelled
            if error > self.half_width[index]:
                self.offtrack_distance += travelled
            self.progress += (s - self.previous_s + self.perimeter / 2) % self.perimeter \
                - self.perimeter / 2
            dt = stamp - self.last_time
            if self.started_moving and stamp - self.start_time > 3 and speed < .2:
                self.current_halt += dt
                self.halt_seconds += dt
                if self.current_halt >= 1. and self.current_halt - dt < 1.:
                    self.halts += 1
                self.longest_halt = max(self.longest_halt, self.current_halt)
            else:
                self.current_halt = 0.
        self.lap = bool(self.progress >= self.perimeter - 1e-6
                        and self.distance >= self.perimeter * .9)
        self.previous, self.previous_s, self.last_time = position, s, stamp
        self.samples.append({'sim_time': stamp, 'x_m': float(position[0]),
                             'y_m': float(position[1]), 'speed_mps': speed,
                             'target_speed_mps': self.target_speed,
                             'error_m': error, 'progress_m': float(self.progress)})
        self.errors.append(error)
        self.speeds.append(speed)

    def detected(self, message):
        """Count mapped detection candidates near actual boundary cone centers."""
        if self.truth is None:
            return
        for marker in message.markers:
            position = np.array([marker.pose.position.x, marker.pose.position.y])
            matched = np.linalg.norm(self.truth - position, axis=1).min() <= .35
            self.matches += int(matched)
            self.unmatched += int(not matched)
            self.candidates += 1

    def result(self, outcome, wall_seconds):
        """Return a compact case record, retaining the scoring limitations."""
        duration = 0. if self.last_time is None else self.last_time - self.start_time
        return {
            'outcome': outcome, 'lap_completed': self.lap, 'sim_seconds': duration,
            'wall_seconds': wall_seconds, 'realtime_factor': duration / max(wall_seconds, .001),
            'distance_m': self.distance, 'progress_m': self.progress,
            'track_length_m': float(self.perimeter), 'offtrack_distance_m': self.offtrack_distance,
            'max_error_m': max(self.errors, default=None),
            'mean_error_m': float(np.mean(self.errors)) if self.errors else None,
            'mean_speed_mps': float(np.mean(self.speeds)) if self.speeds else None,
            'halt_events': self.halts, 'halt_seconds': self.halt_seconds,
            'longest_halt_seconds': self.longest_halt,
            'mapped_candidate_match_fraction': self.matches / self.candidates
            if self.candidates else None,
            'unmatched_mapped_candidates': self.unmatched, 'provenance': self.provenance,
        }


def run_case(case, output, sim_seconds, wall_timeout):
    """Launch one case and always stop its entire process group."""
    output.mkdir(parents=True, exist_ok=True)
    args = ['ros2', 'launch', 'lhr_demo', 'mvs_demo.launch.py',
            'perception:=lidar', 'enable_metrics:=false', 'vehicle_mesh:=false']
    args += [f'{key}:={value}' for key, value in case.items()]
    started = time.monotonic()
    probe = StudyProbe()
    outcome = 'wall_timeout'
    with (output / 'launch.log').open('w') as log:
        process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            while time.monotonic() - started < wall_timeout:
                rclpy.spin_once(probe, timeout_sec=.1)
                if process.poll() is not None:
                    outcome = 'plant_or_launch_exit'
                    break
                if probe.lap:
                    outcome = 'lap'
                    break
                if (probe.last_time is not None
                        and probe.last_time - probe.start_time >= sim_seconds):
                    outcome = 'sim_limit'
                    break
            result = probe.result(outcome, time.monotonic() - started)
            if probe.samples:
                with (output / 'trajectory.csv').open('w', newline='') as stream:
                    writer = csv.DictWriter(stream, fieldnames=list(probe.samples[0]))
                    writer.writeheader()
                    writer.writerows(probe.samples)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            probe.destroy_node()
    result['arguments'] = case
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    """Run the requested matrix and save each verdict, log and aggregate table."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds', type=int, nargs='+', default=[1, 7])
    parser.add_argument('--speeds', type=float, nargs='+', default=[4.])
    parser.add_argument('--plants', nargs='+', choices=['kinematic', 'bobsim'],
                        default=['kinematic', 'bobsim'])
    parser.add_argument('--conditions', nargs='+', choices=list(CONDITIONS),
                        default=['clean', 'motion', 'clutter'])
    parser.add_argument('--sim-seconds', type=float, default=90.)
    parser.add_argument('--wall-timeout', type=float, default=240.)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--domain-id', type=int, default=66)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output already exists; choose a new directory to preserve previous results')
    if min(args.sim_seconds, args.wall_timeout, *args.speeds) <= 0:
        parser.error('durations and speeds must be positive')
    os.environ['ROS_DOMAIN_ID'] = str(args.domain_id)
    args.output.mkdir(parents=True)
    repository = next((parent for parent in Path(__file__).resolve().parents
                       if (parent / '.git').exists()), Path('/opt/lhr'))
    revision = subprocess.check_output(
        ['git', '-C', str(repository), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = bool(subprocess.check_output(
        ['git', '-C', str(repository), 'status', '--porcelain'], text=True).strip())
    fingerprints = {}
    for source in sorted((repository / 'autonomy/ros2/src').rglob('*')):
        if source.is_file() and source.suffix in ('.py', '.yaml', '.json', '.gz'):
            fingerprints[str(source.relative_to(repository))] = hashlib.sha256(
                source.read_bytes()).hexdigest()
    (args.output / 'manifest.json').write_text(json.dumps({
        'git_sha': revision, 'dirty': dirty, 'settings': vars(args),
        'source_sha256': fingerprints},
        default=str, indent=2) + '\n')
    rclpy.init()
    results = []
    try:
        for seed in args.seeds:
            for speed in args.speeds:
                for plant in args.plants:
                    for condition in args.conditions:
                        name = f'seed{seed}-speed{speed:g}-{plant}-{condition}'
                        case = {'seed': seed, 'v_max': speed, 'plant': plant,
                                **CONDITIONS[condition]}
                        result = run_case(case, args.output / name,
                                          args.sim_seconds, args.wall_timeout)
                        result['case'] = name
                        results.append(result)
                        (args.output / 'summary.json').write_text(
                            json.dumps(results, indent=2) + '\n')
                        flat = [{k: v for k, v in item.items()
                                 if k not in ('arguments', 'provenance')}
                                for item in results]
                        with (args.output / 'summary.csv').open('w', newline='') as stream:
                            writer = csv.DictWriter(stream, fieldnames=list(flat[0]))
                            writer.writeheader()
                            writer.writerows(flat)
                        print(name + ': ' + json.dumps(flat[-1]), flush=True)
    finally:
        rclpy.try_shutdown()
    passed = all(result['lap_completed'] and result['offtrack_distance_m'] == 0
                 for result in results)
    raise SystemExit(0 if passed else 1)
