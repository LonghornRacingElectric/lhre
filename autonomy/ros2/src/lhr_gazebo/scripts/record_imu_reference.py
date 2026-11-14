#!/usr/bin/env python3
"""Capture physics IMU inputs and a separate truth reference for offline experiments."""

import argparse
import csv
from dataclasses import asdict
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import xml.etree.ElementTree as ET

from ament_index_python.packages import get_package_share_directory
from lhr_vehicle import load_vehicle
import rclpy
from rclpy.node import Node
from std_msgs.msg import String


def stop(proc):
    """Stop only the process group owned by this capture."""
    try:
        os.killpg(proc.pid, signal.SIGINT)
        proc.wait(timeout=22)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    except ProcessLookupError:
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out', type=Path)
    parser.add_argument('--style', choices=['acceleration', 'autocross', 'oval'], required=True)
    parser.add_argument('--seed', type=int, default=2)
    parser.add_argument('--noise', choices=['true', 'false'], required=True)
    parser.add_argument('--full-rendering', action='store_true',
                        help='cross-check using the unmodified installed world and vehicle')
    parser.add_argument('--capture-native', action='store_true',
                        help='also record Gazebo IMU messages before the ROS bridge')
    parser.add_argument('--imu-rate', type=float,
                        help='diagnostic sensor rate override in the private model only')
    drive = parser.add_mutually_exclusive_group()
    drive.add_argument('--force-drive', action='store_true',
                       help='explicitly use the configured wheel force PID (now the default)')
    drive.add_argument('--direct-drive', action='store_true',
                       help='diagnostic reproduction of the old direct joint velocity mode')
    parser.add_argument('--stop-sim-time', type=float,
                        help='stop an input-path diagnostic at this IMU simulation timestamp')
    parser.add_argument('--post-terminal-sim-sec', type=float, default=0.0,
                        help='record this much simulation time after FINISHED/EMERGENCY')
    args = parser.parse_args()
    if args.full_rendering and (args.imu_rate is not None
                                or args.force_drive or args.direct_drive):
        parser.error('private model overrides cannot be used with --full-rendering')
    if args.imu_rate is not None and args.imu_rate <= 0:
        parser.error('--imu-rate must be positive')
    args.out.mkdir(parents=True, exist_ok=False)
    args.imu_config = asdict(load_vehicle().imu)
    pkg = Path(get_package_share_directory('lhr_gazebo'))
    world = ET.parse(pkg / 'worlds' / f'{args.style}_seed{args.seed}.sdf')
    model = ET.parse(pkg / 'models/fsae_vehicle/model.sdf')
    # No point clouds are consumed in this controlled sensor capture. Removing
    # rendering avoids GPU contention without changing vehicle/IMU physics.
    for link in model.findall('./model/link'):
        for sensor in link.findall("sensor[@type='gpu_lidar']"):
            link.remove(sensor)
    if args.noise == 'false':
        for noise in model.findall(".//sensor[@type='imu']/imu//noise"):
            for field in noise:
                field.text = '0'
    if args.imu_rate is not None:
        model.find(".//sensor[@type='imu']/update_rate").text = str(args.imu_rate)
    if args.force_drive or args.direct_drive:
        config = load_vehicle().gazebo_drive
        for plugin in model.findall(".//plugin[@name='gz::sim::systems::JointController']"):
            for field, value in [('use_force_commands', str(not args.direct_drive).lower()),
                                 ('p_gain', str(config.velocity_p_gain_nm_s_rad)),
                                 ('cmd_max', str(config.max_wheel_torque_nm)),
                                 ('cmd_min', str(-config.max_wheel_torque_nm))]:
                element = plugin.find(field)
                if element is None:
                    element = ET.SubElement(plugin, field)
                element.text = value
    controllers = model.findall(".//plugin[@name='gz::sim::systems::JointController']")
    force_mode = all(p.findtext('use_force_commands') == 'true' for p in controllers)
    args.drive_mode = 'force' if force_mode else 'direct'
    model_path = args.out.resolve() / 'vehicle.sdf'
    model.write(model_path, encoding='unicode')
    found_vehicle = False
    for include in world.findall('.//include'):
        if include.findtext('uri') == 'model://fsae_vehicle':
            include.find('uri').text = str(model_path)
            found_vehicle = True
    if not found_vehicle:
        raise ValueError('Reference world has no fsae_vehicle include')
    scene = world.find('world')
    for plugin in scene.findall('plugin'):
        if plugin.attrib.get('name') == 'gz::sim::systems::Sensors':
            scene.remove(plugin)
    world_path = args.out.resolve() / 'world.sdf'
    world.write(world_path, encoding='unicode')
    if args.full_rendering:
        # The launch applies its normal native-noise overlay to the installed
        # model. The private no-rendering copies above are not loaded.
        world_path = pkg / 'worlds' / f'{args.style}_seed{args.seed}.sdf'

    topics = ['/clock', '/lhr/imu/data', '/lhr/vehicle/odom',
              '/lhr/mission/status', '/lhr/vehicle/cmd']
    launch_args = ['ros2', 'launch', 'lhr_gazebo', 'gazebo_demo.launch.py',
                   'gui:=false', 'rviz:=false', 'perception:=sim', 'estimator:=truth',
                   'enable_metrics:=false', 'safety:=false', 'ready_hold_sec:=10.0',
                   'use_wheel_speeds:=false', f'imu_noise:={args.noise}',
                   f'track_style:={args.style}', f'seed:={args.seed}', f'world:={world_path}']
    (args.out / 'args.json').write_text(json.dumps(launch_args, indent=2) + '\n')
    rclpy.init()
    node = Node('imu_reference_capture')
    terminal = None
    terminal_sim = None
    latest_imu_time = 0.0
    native_rows = []
    native_lock = threading.Lock()
    native_node = None
    if args.capture_native or args.stop_sim_time is not None or args.post_terminal_sim_sec > 0:
        from gz.msgs10.imu_pb2 import IMU
        from gz.transport13 import Node as GzNode

        native_node = GzNode()

        def native_imu(msg):
            nonlocal latest_imu_time
            stamp = msg.header.stamp.sec + msg.header.stamp.nsec * 1e-9
            g, a = msg.angular_velocity, msg.linear_acceleration
            with native_lock:
                latest_imu_time = stamp
                native_rows.append((stamp, g.x, g.y, g.z, a.x, a.y, a.z))

        native_node.subscribe(IMU, '/imu/data', native_imu)

    def state(msg):
        nonlocal terminal, terminal_sim
        if msg.data in ('FINISHED', 'EMERGENCY') and terminal is None:
            terminal = time.monotonic()
            terminal_sim = latest_imu_time

    node.create_subscription(String, '/lhr/mission/status', state, 10)
    with (args.out / 'bag.log').open('w') as bag_log, (args.out / 'launch.log').open('w') as log:
        recorder = subprocess.Popen(
            ['ros2', 'bag', 'record', '-s', 'mcap', '-o', str(args.out / 'bag'),
             '--topics', *topics], stdout=bag_log, stderr=subprocess.STDOUT,
            start_new_session=True)
        launch = None
        try:
            launch = subprocess.Popen(launch_args, stdout=log, stderr=subprocess.STDOUT,
                                      start_new_session=True)
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                rclpy.spin_once(node, timeout_sec=0.1)
                if terminal is not None:
                    if args.post_terminal_sim_sec > 0:
                        if latest_imu_time >= terminal_sim + args.post_terminal_sim_sec:
                            break
                    elif time.monotonic() - terminal > 2:
                        break
                if args.stop_sim_time is not None and latest_imu_time >= args.stop_sim_time:
                    break
                if launch.poll() is not None:
                    raise RuntimeError('Reference launch exited; inspect launch.log')
            else:
                raise RuntimeError('Reference capture timed out; inspect launch.log')
        finally:
            if launch is not None:
                stop(launch)
            stop(recorder)
            if native_node is not None:
                native_node.unsubscribe('/imu/data')
                with (args.out / 'native-imu.csv').open('w') as stream:
                    writer = csv.writer(stream, lineterminator='\n')
                    writer.writerow(['stamp_s', 'gx', 'gy', 'gz', 'ax', 'ay', 'az'])
                    with native_lock:
                        writer.writerows(native_rows)
            (args.out / 'capture-options.json').write_text(
                json.dumps(vars(args), default=str, indent=2) + '\n')
            node.destroy_node()
            rclpy.shutdown()
    print(f'Reference capture complete: {args.out}', flush=True)


if __name__ == '__main__':
    main()
