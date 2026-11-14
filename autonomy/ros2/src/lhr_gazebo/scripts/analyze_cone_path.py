#!/usr/bin/env python3
"""Replay identical LiDAR clouds with estimated/reference poses and score local geometry."""

import argparse
import copy
import csv
import json
import math
from pathlib import Path
from types import SimpleNamespace

from lhr_metrics.course import CourseEnvelope
from lhr_metrics.reference import paired_centerline, ReferenceTrack, world_geometry
import lhr_perception.lidar_cone_detector as detector_module
from lhr_perception.lidar_cone_detector import LidarConeDetector
from lhr_track_builder.track_builder_node import TrackBuilder
from lhr_trackgen.cone_geometry import CONE_SPECS
from lhr_vehicle import load_vehicle
from nav_msgs.msg import Odometry
import numpy as np
import rclpy
from rclpy.serialization import deserialize_message
from rosbag2_py import ConverterOptions, SequentialReader, StorageOptions
from rosidl_runtime_py.utilities import get_message
from scipy.spatial.transform import Rotation
from visualization_msgs.msg import Marker, MarkerArray


def stamp(msg):
    return msg.header.stamp.sec + msg.header.stamp.nanosec*1e-9


def pose_row(msg):
    p, q = msg.pose.pose.position, msg.pose.pose.orientation
    return [stamp(msg), p.x, p.y, p.z, q.x, q.y, q.z, q.w]


def pose_at(rows, time):
    if not rows[0, 0] <= time <= rows[-1, 0]:
        return None
    k = min(max(int(np.searchsorted(rows[:, 0], time)), 1), len(rows)-1)
    a, b = rows[k-1], rows[k]
    fraction = (time-a[0])/(b[0]-a[0])
    q = b[4:] if a[4:] @ b[4:] >= 0 else -b[4:]
    q = a[4:]*(1-fraction)+q*fraction
    q /= np.linalg.norm(q)
    return np.r_[a[1:4]*(1-fraction)+b[1:4]*fraction, q]


def odometry(pose, time):
    msg = Odometry()
    msg.header.stamp.sec = int(time)
    msg.header.stamp.nanosec = round((time-int(time))*1e9)
    msg.header.frame_id, msg.child_frame_id = 'map', 'base_link'
    p, q = msg.pose.pose.position, msg.pose.pose.orientation
    p.x, p.y, p.z = map(float, pose[:3])
    q.x, q.y, q.z, q.w = map(float, pose[3:])
    return msg


def planar(pose):
    q = pose[3:]
    yaw = math.atan2(2*(q[3]*q[2]+q[0]*q[1]), 1-2*(q[1]**2+q[2]**2))
    c, s = math.cos(yaw), math.sin(yaw)
    return pose[:2], np.array([[c, -s], [s, c]]), yaw


def local(points, pose):
    p, rotation, _ = planar(pose)
    return (np.asarray(points).reshape(-1, 2)-p) @ rotation


def to_world(points, pose):
    p, rotation, _ = planar(pose)
    return np.asarray(points).reshape(-1, 2) @ rotation.T+p


def nearest_distance(point, path):
    path = np.asarray(path).reshape(-1, 2)
    if len(path) < 2:
        return None
    a, delta = path[:-1], np.diff(path, axis=0)
    u = np.clip(np.sum((point-a)*delta, axis=1)/np.maximum(np.sum(delta**2, axis=1), 1e-12), 0, 1)
    return float(np.min(np.linalg.norm(point-(a+u[:, None]*delta), axis=1)))


def horizon_clearance(path, envelope, cone_xy, cone_radius):
    """Conservative body/wheel box versus cone circumscribed circles, first 8 m."""
    path = np.asarray(path).reshape(-1, 2)
    if len(path) < 2:
        return None
    best, distance = math.inf, 0.0
    for start, end in zip(path[:-1], path[1:]):
        length = float(np.linalg.norm(end-start))
        if length < 1e-6:
            continue
        yaw = math.atan2(*(end-start)[::-1])
        c, s = math.cos(yaw), math.sin(yaw)
        rotation = np.array([[c, -s], [s, c]])
        limit = min(length, 8-distance)
        for step in np.linspace(0, limit, max(2, math.ceil(limit/.1)+1)):
            position = start+(end-start)*step/length
            points = (cone_xy-position) @ rotation
            dx = np.maximum(np.maximum(envelope.rear-points[:, 0], points[:, 0]-envelope.front), 0)
            dy = np.maximum(abs(points[:, 1])-envelope.half_width, 0)
            best = min(best, float(np.min(np.hypot(dx, dy)-cone_radius)))
        distance += length
        if distance >= 8:
            break
    return best if math.isfinite(best) else None


