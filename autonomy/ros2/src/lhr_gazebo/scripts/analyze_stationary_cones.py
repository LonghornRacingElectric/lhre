#!/usr/bin/env python3
"""Audit stationary LiDAR geometry versus pose registration and persistent mapping."""

import argparse
from collections import Counter, defaultdict
import csv
import json
import math
from pathlib import Path
from types import SimpleNamespace

from analyze_cone_path import local, odometry, planar, pose_at, pose_row, read_bag, stamp, to_world
from lhr_metrics.reference import world_geometry
import lhr_perception.lidar_cone_detector as detector_module
from lhr_perception.lidar_cone_detector import LidarConeDetector, transform_points
import numpy as np
import rclpy
from rclpy.serialization import deserialize_message
from rosbag2_py import ConverterOptions, SequentialReader, StorageOptions
from rosidl_runtime_py.utilities import get_message
from sensor_msgs_py import point_cloud2


def distribution(values):
    """Summarize finite scalar observations, preserving an empty set as unknown."""
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    return ({'n': len(values), 'median': float(np.median(values)),
             'p95': float(np.percentile(values, 95)), 'max': float(values.max())}
            if len(values) else None)


def geometry(points, targets, radius=.35):
    """Report nearest geometry errors and excess landmarks assigned to one target."""
    points, targets = np.asarray(points).reshape(-1, 2), np.asarray(targets).reshape(-1, 2)
    if not len(points) or not len(targets):
        return {'count': len(points), 'p95_m': None, 'unmatched': len(points), 'duplicates': 0}
    distances = np.linalg.norm(points[:, None, :]-targets, axis=2)
    nearest = distances.argmin(axis=1)
    error = distances[np.arange(len(points)), nearest]
    accepted = error <= radius
    counts = Counter(nearest[accepted])
    return {'count': len(points), 'p95_m': float(np.percentile(error, 95)),
            'unmatched': int((~accepted).sum()),
            'duplicates': sum(max(0, n-1) for n in counts.values())}


def jitter(tracks):
    """Measure each landmark's scatter around its own temporal median, not truth."""
    spreads = []
    for points in tracks.values():
        if len(points) >= 10:
            points = np.asarray(points)
            spreads.extend(np.linalg.norm(points-np.median(points, axis=0), axis=1))
    return {'landmarks_with_10_samples': sum(len(p) >= 10 for p in tracks.values()),
            'radial_scatter_m': distribution(spreads)}


def audit_motion(capture, start, end):
    """Check the independent physics reference, command stream and mission state."""
    truth = np.loadtxt(capture / 'probe-odom3d.csv', delimiter=',', skiprows=1)
    rows = truth[(truth[:, 0] >= start) & (truth[:, 0] <= end)]
    if len(rows) < 2:
        raise ValueError('Insufficient physics reference in requested stationary window')
    reference = rows[0, 1:8]
    yaw = np.unwrap([planar(p[1:8])[2] for p in rows])
    reader = SequentialReader()
    reader.open(StorageOptions(uri=str(capture / 'bag'), storage_id='mcap'),
                ConverterOptions('', ''))
    types = {t.name: get_message(t.type) for t in reader.get_all_topics_and_types()}
    commands, states = [], Counter()
    while reader.has_next():
        topic, raw, _ = reader.read_next()
        if topic == '/lhr/vehicle/cmd':
            msg = deserialize_message(raw, types[topic])
            if start <= stamp(msg) <= end:
                commands.append(abs(msg.drive.speed))
        elif topic == '/lhr/mission/status':
            states[deserialize_message(raw, types[topic]).data] += 1
    displacement = np.linalg.norm(rows[:, 1:4]-reference[:3], axis=1)
    return truth[:, :8], {
        'physics_samples': len(rows),
        'physics_max_translation_from_window_start_m': float(displacement.max()),
        'physics_max_heading_change_deg': float(np.degrees(abs(yaw-yaw[0])).max()),
        'physics_max_linear_speed_m_s': float(np.linalg.norm(rows[:, 8:11], axis=1).max()),
        'command_samples': len(commands),
        'max_abs_commanded_speed_m_s': max(commands, default=None),
        'mission_states_whole_capture': dict(states),
        'stationary_verified': bool(displacement.max() < .02 and np.ptp(yaw) < math.radians(.2)
                                    and len(commands) and max(commands) < 1e-6
                                    and 'DRIVING' not in states),
    }


