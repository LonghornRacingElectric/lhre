#!/usr/bin/env python3
"""Audit simulated fixes, actual fusion decisions and trajectory error from a bag."""

import argparse
from collections import Counter
import json
import math
from pathlib import Path

from analyze_cone_path import planar, pose_at, pose_row
from lhr_metrics.reference import paired_centerline, ReferenceTrack, world_geometry
from lhr_state_estimation.gnss import LocalENU
from lhr_vehicle import load_vehicle
import numpy as np
from rclpy.serialization import deserialize_message
from rosbag2_py import ConverterOptions, SequentialReader, StorageOptions
from rosidl_runtime_py.utilities import get_message
from scipy.spatial.transform import Rotation


def analyze(capture, out):
    reader = SequentialReader()
    reader.open(StorageOptions(uri=str(capture / 'bag'), storage_id='mcap'),
                ConverterOptions('', ''))
    types = {t.name: get_message(t.type) for t in reader.get_all_topics_and_types()}
    poses, fixes, decisions = [], [], []
    while reader.has_next():
        topic, raw, _ = reader.read_next()
        if topic not in ('/lhr/vehicle/odom', '/lhr/gnss/fix',
                         '/lhr/state_estimation/gnss_status'):
            continue
        msg = deserialize_message(raw, types[topic])
        if topic.endswith('gnss_status'):
            decisions.append(json.loads(msg.data))
        elif topic.endswith('/fix'):
            fixes.append(msg)
        else:
            poses.append(pose_row(msg))
    truth = np.loadtxt(capture / 'probe-odom3d.csv', delimiter=',', skiprows=1)[:, :8]
    origin = LocalENU(0, 0, 0)  # The recorded experiment's declared synthetic datum.
    lever = np.asarray(load_vehicle().gnss.position_m)
    errors, yaw_errors, stamps, progress = [], [], [], []
    left, right, _ = world_geometry(Path(__file__).parents[1] / 'worlds/autocross_seed3.sdf')
    reference = ReferenceTrack(paired_centerline(left, right))
    for row in poses:
        actual = pose_at(truth, row[0])
        if actual is None:
            continue
        stamps.append(row[0])
        errors.append(float(np.linalg.norm(row[1:3]-actual[:2])))
        delta = planar(np.asarray(row[1:]))[2]-planar(actual)[2]
        yaw_errors.append(abs((delta+math.pi) % (2*math.pi)-math.pi))
        progress.append(reference.project(*actual[:2])[1])
    progress = (np.unwrap(np.asarray(progress)*2*math.pi/reference.length)
                * reference.length/(2*math.pi))
    progress -= progress[0]
    stamps, errors, yaw_errors = map(np.asarray, [stamps, errors, yaw_errors])
    noise, covariance, fix_stamps = [], [], []
    for fix in fixes:
        time = fix.header.stamp.sec+fix.header.stamp.nanosec*1e-9
        actual = pose_at(truth, time)
        if actual is None:
            continue
        antenna = actual[:3]+Rotation.from_quat(actual[3:]).apply(lever)
        point = origin.to_enu(fix.latitude, fix.longitude, fix.altitude)
        noise.append(point-antenna)
        covariance.append(np.diag(np.asarray(fix.position_covariance).reshape(3, 3)))
        fix_stamps.append(time)
    mask = (progress >= 1) & (progress <= 6)
    contacts = json.loads((capture / 'cone-contacts.json').read_text())
    contact_time = contacts[0][0] if contacts else float('inf')
    pre_contact = stamps < contact_time
    accepted = [d for d in decisions if d['accepted']]
    summary = {
        'capture': str(capture), 'fixes': len(fixes), 'fusion_decisions': len(decisions),
        'accepted': sum(d['accepted'] for d in decisions),
        'reasons': dict(Counter(d['reason'] for d in decisions)),
        'raw_noise_stddev_enu_m': np.std(noise, axis=0, ddof=1).tolist() if noise else None,
        'mean_reported_variance_enu_m2': (
            np.mean(covariance, axis=0).tolist() if covariance else None),
        'median_fix_period_s': (
            float(np.median(np.diff(fix_stamps))) if len(fix_stamps) > 1 else None),
        'median_fusion_age_s': float(np.median([
            d['state_stamp_s']-d['stamp_s'] for d in accepted])) if accepted else None,
        'pre_contact_decisions': dict(Counter(
            d['reason'] for d in decisions if d['state_stamp_s'] < contact_time)),
        'pre_contact_mean_position_error_m': float(errors[pre_contact].mean()),
        'pre_contact_max_position_error_m': float(errors[pre_contact].max()),
        'common_route_section_m': [1, 6], 'common_section_samples': int(mask.sum()),
        'common_section_mean_position_error_m': float(errors[mask].mean()),
        'common_section_max_position_error_m': float(errors[mask].max()),
        'common_section_mean_heading_error_rad': float(yaw_errors[mask].mean()),
        'full_trace_max_position_error_m': float(errors.max()),
        'heading_error_at_6s_rad': float(np.interp(6., stamps, yaw_errors)),
        'first_physics_contact': contacts[:1],
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    np.savetxt(out / 'errors.csv', np.column_stack([stamps, progress, errors, yaw_errors]),
               delimiter=',', header='stamp_s,route_progress_m,position_error_m,heading_error_rad',
               comments='')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('out', type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.capture, args.out), indent=2))


if __name__ == '__main__':
    main()
