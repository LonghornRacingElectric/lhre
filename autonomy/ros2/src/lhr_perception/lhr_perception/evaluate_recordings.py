"""Replay stationary acceptance captures through the production cone detector."""

import argparse
import csv
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path

from geometry_msgs.msg import TransformStamped
from lhr_perception.lidar_cone_detector import LidarConeDetector
from lhr_vehicle import load_vehicle, vehicle_sha256
from nav_msgs.msg import Odometry
import numpy as np
import rclpy
from scipy.spatial.transform import Rotation
from sensor_msgs_py import point_cloud2
from std_msgs.msg import Header


@dataclass(frozen=True)
class Settings:
    """Record each independently replayed detector configuration."""

    name: str
    ground_z_min: float
    min_cluster_points: int
    stack_window_sec: float


SETTINGS = (Settings('legacy_evidence', .15, 1, 0.),
            Settings('stack_3', .05, 3, .5), Settings('stack_5', .05, 5, .5))


def level_rotation(imu):
    """Level a stationary recording using its mean accelerometer direction."""
    up = np.asarray(imu[:, 3:6].mean(axis=0), dtype=float)
    norm = np.linalg.norm(up)
    if not np.isfinite(norm) or norm == 0.:
        raise ValueError('Recording has no valid gravity direction')
    up /= norm
    if up[2] < -.999:
        raise ValueError('Upside-down capture requires an explicit mount transform')
    axis = np.cross(up, [0., 0., 1.])
    skew = np.array([[0., -axis[2], axis[1]], [axis[2], 0., -axis[0]],
                     [-axis[1], axis[0], 0.]])
    return np.eye(3) + skew + skew @ skew / (1. + up[2])


def frame_slices(times, frame_sec=.1):
    """Return index slices for every complete frame, including empty ones."""
    if not len(times) or not np.isfinite(times).all() or np.any(np.diff(times) < 0):
        raise ValueError('Recording timestamps must be finite, nonempty and sorted')
    relative = times - times[0]
    count = int(math.floor((relative[-1] + 1e-9) / frame_sec))
    edges = np.searchsorted(relative, np.arange(count + 1) * frame_sec)
    return [(int(a), int(b)) for a, b in zip(edges, edges[1:])]


def evaluate(xyz, times, imu, row, settings):
    """Measure reference matches before persistent-map deduplication."""
    detector = LidarConeDetector()
    detector._ground_z_min = settings.ground_z_min
    detector._min_cluster_pts = settings.min_cluster_points
    detector._stack_window = settings.stack_window_sec
    mount = TransformStamped()
    mount.header.frame_id, mount.child_frame_id = 'base_link', 'lidar'
    mount.transform.translation.x = load_vehicle().lidar_position_m[0]
    mount.transform.translation.y = load_vehicle().lidar_position_m[1]
    mount.transform.translation.z = float(row['sensor_height_m'])
    quat = Rotation.from_matrix(level_rotation(imu)).as_quat()
    q = mount.transform.rotation
    q.x, q.y, q.z, q.w = map(float, quat)
    detector._tf_buffer.set_transform_static(mount, 'stationary_capture')
    bearing = math.radians(float(row['bearing_deg']))
    distance = float(row['range_center_m'])
    target = distance * np.array([math.cos(bearing), math.sin(bearing)])
    target += [mount.transform.translation.x, mount.transform.translation.y]
    matches, errors, unmatched = [], [], []
    observations = []
    original_merge = detector._try_merge

    def observe(mx, my, cone_map):
        observations.append((mx, my))
        return original_merge(mx, my, cone_map)

    detector._try_merge = observe
    try:
        for frame, (begin, end) in enumerate(frame_slices(times)):
            observations.clear()
            header = Header(frame_id='lidar')
            header.stamp.sec = 10 + frame // 10
            header.stamp.nanosec = (frame % 10) * 100000000
            odom = Odometry()
            odom.header.frame_id, odom.child_frame_id = 'map', 'base_link'
            odom.header.stamp = header.stamp
            odom.pose.pose.orientation.w = 1.
            detector._odom_cb(odom)
            cloud = point_cloud2.create_cloud_xyz32(header, xyz[begin:end])
            detector._cloud_cb(cloud)
            detector._process()
            positions = np.asarray(observations).reshape(-1, 2)
            distances = np.linalg.norm(positions - target, axis=1)
            matched = distances <= .35
            matches.append(bool(matched.any()))
            unmatched.append(int((~matched).sum()))
            if matched.any():
                errors.append(float(distances[matched].min()))
        nframes = len(matches)
        return {'settings': asdict(settings), 'frames': nframes,
                'reference_detected_pct': float(100 * np.mean(matches)) if nframes else 0.,
                'median_reference_error_m': float(np.median(errors)) if errors else None,
                'unmatched_candidates_per_frame': float(np.mean(unmatched)) if nframes else 0.,
                'unmatched_candidates_p95': float(np.percentile(unmatched, 95)) if nframes else 0.}
    finally:
        detector.destroy_node()


def main():
    """Evaluate reference-cone matches on local stationary NPZ recordings."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw-dir', type=Path, required=True)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--run', action='append')
    parser.add_argument('--setting', action='append', choices=[s.name for s in SETTINGS])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--domain-id', type=int, default=64,
                        help='Isolated replay domain; must differ from your live stack')
    args = parser.parse_args()
    with args.results.open() as stream:
        rows = [row for row in csv.DictReader(stream) if row['run'].startswith('small')
                and row['tilt_setting'] == 'level' and row['range_center_m'] and row['bearing_deg']
                and (not args.run or row['run'] in args.run)]
    if not rows:
        parser.error('No measured small-cone level recordings match the selection')
    rclpy.init(args=['--ros-args', '--log-level', 'error'], domain_id=args.domain_id)
    runs = []
    try:
        for row in rows:
            path = args.raw_dir / Path(row['raw_npz']).name
            if not path.exists():
                runs.append({'run': row['run'], 'missing': str(path)})
                continue
            with np.load(path, allow_pickle=False) as capture:
                xyz, times, imu = capture['xyz'], capture['t'], capture['imu']
            # Packet timestamps can repeat and arrive out of order. Stable-sort
            # points together with their timestamps; never silently shuffle xyz.
            order = np.argsort(times, kind='stable')
            xyz, times = xyz[order], times[order]
            result = {'run': row['run'], 'range_m': float(row['range_center_m']),
                      'lighting': row['lighting'],
                      'capture_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                      'evaluations': [evaluate(xyz, times, imu, row, setting)
                                      for setting in SETTINGS
                                      if not args.setting or setting.name in args.setting]}
            runs.append(result)
            print(row['run'], [(e['settings']['name'], round(e['reference_detected_pct'], 1),
                                round(e['unmatched_candidates_per_frame'], 1))
                               for e in result['evaluations']], flush=True)
    finally:
        rclpy.shutdown()
    report = {'scope': 'Stationary reference-cone matches, not exhaustive false-positive labels',
              'limitations': ['Mean gravity and published centroid supply reference coordinates',
                              'Unmatched candidates are not proven false positives',
                              'No moving recordings or localization errors are evaluated'],
              'vehicle_sha256': vehicle_sha256(),
              'detector_source_sha256': hashlib.sha256(
                  Path(__file__).with_name('lidar_cone_detector.py').read_bytes()).hexdigest(),
              'evaluator_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'reference_results_sha256': hashlib.sha256(args.results.read_bytes()).hexdigest(),
              'match_radius_m': .35,
              'frame_sec': .1, 'runs': runs}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