def trim_path(path, point):
    path = np.asarray(path).reshape(-1, 2)
    if len(path) < 2:
        return path
    start, delta = path[:-1], np.diff(path, axis=0)
    fraction = np.clip(np.sum((point-start)*delta, axis=1)
                       / np.maximum(np.sum(delta**2, axis=1), 1e-12), 0, 1)
    projected = start+fraction[:, None]*delta
    index = int(np.argmin(np.linalg.norm(projected-point, axis=1)))
    return np.vstack([projected[index], path[index+1:]])


def read_bag(path):
    reader = SequentialReader()
    reader.open(StorageOptions(uri=str(path / 'bag'), storage_id='mcap'), ConverterOptions('', ''))
    types = {t.name: get_message(t.type) for t in reader.get_all_topics_and_types()}
    data = {'clouds': [], 'odom': [], 'maps': [], 'paths': []}
    while reader.has_next():
        topic, raw, _ = reader.read_next()
        if topic not in ('/lhr/lidar/points', '/lhr/vehicle/odom',
                         '/lhr/sensor/cones_detected', '/lhr/track/centerline'):
            continue
        msg = deserialize_message(raw, types[topic])
        key = {'/lhr/lidar/points': 'clouds', '/lhr/vehicle/odom': 'odom',
               '/lhr/sensor/cones_detected': 'maps', '/lhr/track/centerline': 'paths'}[topic]
        data[key].append(msg)
    return data


