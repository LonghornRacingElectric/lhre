#!/usr/bin/env python3
"""Metrics v0: CTE, off-track count, lap detection, speed stats, CSV output."""

import csv
import math
import os
import sys
import time
from typing import List, Tuple

from lhr_metrics.track_error import TrackErrorAccumulator
from lhr_vehicle import vehicle_sha256
from nav_msgs.msg import Odometry, Path
from rcl_interfaces.msg import ParameterDescriptor
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String


# The launch arguments that change a run's outcome, recorded beside the
# outcomes. A row listing only results cannot be compared with another
# row, because nothing in it says what differed between the two runs.
# A new launch argument that affects the result belongs here too, or
# the row quietly stops explaining itself.
RUN_PARAMS = (
    ('scenario', 'mvs_demo'),
    ('git_sha', 'unknown'),
    ('seed', 0),
    ('track_style', ''),
    ('num_waypoints', 0),
    ('mission', ''),
    ('perception', 'sim'),
    ('plant', 'kinematic'),
    ('motion_distortion', False),
    ('clutter_profile', 'none'),
    ('start_finish_cones', False),
    ('stack_window_sec', 0.5),
    ('min_cluster_points', 3),
    ('ground_z_min', 0.05),
    ('start_on_track', True),
    ('lidar', False),
    ('mount_pitch_rad', 0.0),
    ('elevation_profile', 'livox'),
    ('return_profile', 'baseline'),
    ('fov_deg', 0.0),
    ('max_range_m', 0.0),
    ('noise_std_m', 0.0),
    ('false_negative_rate', 0.0),
    ('lookahead_dist', 0.0),
    ('a_lat_max', 0.0),
    ('v_min', 0.0),
    ('v_max', 0.0),
    ('max_accel', 0.0),
    ('max_decel', 0.0),
)

OUTCOMES = (
    'outcome', 'duration_s', 'samples', 'path_length_m', 'mean_cte',
    'max_cte', 'off_track_count', 'off_track_dist_m', 'mean_speed',
    'max_speed', 'lap_completed',
)

# Why the run ended. Only 'lap' and 'mission_finished' are a pass; the
# others exit non-zero so a headless run cannot look successful by
# having quietly produced nothing.
CLEAN_OUTCOMES = ('lap', 'mission_finished')

CSV_HEADER = (
    ['run_id', 'vehicle_sha256']
    + [name for name, _ in RUN_PARAMS]
    + list(OUTCOMES)
)