def analyze(capture, out, start=2., end=20.):
    """Replay identical recorded scans with estimated, physics and fixed registration."""
    out.mkdir(parents=True, exist_ok=False)
    truth, motion = audit_motion(capture, start, end)
    data = read_bag(capture)
    estimated = np.asarray([pose_row(msg) for msg in data['odom']])
    if len(estimated) < 2:
        raise ValueError('No usable estimated odometry')
    fixed = pose_at(truth, start)
    if fixed is None:
        raise ValueError('Physics reference does not cover the stationary window')
    _, _, cones = world_geometry(Path(__file__).parents[1] / 'worlds/autocross_seed3.sdf')
    targets = np.asarray([c[1:3] for c in cones])
    rclpy.init(args=['--ros-args', '--params-file',
                     str(capture / 'lidar_cone_detector-params.yaml')])
    tags = ('base', 'estimated', 'physics', 'fixed', 'instant')
    nodes = {tag: LidarConeDetector() for tag in tags}
    original = detector_module.update_cone_map
    fresh, active = {}, ['']

    def observe(detections, cone_map, radius):
        fresh[active[0]] = np.asarray(detections).reshape(-1, 2)
        return original(detections, cone_map, radius)

    detector_module.update_cone_map = observe
    for node in nodes.values():
        node._det_pub = SimpleNamespace(publish=lambda _: None)
        node._debug_pub = SimpleNamespace(publish=lambda _: None)
    base_pose = np.array([0., 0., 0., 0., 0., 0., 1.])
    tracks = {tag: defaultdict(list) for tag in ('raw_roi', 'base', 'registered')}
    births = {tag: {} for tag in tags}
    frames, snapshots, pose_rows = [], [], []
    try:
        for cloud in data['clouds']:
            time = stamp(cloud)
            pe, pt = pose_at(estimated, time), pose_at(truth, time)
            if pe is None or pt is None or time > end:
                continue
            # Include startup scans so persistence matches a detector started at launch.
            poses = {'base': base_pose, 'estimated': pe, 'physics': pt,
                     'fixed': fixed, 'instant': pe}
            fresh.clear()
            starts = {}
            for tag, node in nodes.items():
                if tag in ('base', 'instant'):
                    node._cone_map.clear()
                    node._scan_history.clear()
                    births[tag].clear()
                starts[tag] = len(node._cone_map)
                active[0] = tag
                node._odom_cb(odometry(poses[tag], time))
                node._cloud_cb(cloud)
                node._process()
            if not all(tag in fresh for tag in tags):
                continue
            for tag in tags:
                for index in range(starts[tag], len(nodes[tag]._cone_map)):
                    # Recover the observation's physical identity at birth;
                    # later displaced replicas must not masquerade as new cones.
                    observed = nodes[tag]._cone_map[index][:2]
                    sensor_point = local([observed], poses[tag])
                    physical = to_world(sensor_point, pt)[0]
                    distances = np.linalg.norm(targets-physical, axis=1)
                    target = int(distances.argmin())
                    if distances[target] <= .35:
                        births[tag][index] = target
            if time < start:
                continue
            actual_local = local(targets, pt)
            pose_rows.append([time, *pe[:2], planar(pe)[2], *pt[:2], planar(pt)[2]])
            row = {'stamp_s': time, 'pose_error_m': float(np.linalg.norm(pe[:2]-pt[:2])),
                   'heading_error_deg': float(math.degrees(abs(
                       (planar(pe)[2]-planar(pt)[2]+math.pi) % (2*math.pi)-math.pi)))}
            for tag in ('estimated', 'physics', 'fixed', 'instant'):
                mapped = np.asarray(nodes[tag]._cone_map).reshape(-1, 3)[:, :2]
                metrics = geometry(local(mapped, poses[tag]), actual_local)
                row.update({f'{tag}_{k}': v for k, v in metrics.items()})
                counts = Counter(births[tag].values())
                row[f'{tag}_birth_identified'] = len(births[tag])
                row[f'{tag}_birth_duplicate_excess'] = sum(n-1 for n in counts.values())
            # Associate sensor-local centroids to known cones only for offline scoring.
            # Neither the live detector nor the EKF receives these identities.
            base = fresh['base']
            registered = fresh['estimated']
            world = to_world(base, pt)
            for index, point in enumerate(world):
                distances = np.linalg.norm(targets-point, axis=1)
                target = int(distances.argmin())
                if distances[target] > .35:
                    continue
                tracks['base'][target].append(base[index])
                if len(registered):
                    predicted = to_world([base[index]], pe)[0]
                    k = int(np.linalg.norm(registered-predicted, axis=1).argmin())
                    if np.linalg.norm(registered[k]-predicted) < 1e-5:
                        tracks['registered'][target].append(registered[k])
            points = point_cloud2.read_points_numpy(
                cloud, field_names=('x', 'y', 'z'), skip_nans=True).reshape(-1, 3)
            points = transform_points(points, nodes['base']._configured_mount.transform)
            points = points[np.isfinite(points).all(axis=1)]
            # Independent raw-return ROI, not a change to production height filtering.
            points = points[(points[:, 2] > .05) & (points[:, 2] < .55)]
            for target, point in enumerate(actual_local):
                roi = points[np.linalg.norm(points[:, :2]-point, axis=1) < .35]
                if len(roi) >= 3:
                    tracks['raw_roi'][target].append(roi[:, :2].mean(axis=0))
            frames.append(row)
            if len(frames) % 20 == 1:
                snapshots.append({
                    'stamp_s': time, 'base_detections': base.tolist(),
                    'estimated_detections': local(registered, pe).tolist(),
                    'true_local_cones': actual_local.tolist(),
                    'estimated_map_in_vehicle': local(
                        np.asarray(nodes['estimated']._cone_map)[:, :2], pe).tolist(),
                    'physics_map_in_vehicle': local(
                        np.asarray(nodes['physics']._cone_map)[:, :2], pt).tolist(),
                })
    finally:
        detector_module.update_cone_map = original
        for node in nodes.values():
            node.destroy_node()
        rclpy.shutdown()
    if not frames:
        raise ValueError('No replayed scans in requested window')
    live = []
    for markers in data['maps']:
        valid = [m for m in markers.markers if m.action == m.ADD]
        if not valid:
            continue
        time = stamp(valid[0])
        pe, pt = pose_at(estimated, time), pose_at(truth, time)
        if start <= time <= end and pe is not None and pt is not None:
            points = [[m.pose.position.x, m.pose.position.y] for m in valid]
            replay = min(frames, key=lambda row: abs(row['stamp_s']-time))
            live.append({'stamp_s': time, **geometry(local(points, pe), local(targets, pt)),
                         'nearest_replayed_stamp_s': replay['stamp_s']})
    pose_rows = np.asarray(pose_rows)
    imu = np.loadtxt(capture / 'native-imu.csv', delimiter=',', skiprows=1)
    imu = imu[(imu[:, 0] >= start) & (imu[:, 0] <= end)]
    summary = {
        'capture': str(capture), 'analysis_window_s': [start, end], 'frames': len(frames),
        'motion_audit': motion,
        'stationary_native_gyro_z': {
            'samples': len(imu), 'mean_rad_s': float(imu[:, 3].mean()),
            'mean_deg_s': float(np.degrees(imu[:, 3].mean())),
            'stddev_rad_s': float(imu[:, 3].std(ddof=1)),
            'mean_integrated_over_window_deg': float(np.degrees(
                imu[:, 3].mean()*(end-start))),
        },
        'pose_position_error_m': distribution([r['pose_error_m'] for r in frames]),
        'pose_heading_error_deg': distribution([r['heading_error_deg'] for r in frames]),
        'estimated_translation_from_first_scan_m': distribution(
            np.linalg.norm(pose_rows[:, 1:3]-pose_rows[0, 1:3], axis=1)),
        'sensor_and_fresh_detection_jitter': {tag: jitter(track) for tag, track in tracks.items()},
        'replay_geometry': {tag: {
            key: distribution([r[f'{tag}_{key}'] for r in frames])
            for key in ('count', 'p95_m', 'unmatched', 'duplicates',
                        'birth_identified', 'birth_duplicate_excess')}
            for tag in ('estimated', 'physics', 'fixed', 'instant')},
        'live_geometry': {key: distribution([r[key] for r in live])
                          for key in ('count', 'p95_m', 'unmatched', 'duplicates')},
        'final_replay_frame': frames[-1],
        'limits': 'Single seed-3 capture; identity/radius scoring is diagnostic. Raw ROI and '
                  'fresh-detection jitter use >=10 observations per cone. Scans before '
                  'the analysis window remain in persistent history. Physics/fixed poses '
                  'are offline controls only. Production perception/estimator are unchanged.',
    }
    (out / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    (out / 'snapshots.json').write_text(json.dumps(snapshots)+'\n')
    for name, rows in [('frames', frames), ('live', live)]:
        if rows:
            with (out / f'{name}.csv').open('w') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
                writer.writeheader()
                writer.writerows(rows)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('out', type=Path)
    parser.add_argument('--start-s', type=float, default=2.)
    parser.add_argument('--end-s', type=float, default=20.)
    args = parser.parse_args()
    if not 0 <= args.start_s < args.end_s:
        parser.error('require 0 <= start-s < end-s')
    print(json.dumps(analyze(args.capture, args.out, args.start_s, args.end_s), indent=2))


if __name__ == '__main__':
    main()