def analyze(capture, out):
    data = read_bag(capture)
    out.mkdir(parents=True, exist_ok=True)
    probe = np.loadtxt(capture / 'probe-odom3d.csv', delimiter=',', skiprows=1)
    truth = probe[:, :8]
    estimated = np.array([pose_row(msg) for msg in data['odom']])
    left, right, cones = world_geometry(Path(__file__).parents[1] / 'worlds/autocross_seed3.sdf')
    xy = np.asarray([c[1:3] for c in cones])
    radii = np.asarray([CONE_SPECS[c[4]].base_width_m/math.sqrt(2) for c in cones])
    envelope = CourseEnvelope(left, right, cones, load_vehicle(), True)
    reference = ReferenceTrack(paired_centerline(left, right))
    contact_path = capture / 'cone-contacts.json'
    contacts = json.loads(contact_path.read_text())
    audit = json.loads((capture / 'contact-audit.json').read_text()) if (
        capture / 'contact-audit.json').exists() else None
    first_contact = min((row[0] for row in contacts), default=None)
    first_envelope = None
    for row in truth[::20]:
        pose = row[1:]
        if envelope.pose_check(*pose[:2], planar(pose)[2])[1]:
            first_envelope = float(row[0])
            break
    # A conservative envelope is a risk marker, not a physics contact event.
    events = [v for v in [first_contact, first_envelope] if v is not None]
    cutoff = min(events)-.3 if events else 21.0
    rclpy.init(args=[
        '--ros-args', '--params-file', str(capture / 'lidar_cone_detector-params.yaml'),
        '--params-file', str(capture / 'track_builder-params.yaml')])
    nodes = {tag: LidarConeDetector() for tag in ('estimated', 'truth', 'rigid', 'instant')}
    builder = TrackBuilder()
    fresh, associations, active = {}, {}, ['']
    original_update = detector_module.update_cone_map

    def observe(detections, cone_map, radius):
        fresh[active[0]] = np.asarray(detections).reshape(-1, 2)
        before = np.asarray(cone_map).reshape(-1, 3).copy()
        result = original_update(detections, cone_map, radius)
        matches = []
        for index, old in enumerate(before):
            new = cone_map[index]
            if new[2] > old[2]:
                observed = np.asarray(new[:2])*new[2]-old[:2]*old[2]
                detection = int(np.argmin(np.linalg.norm(fresh[active[0]]-observed, axis=1)))
                matches.append((detection, index))
        associations[active[0]] = matches
        return result

    detector_module.update_cone_map = observe
    marker_cache = {}
    for tag, node in nodes.items():
        node._det_pub = SimpleNamespace(
            publish=lambda msg, tag=tag: marker_cache.update({tag: msg}))
        node._debug_pub = SimpleNamespace(publish=lambda _: None)
    identities = {tag: {} for tag in nodes}
    fixed_r = np.array([[0., -1.], [1., 0.]])
    fixed_p = np.array([3., -2.])
    frames, snapshots = [], []
    try:
        for cloud in data['clouds']:
            time = stamp(cloud)
            pe, pt = pose_at(estimated, time), pose_at(truth, time)
            if pe is None or pt is None:
                continue
            pr = pt.copy()
            pr[:2] = fixed_r @ pt[:2]+fixed_p
            pr[3:] = (Rotation.from_euler('z', math.pi/2)*Rotation.from_quat(pt[3:])).as_quat()
            poses = {'estimated': pe, 'truth': pt, 'rigid': pr, 'instant': pe}
            nodes['instant']._cone_map.clear()
            nodes['instant']._scan_history.clear()
            identities['instant'].clear()
            starts = {tag: len(node._cone_map) for tag, node in nodes.items()}
            for tag, node in nodes.items():
                active[0] = tag
                node._odom_cb(odometry(poses[tag], time))
                node._cloud_cb(cloud)
                node._process()
            if 'truth' not in fresh or not len(fresh['truth']):
                continue
            truth_distances = np.linalg.norm(fresh['truth'][:, None, :]-xy, axis=2)
            truth_indices = np.argmin(truth_distances, axis=1)
            row = {'stamp_s': time, 'pre_contact': time < cutoff,
                   'pose_error_m': float(np.linalg.norm(pe[:2]-pt[:2])),
                   'physical_centerline_cte_m': reference.project(*pt[:2])[0]}
            live_paths = [p for p in data['paths'] if stamp(p) <= time and p.poses]
            if live_paths:
                live = np.array([[p.pose.position.x, p.pose.position.y]
                                 for p in live_paths[-1].poses])
                row['live_car_to_displayed_path_m'] = nearest_distance(pe[:2], live)
                live = trim_path(live, pe[:2])
                row['live_commanded_path_clearance_m'] = horizon_clearance(
                    to_world(local(live, pe), pt), envelope, xy, radii)
            else:
                row['live_car_to_displayed_path_m'] = None
                row['live_commanded_path_clearance_m'] = None
            aligned = all(len(fresh[tag]) == len(fresh['truth']) for tag in nodes)
            row['fresh_lists_aligned'] = aligned
            frame_paths = {}
            for tag, node in nodes.items():
                mapped = np.asarray(node._cone_map).reshape(-1, 3)[:, :2]
                checked, wrong = 0, 0
                if aligned:
                    for detection, index in associations[tag]:
                        target = int(truth_indices[detection])
                        if (index in identities[tag]
                                and np.linalg.norm(fresh['truth'][detection]-xy[target]) < .35):
                            checked += 1
                            wrong += identities[tag][index] != target
                row[f'{tag}_checked_associations'] = checked
                row[f'{tag}_wrong_birth_identity_associations'] = wrong
                if aligned:
                    for index in range(starts[tag], len(mapped)):
                        nearest = int(np.argmin(np.linalg.norm(fresh[tag]-mapped[index], axis=1)))
                        target = int(truth_indices[nearest])
                        if np.linalg.norm(fresh['truth'][nearest]-xy[target]) < .35:
                            identities[tag][index] = target
                errors = []
                for index, target in identities[tag].items():
                    if np.linalg.norm(xy[target]-pt[:2]) > 20:
                        continue
                    errors.append(float(np.linalg.norm(local(mapped[index], poses[tag])[0]
                                                       - local(xy[target], pt)[0])))
                row[f'{tag}_map_count'] = len(mapped)
                row[f'{tag}_identified'] = len(errors)
                row[f'{tag}_identity_coverage'] = len(identities[tag])/max(len(mapped), 1)
                row[f'{tag}_local_map_error_median_m'] = (
                    float(np.median(errors)) if errors else None)
                row[f'{tag}_local_map_error_p95_m'] = (
                    float(np.percentile(errors, 95)) if errors else None)
                builder._odom_cb(odometry(poses[tag], time))
                builder._cones_cb(marker_cache[tag])
                path = np.asarray(builder._compute_midpoints()).reshape(-1, 2)
                path = trim_path(path, poses[tag][:2])
                physical_path = to_world(local(path, poses[tag]), pt)
                frame_paths[tag] = physical_path.tolist()
                row[f'{tag}_path_clearance_m'] = horizon_clearance(
                    physical_path, envelope, xy, radii)
                row[f'{tag}_car_to_displayed_path_m'] = nearest_distance(poses[tag][:2], path)
                if tag == 'truth':
                    observed = sorted(set(identities[tag].values()))
                    visible = np.flatnonzero(np.linalg.norm(xy-pt[:2], axis=1) < 20)
                    for label, subset, colored in [
                        ('oracle_observed', observed, False),
                        ('oracle_visible', visible, False),
                        ('oracle_colored', observed, True),
                    ]:
                        markers = []
                        for target in subset:
                            marker = Marker()
                            marker.id = int(target)
                            side = 'left_cones' if cones[target][4] == 'blue' else 'right_cones'
                            marker.ns = side if colored else 'cones'
                            marker.pose.position.x, marker.pose.position.y = map(float, xy[target])
                            markers.append(marker)
                        builder._cones_cb(MarkerArray(markers=markers))
                        ideal = np.asarray(builder._compute_midpoints()).reshape(-1, 2)
                        ideal = trim_path(ideal, pt[:2])
                        row[f'{label}_path_clearance_m'] = horizon_clearance(
                            ideal, envelope, xy, radii)
            if len(frames) % 10 == 0 or abs(time-20.5) < .001:
                snapshots.append({'stamp_s': time, 'true_pose': pt.tolist(),
                                  'estimated_pose': pe.tolist(),
                                  'estimated_map': nodes['estimated']._cone_map,
                                  'truth_map': nodes['truth']._cone_map,
                                  'instant_map': nodes['instant']._cone_map,
                                  'physical_paths': frame_paths})
                snapshots[-1] = copy.deepcopy(snapshots[-1])
            frames.append(row)
    finally:
        detector_module.update_cone_map = original_update
        for node in [*nodes.values(), builder]:
            node.destroy_node()
        rclpy.shutdown()
    with (out / 'frames.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(frames[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(frames)
    (out / 'snapshots.json').write_text(json.dumps(snapshots) + '\n')
    before = [r for r in frames if r['pre_contact'] and r['stamp_s'] > 7]
    summary = {'capture': str(capture), 'frames': len(frames), 'pre_contact_frames': len(before),
               'first_native_cone_contact_s': first_contact, 'contact_audit': audit,
               'first_conservative_cone_overlap_s': first_envelope, 'cutoff_s': cutoff}
    for key in frames[0]:
        if key.endswith(('_m', '_count')):
            values = [r[key] for r in before if r[key] is not None]
            if values:
                summary[key] = {'median': float(np.median(values)), 'max': float(max(values)),
                                'min': float(min(values))}
    summary['association_checks'] = {}
    for tag in nodes:
        checked = sum(r[f'{tag}_checked_associations'] for r in before)
        wrong = sum(r[f'{tag}_wrong_birth_identity_associations'] for r in before)
        summary['association_checks'][tag] = {
            'checked': checked, 'wrong_birth_identity': wrong,
            'wrong_fraction': wrong/max(checked, 1)}
    summary['aligned_fresh_fraction'] = (
        sum(r['fresh_lists_aligned'] for r in before)/max(len(before), 1))
    live_errors, agreement = [], []
    for message in data['maps']:
        markers = [m for m in message.markers if m.action == Marker.ADD]
        if not markers:
            continue
        time = stamp(markers[0])
        if not 7 < time < cutoff:
            continue
        pe, pt = pose_at(estimated, time), pose_at(truth, time)
        if pe is None or pt is None:
            continue
        points = np.array([[m.pose.position.x, m.pose.position.y] for m in markers])
        body = local(points, pe)
        body = body[np.linalg.norm(body, axis=1) < 20]
        if len(body):
            physical = to_world(body, pt)
            errors = np.min(np.linalg.norm(physical[:, None, :]-xy, axis=2), axis=1)
            live_errors.append(float(np.percentile(errors, 95)))
    for snapshot in snapshots:
        time = snapshot['stamp_s']
        if not 7 < time < cutoff:
            continue
        replayed = np.asarray(snapshot['estimated_map'])[:, :2]
        candidates = []
        for message in data['maps']:
            markers = [m for m in message.markers if m.action == Marker.ADD]
            if not markers or not time <= stamp(markers[0]) <= time+.3:
                continue
            actual = np.array([[m.pose.position.x, m.pose.position.y] for m in markers])
            distances = np.linalg.norm(replayed[:, None, :]-actual, axis=2)
            forward = np.min(distances, axis=0).mean()
            reverse = np.min(distances, axis=1).mean()
            candidates.append(float(.5*(forward+reverse)))
        if candidates:
            agreement.append(min(candidates))
    summary['live_map_nearest_geometry_p95_m'] = {
        'frames': len(live_errors), 'median': float(np.median(live_errors)),
        'max': float(max(live_errors))}
    summary['live_replay_map_agreement_m'] = {
        'snapshots': len(agreement), 'median': float(np.median(agreement)),
        'max': float(max(agreement))}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('out', type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.capture, args.out), indent=2))


if __name__ == '__main__':
    main()