class MetricsNode(Node):
    """Compute CTE stats, speed stats, and detect lap completion."""

    def __init__(self):
        super().__init__('metrics_node')

        # --- Parameters ---
        self.declare_parameter('off_track_threshold', 2.0)
        self.declare_parameter('start_radius', 2.0)
        self.declare_parameter('start_hysteresis', 1.0)
        self.declare_parameter('min_lap_time', 5.0)
        self.declare_parameter('output_csv', 'data/metrics.csv')
        # Dynamically typed, not pinned to string: a run id that looks
        # like a number arrives as one. Launch hands parameters over in
        # a YAML file, where 20261004_120000 is the integer
        # 20261004120000, and a typed declaration turns that into a
        # crash over what is only an identifier.
        self.declare_parameter(
            'run_id', '', ParameterDescriptor(dynamic_typing=True))
        self.declare_parameter('timeout_sec', 120.0)

        self._off_track_thresh = self.get_parameter(
            'off_track_threshold').get_parameter_value().double_value
        self._start_radius = self.get_parameter(
            'start_radius').get_parameter_value().double_value
        self._start_hyst = self.get_parameter(
            'start_hysteresis').get_parameter_value().double_value
        self._min_lap_time = self.get_parameter(
            'min_lap_time').get_parameter_value().double_value
        self._csv_path = self.get_parameter(
            'output_csv').get_parameter_value().string_value
        run_id = self.get_parameter('run_id').value
        self._run_id = (
            str(run_id) if run_id not in (None, '')
            else time.strftime('%Y%m%dT%H%M%S'))

        # --- Provenance and independent variables ---
        self._run_params = {}
        for name, default in RUN_PARAMS:
            self.declare_parameter(name, default)
            self._run_params[name] = str(self.get_parameter(name).value)

        try:
            self._vehicle_sha = vehicle_sha256()
        except (FileNotFoundError, OSError) as exc:
            # Losing provenance is worth a warning, not a dead run.
            self._vehicle_sha = 'unknown'
            self.get_logger().warn(f'vehicle_sha256 unavailable: {exc}')

        # --- Centerline cache ---
        self._path: List[Tuple[float, float]] = []

        # --- CTE stats ---
        # Weighted by arc length, not by sample count. See
        # lhr_metrics/track_error.py for why that distinction decides
        # whether two runs of the same seed are comparable.
        self._cte = TrackErrorAccumulator(self._off_track_thresh)
        self._start_time: float = 0.0
        self._last_time: float = 0.0
        self._warned_unstamped = False

        # --- Speed stats ---
        self._speed_sum = 0.0
        self._speed_max = 0.0

        # Wall time on purpose: this is the watchdog that stops a
        # headless run hanging when the sim dies and sim time freezes.
        self.timeout_sec = self.get_parameter(
            'timeout_sec').get_parameter_value().double_value

        # --- Run lifecycle ---
        self.finished = False
        self._outcome = 'interrupted'

        # --- Lap detection state ---
        self._lap_completed = False
        self._near_start = False
        self._left_start = False
        self._lap_start_time: float = 0.0

        # --- Publisher ---
        self._lap_pub = self.create_publisher(
            Bool, '/lhr/metrics/lap_complete', 10)

        # --- Subscribers ---
        self.create_subscription(
            Path, '/lhr/track/centerline', self._path_cb, 10)
        self.create_subscription(
            Odometry, '/lhr/vehicle/odom', self._odom_cb, 10)
        self.create_subscription(
            String, '/lhr/mission/status', self._status_cb, 10)

        self.get_logger().info(
            f'Metrics v0 ready  (run_id={self._run_id}, '
            f'off_track>{self._off_track_thresh}m)')

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------
    def _status_cb(self, msg: String):
        # FINISHED is terminal in the mission manager, so it ends the
        # run even on a mission with no lap, such as acceleration.
        if msg.data == 'FINISHED':
            self.finish('mission_finished')
        elif msg.data == 'EMERGENCY':
            self.finish('emergency')

    def _path_cb(self, msg: Path):
        self._path = [
            (ps.pose.position.x, ps.pose.position.y)
            for ps in msg.poses
        ]

    def _odom_cb(self, msg: Odometry):
        if not self._path:
            return

        px = msg.pose.pose.position.x
        py = msg.pose.pose.position.y

        now = self._stamp_sec(msg.header.stamp)
        if self._cte.samples == 0:
            self._start_time = now
            self._lap_start_time = now
        self._last_time = now

        # --- CTE ---
        self._cte.add(px, py, self._nearest_distance(px, py))

        # --- Speed ---
        vx = msg.twist.twist.linear.x
        vy = msg.twist.twist.linear.y
        speed = math.hypot(vx, vy)
        self._speed_sum += speed
        if speed > self._speed_max:
            self._speed_max = speed

        # --- Lap detection ---
        if not self._lap_completed and len(self._path) > 1:
            self._update_lap_detection(px, py, now)

    # ------------------------------------------------------------------
    # Clock
    # ------------------------------------------------------------------
    def _stamp_sec(self, stamp) -> float:
        """
        Seconds from a message stamp, so timing follows the run's clock.

        Wall time made a run's duration depend on host load, so two
        identical runs never produced the same row. Odom arrives stamped
        from the kinematic sim and from the Gazebo bridge; an unstamped
        message warns once rather than silently reporting zero.
        """
        if stamp.sec == 0 and stamp.nanosec == 0:
            if not self._warned_unstamped:
                self._warned_unstamped = True
                self.get_logger().warn(
                    'odom has no header stamp; falling back to the node '
                    'clock, so durations are not reproducible')
            return self.get_clock().now().nanoseconds * 1e-9
        return stamp.sec + stamp.nanosec * 1e-9

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------
    def _nearest_distance(self, px: float, py: float) -> float:
        """Distance from (px, py) to nearest point on centerline."""
        best = float('inf')
        for cx, cy in self._path:
            d2 = (px - cx) ** 2 + (py - cy) ** 2
            if d2 < best:
                best = d2
        return math.sqrt(best)

    # ------------------------------------------------------------------
    # Lap detection
    # ------------------------------------------------------------------
    def _update_lap_detection(self, px: float, py: float, now: float):
        sx, sy = self._path[0]
        dist = math.hypot(px - sx, py - sy)

        in_zone = dist < self._start_radius
        beyond = dist > (self._start_radius + self._start_hyst)

        if not self._left_start:
            if beyond:
                self._left_start = True
                self._lap_start_time = now
        else:
            elapsed = now - self._lap_start_time
            if in_zone and elapsed > self._min_lap_time:
                self._lap_completed = True
                self._lap_pub.publish(Bool(data=True))
                self.get_logger().info('Lap completed!')
                self.finish('lap')

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------
    def _build_row(self) -> dict:
        samples = self._cte.samples
        duration = (
            self._last_time - self._start_time if samples else 0.0)
        mean_speed = (self._speed_sum / samples) if samples else 0.0
        row = {
            'run_id': self._run_id,
            'vehicle_sha256': self._vehicle_sha,
            'outcome': self._outcome,
            'duration_s': f'{duration:.2f}',
            'samples': str(samples),
            'path_length_m': f'{self._cte.path_length:.2f}',
            'mean_cte': f'{self._cte.mean_cte:.4f}',
            'max_cte': f'{self._cte.max_cte:.4f}',
            'off_track_count': str(self._cte.off_track_samples),
            'off_track_dist_m': f'{self._cte.off_track_distance:.2f}',
            'mean_speed': f'{mean_speed:.2f}',
            'max_speed': f'{self._speed_max:.2f}',
            'lap_completed': str(self._lap_completed).lower(),
        }
        row.update(self._run_params)
        return row

    def _print_summary(self):
        row = self._build_row()
        self.get_logger().info('--- Metrics Summary ---')
        for k, v in row.items():
            self.get_logger().info(f'  {k}: {v}')

    @staticmethod
    def _stale_header(path: str) -> bool:
        """Report whether an existing CSV's columns differ from ours."""
        try:
            with open(path, newline='') as f:
                return next(csv.reader(f), []) != list(CSV_HEADER)
        except OSError:
            return False

    def _write_csv(self):
        csv_dir = os.path.dirname(self._csv_path)
        if csv_dir:
            os.makedirs(csv_dir, exist_ok=True)

        # A file written before a column existed cannot hold the new row.
        # Set it aside rather than dropping the extra fields, which is
        # how a metrics file quietly starts lying about what it holds.
        if (os.path.exists(self._csv_path)
                and self._stale_header(self._csv_path)):
            retired = self._csv_path + '.old'
            os.replace(self._csv_path, retired)
            self.get_logger().warn(
                f'{self._csv_path} had an older column set; '
                f'moved it to {retired}')

        write_header = not os.path.exists(self._csv_path)
        row = self._build_row()

        with open(self._csv_path, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
            if write_header:
                writer.writeheader()
            writer.writerow(row)

        self.get_logger().info(f'CSV row appended to {self._csv_path}')

    def finish(self, outcome: str):
        """
        End the run, recording why.

        Every ending goes through here so a row is always written.
        Before this, only Ctrl+C wrote one, so a headless run produced
        nothing and still exited zero.
        """
        if self.finished:
            return
        self._outcome = outcome
        self.finished = True
        self.get_logger().info(f'Run ended: {outcome}')

    def finalize(self) -> int:
        """
        Write the row and return the process exit code.

        Non-zero on anything but a clean finish, so a gate can read the
        exit code alone and a hung or crashed run cannot pass.
        """
        if self._cte.samples > 0:
            self._print_summary()
            self._write_csv()
        else:
            self.get_logger().error('No odom samples; nothing to write')
            return 1
        return 0 if self._outcome in CLEAN_OUTCOMES else 1


def main():
    """Entry point."""
    rclpy.init()
    node = MetricsNode()
    deadline = (time.monotonic() + node.timeout_sec
                if node.timeout_sec > 0.0 else None)
    try:
        while rclpy.ok() and not node.finished:
            rclpy.spin_once(node, timeout_sec=0.1)
            if deadline is not None and time.monotonic() > deadline:
                node.finish('timeout')
    except KeyboardInterrupt:
        node.finish('interrupted')
    code = node.finalize()
    node.destroy_node()
    rclpy.shutdown()
    sys.exit(code)
