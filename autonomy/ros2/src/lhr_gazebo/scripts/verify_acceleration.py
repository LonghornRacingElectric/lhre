#!/usr/bin/env python3
"""Run a Gazebo acceleration mission and fail unless the detected-cone loop finishes."""

import argparse
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

from ackermann_msgs.msg import AckermannDriveStamped
from lhr_vehicle import load_vehicle
from nav_msgs.msg import Odometry, Path as NavPath
import rclpy
from rclpy.node import Node
from std_msgs.msg import String


def main():
    """Launch an isolated run, observe its actual ROS outputs, and report results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--perception', choices=['sim', 'lidar'], default='sim')
    parser.add_argument('--timeout', type=float, default=180.)
    parser.add_argument('--gui', action='store_true')
    parser.add_argument('--keep-running', action='store_true')
    args = parser.parse_args()
    vehicle = load_vehicle()
    nose_offset = vehicle.chassis_center_x_m + vehicle.body_length_m / 2
    samples, states, paths, commands = [], [], [], []
    previous_time = None
    rclpy.init(args=[])
    node = Node('verify_acceleration')

    def _odom_cb(msg):
        nonlocal previous_time
        p = msg.pose.pose.position
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec / 1e9
        speed = 0.
        if previous_time is not None and stamp > previous_time:
            speed = math.hypot(p.x - samples[-1][0], p.y - samples[-1][1]) / (
                stamp - previous_time)
        previous_time = stamp
        samples.append((p.x, p.y, speed))

    def _status_cb(msg):
        if not states or states[-1] != msg.data:
            states.append(msg.data)

    node.create_subscription(Odometry, '/lhr/vehicle/odom', _odom_cb, 100)
    node.create_subscription(String, '/lhr/mission/status', _status_cb, 10)
    node.create_subscription(NavPath, '/lhr/track/centerline',
                             lambda msg: paths.append(len(msg.poses)), 10)
    node.create_subscription(AckermannDriveStamped, '/lhr/vehicle/cmd',
                             lambda msg: commands.append(msg.drive.speed), 10)
    log_path = Path(tempfile.mkdtemp(prefix='lhr-acceleration-')) / 'launch.log'
    process = None
    passed = False
    try:
        with log_path.open('w') as log:
            process = subprocess.Popen([
                'ros2', 'launch', 'lhr_gazebo', 'gazebo_demo.launch.py',
                'track_style:=acceleration', f'perception:={args.perception}',
                f'gui:={str(args.gui).lower()}', 'rviz:=false', 'enable_metrics:=false',
            ], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + args.timeout
        finished_at = None
        while time.monotonic() < deadline and process.poll() is None:
            rclpy.spin_once(node, timeout_sec=0.1)
            if states and states[-1] == 'FINISHED':
                if finished_at is None:
                    finished_at = time.monotonic()
                if time.monotonic() - finished_at > 2:
                    break
        assert samples, 'No Gazebo odometry received'
        assert 'DRIVING' in states and states[-1] == 'FINISHED', f'Mission states: {states}'
        stop_nose_x = samples[-1][0] + nose_offset
        assert 75 < stop_nose_x < 150, f'Stop outside exit lane: {stop_nose_x}'
        assert max(abs(y) for _, y, _ in samples) < 1., 'Vehicle left central corridor'
        assert samples[-1][2] < 0.1, f'Vehicle still moving: {samples[-1]}'
        assert max(v for _, _, v in samples) > 3., 'Vehicle did not accelerate'
        assert max(paths, default=0) > 10, 'No usable detected-cone path'
        assert len(commands) > 20 and max(commands[-10:]) == 0., 'Commands did not stop'
        passed = True
        print(json.dumps({
            'perception': args.perception, 'states': states,
            'max_speed_mps': max(v for _, _, v in samples),
            'max_lateral_error_m': max(abs(y) for _, y, _ in samples),
            'stop_nose_x_m': stop_nose_x, 'stop_after_finish_m': stop_nose_x - 75,
            'final_speed_mps': samples[-1][2], 'odom_samples': len(samples),
            'max_path_points': max(paths), 'log': str(log_path),
        }, indent=2))
    finally:
        if process is not None and not (passed and args.keep_running):
            os.killpg(process.pid, signal.SIGINT)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        node.destroy_node()
        rclpy.shutdown()
        if not passed:
            print('\n'.join(log_path.read_text().splitlines()[-50:]))


if __name__ == '__main__':
    main()
