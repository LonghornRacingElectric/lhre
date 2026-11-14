#!/usr/bin/env python3
"""Record a bounded headless reproduction of the wheel-free seed-3 EKF run."""

import argparse
import copy
import csv
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET

from ament_index_python.packages import get_package_share_directory
from gz.msgs10.contacts_pb2 import Contacts
from gz.msgs10.imu_pb2 import IMU
from gz.msgs10.odometry_pb2 import Odometry
from gz.transport13 import Node as GzNode
from lhr_vehicle import load_vehicle, vehicle_sha256
import rclpy
from record_imu_reference import stop
from std_msgs.msg import Bool


def main():
    gnss = load_vehicle().gnss
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out', type=Path)
    parser.add_argument('--noise', choices=['true', 'false'], default='true')
    parser.add_argument('--stop-sim-time', type=float, default=30.0)
    parser.add_argument('--high-rate-probe', action='store_true',
                        help='add a noiseless 1 kHz IMU and separate 3D truth odometry')
    parser.add_argument('--emergency-at', type=float,
                        help='request a controlled mission stop at this simulation time')
    parser.add_argument('--estimator', choices=['ekf', 'truth'], default='ekf')
    parser.add_argument('--gnss', action='store_true', help='fuse simulated periodic NavSatFix')
    parser.add_argument('--gnss-rate-hz', type=float, default=gnss.update_rate_hz)
    parser.add_argument('--gnss-stddev-m', type=float, default=gnss.horizontal_stddev_m)
    parser.add_argument('--gnss-latency-s', type=float, default=gnss.latency_s)
    parser.add_argument('--calibrate-gyro', action='store_true')
    parser.add_argument('--stationary-updates', action='store_true')
    parser.add_argument('--gnss-heading-gain', type=float, default=1.)
    parser.add_argument('--perception-study', action='store_true',
                        help='record raw clouds/maps/paths and passive physics contact probes')
    parser.add_argument('--stationary', action='store_true',
                        help='disable mission auto-go; verify physical stationarity offline')
    args = parser.parse_args()
    if args.perception_study:
        args.high_rate_probe = True
    args.out.mkdir(parents=True, exist_ok=False)
    latest = 0.0
    rows = []
    lock = threading.Lock()
    native = GzNode()
    probe_rows, odom_rows = [], []
    contacts = []
    contact_audit = {'messages': 0, 'contacts': 0, 'examples': []}

    def imu(msg):
        nonlocal latest
        stamp = msg.header.stamp.sec + msg.header.stamp.nsec * 1e-9
        g, a = msg.angular_velocity, msg.linear_acceleration
        with lock:
            latest = stamp
            rows.append((stamp, g.x, g.y, g.z, a.x, a.y, a.z))

    native.subscribe(IMU, '/imu/data', imu)
    if args.high_rate_probe:
        pkg = Path(get_package_share_directory('lhr_gazebo'))
        model = ET.parse(pkg / 'models/fsae_vehicle/model.sdf')
        original = model.find(".//sensor[@type='imu']")
        if args.noise == 'false':
            for noise in original.findall('.//noise'):
                for field in noise:
                    field.text = '0'
        probe = copy.deepcopy(original)
        probe.set('name', 'diagnostic_imu_1000')
        probe.find('topic').text = '/diagnostic/imu_1000'
        probe.find('update_rate').text = '1000'
        for axis in probe.findall('.//noise/..'):
            for noise in axis.findall('noise'):
                axis.remove(noise)
        for link in model.findall('.//link'):
            if original in list(link):
                link.append(probe)
                break
        plugin = ET.SubElement(model.find('model'), 'plugin', {
            'filename': 'gz-sim-odometry-publisher-system',
            'name': 'gz::sim::systems::OdometryPublisher'})
        for name, value in [('odom_topic', '/diagnostic/odom3d'),
                            ('tf_topic', '/diagnostic/tf3d'), ('dimensions', '3'),
                            ('odom_publish_frequency', '1000')]:
            ET.SubElement(plugin, name).text = value
        if args.perception_study:
            for link in model.findall('./model/link'):
                collisions = link.findall('collision')
                if not collisions:
                    continue
                sensor = ET.SubElement(link, 'sensor', {
                    'name': 'diagnostic_contact', 'type': 'contact'})
                ET.SubElement(sensor, 'update_rate').text = '1000'
                topic = f'/diagnostic/contact/{link.attrib["name"]}'
                ET.SubElement(sensor, 'topic').text = topic
                contact = ET.SubElement(sensor, 'contact')
                # Harmonic's Contact system reads the nested contact topic.
                ET.SubElement(contact, 'topic').text = topic
                for collision in collisions:
                    ET.SubElement(contact, 'collision').text = collision.attrib['name']
        model_path = args.out.resolve() / 'probe-vehicle.sdf'
        model.write(model_path, encoding='unicode')
        world = ET.parse(pkg / 'worlds/autocross_seed3.sdf')
        for include in world.findall('.//include'):
            if include.findtext('uri') == 'model://fsae_vehicle':
                include.find('uri').text = str(model_path)
        world_path = args.out.resolve() / 'probe-world.sdf'
        if args.perception_study:
            ET.SubElement(world.find('world'), 'plugin', {
                'filename': 'gz-sim-contact-system', 'name': 'gz::sim::systems::Contact'})
        world.write(world_path, encoding='unicode')

        def probe_imu(msg):
            stamp = msg.header.stamp.sec + msg.header.stamp.nsec*1e-9
            g, a, q = msg.angular_velocity, msg.linear_acceleration, msg.orientation
            with lock:
                probe_rows.append((stamp, g.x, g.y, g.z, a.x, a.y, a.z, q.x, q.y, q.z, q.w))

        def probe_odom(msg):
            stamp = msg.header.stamp.sec + msg.header.stamp.nsec*1e-9
            p, q, v = msg.pose.position, msg.pose.orientation, msg.twist.linear
            w = msg.twist.angular
            with lock:
                odom_rows.append((stamp, p.x, p.y, p.z, q.x, q.y, q.z, q.w,
                                  v.x, v.y, v.z, w.x, w.y, w.z))

        native.subscribe(IMU, '/diagnostic/imu_1000', probe_imu)
        native.subscribe(Odometry, '/diagnostic/odom3d', probe_odom)
        if args.perception_study:
            def contact_message(msg):
                with lock:
                    contact_audit['messages'] += 1
                    contact_audit['contacts'] += len(msg.contact)
                for contact in msg.contact:
                    pair = (contact.collision1.name, contact.collision2.name)
                    with lock:
                        if len(contact_audit['examples']) < 5:
                            contact_audit['examples'].append(
                                [contact.collision1.id, pair[0], contact.collision2.id, pair[1]])
                    if any('cone_' in name for name in pair):
                        stamp = msg.header.stamp.sec + msg.header.stamp.nsec*1e-9
                        with lock:
                            contacts.append((stamp, *pair))

            for link in model.findall('./model/link'):
                if link.find("sensor[@type='contact']") is not None:
                    native.subscribe(Contacts, f'/diagnostic/contact/{link.attrib["name"]}',
                                     contact_message)
    launch_args = [
        'ros2', 'launch', 'lhr_gazebo', 'gazebo_demo.launch.py',
        'gui:=false', 'rviz:=false', 'foxglove:=false',
        'track_style:=autocross', 'seed:=3', 'perception:=lidar', f'estimator:={args.estimator}',
        f'imu_noise:={args.noise}', 'use_wheel_speeds:=false',
        'safety:=true', 'safety_lidar_timeout_s:=1.5',
        f'output_csv:={args.out.resolve() / "metrics.csv"}',
    ]
    if args.high_rate_probe:
        launch_args.append(f'world:={world_path}')
    if args.gnss:
        launch_args += ['use_gnss:=true', f'gnss_rate_hz:={args.gnss_rate_hz}',
                        f'gnss_horizontal_stddev_m:={args.gnss_stddev_m}',
                        f'gnss_latency_s:={args.gnss_latency_s}']
    launch_args += [f'use_stationary_updates:={str(args.stationary_updates).lower()}',
                    f'calibrate_gyro:={str(args.calibrate_gyro).lower()}',
                    f'gnss_heading_gain:={args.gnss_heading_gain}']
    if args.stationary:
        launch_args.append('auto_go:=false')
    topics = ['/clock', '/lhr/imu/data', '/lhr/vehicle/odom', '/lhr/vehicle/odom_truth',
              '/lhr/vehicle/cmd', '/lhr/mission/status', '/lhr/safety/status']
    topics += ['/tf', '/tf_static', '/lhr/state_estimation/stationary_status']
    if args.perception_study:
        topics += ['/lhr/lidar/points', '/lhr/sensor/cones_detected',
                   '/lhr/track/centerline', '/lhr/control/lookahead']
    if args.gnss:
        topics += ['/lhr/gnss/fix', '/lhr/state_estimation/gnss_status']
    if args.calibrate_gyro:
        topics += ['/lhr/state_estimation/gyro_calibration']
    metadata = {'launch_args': launch_args, 'stop_sim_time': args.stop_sim_time,
                'vehicle_sha256': vehicle_sha256(),
                'ROS_DOMAIN_ID': os.environ.get('ROS_DOMAIN_ID'),
                'GZ_PARTITION': os.environ.get('GZ_PARTITION')}
    metadata['high_rate_probe'] = args.high_rate_probe
    metadata['imu_config'] = asdict(load_vehicle().imu)
    metadata['emergency_at_s'] = args.emergency_at
    metadata['perception_study'] = args.perception_study
    metadata['use_gnss'] = args.gnss
    metadata['use_stationary_updates'] = args.stationary_updates
    metadata['calibrate_gyro'] = args.calibrate_gyro
    metadata['gnss_heading_gain'] = args.gnss_heading_gain
    metadata['stationary_requested'] = args.stationary
    metadata['gnss_profile'] = {'rate_hz': args.gnss_rate_hz, 'sigma_m': args.gnss_stddev_m,
                                'latency_s': args.gnss_latency_s}
    (args.out / 'args.json').write_text(json.dumps(metadata, indent=2) + '\n')
    emergency_node = None
    if args.emergency_at is not None:
        rclpy.init()
        emergency_node = rclpy.create_node('diagnostic_stop_request')
        emergency_pub = emergency_node.create_publisher(Bool, '/lhr/mission/emergency', 10)
    emergency_sent = False
    params_dumped = False
    with (args.out / 'bag.log').open('w') as bag_log, (args.out / 'launch.log').open('w') as log:
        recorder = subprocess.Popen(
            ['ros2', 'bag', 'record', '-s', 'mcap', '-o', str(args.out / 'bag'),
             '--storage-preset-profile', 'zstd_fast', '--topics', *topics],
            stdout=bag_log, stderr=subprocess.STDOUT,
            start_new_session=True)
        launch = None
        try:
            launch = subprocess.Popen(launch_args, stdout=log, stderr=subprocess.STDOUT,
                                      start_new_session=True)
            deadline = time.monotonic() + 180
            while latest < args.stop_sim_time:
                if shutil.disk_usage(args.out).free < 150*1024**2:
                    raise RuntimeError('Capture stopped with less than 150 MiB disk headroom')
                if args.perception_study and latest >= 3 and not params_dumped:
                    names = ['lidar_cone_detector', 'track_builder', 'pure_pursuit',
                             'mission_manager']
                    if args.estimator == 'ekf':
                        names += ['ekf_node']
                    if args.gnss:
                        names += ['gnss_sim']
                    for name in names:
                        result = subprocess.run(['ros2', 'param', 'dump', '--no-daemon',
                                                 '--spin-time', '2', '--timeout', '5', f'/{name}'],
                                                capture_output=True, text=True, timeout=15)
                        if result.returncode != 0:
                            raise RuntimeError(
                                f'Could not record {name} parameters: {result.stderr}')
                        (args.out / f'{name}-params.yaml').write_text(result.stdout)
                    params_dumped = True
                if (args.emergency_at is not None and latest >= args.emergency_at
                        and not emergency_sent):
                    emergency_pub.publish(Bool(data=True))
                    emergency_sent = True
                if launch.poll() is not None:
                    raise RuntimeError('Launch exited; inspect launch.log')
                if time.monotonic() >= deadline:
                    raise RuntimeError('Bounded capture timed out; inspect launch.log')
                time.sleep(0.1)
        finally:
            if launch is not None:
                stop(launch)
            stop(recorder)
            native.unsubscribe('/imu/data')
            with (args.out / 'native-imu.csv').open('w') as stream:
                writer = csv.writer(stream, lineterminator='\n')
                writer.writerow(['stamp_s', 'gx', 'gy', 'gz', 'ax', 'ay', 'az'])
                with lock:
                    writer.writerows(rows)
            if args.high_rate_probe:
                native.unsubscribe('/diagnostic/imu_1000')
                native.unsubscribe('/diagnostic/odom3d')
                imu_header = [
                    'stamp_s', 'gx', 'gy', 'gz', 'ax', 'ay', 'az', 'qx', 'qy', 'qz', 'qw']
                odom_header = ['stamp_s', 'x', 'y', 'z', 'qx', 'qy', 'qz', 'qw',
                               'vx', 'vy', 'vz', 'wx', 'wy', 'wz']
                for filename, header, samples in [
                    ('probe-imu.csv', imu_header, probe_rows),
                    ('probe-odom3d.csv', odom_header, odom_rows),
                ]:
                    with (args.out / filename).open('w') as stream:
                        writer = csv.writer(stream, lineterminator='\n')
                        writer.writerow(header)
                        with lock:
                            writer.writerows(samples)
            if emergency_node is not None:
                emergency_node.destroy_node()
                rclpy.shutdown()
            if args.perception_study:
                (args.out / 'cone-contacts.json').write_text(json.dumps(contacts) + '\n')
                (args.out / 'contact-audit.json').write_text(json.dumps(contact_audit) + '\n')
    print(f'Stop diagnosis recorded: {args.out}', flush=True)


if __name__ == '__main__':
    main()
