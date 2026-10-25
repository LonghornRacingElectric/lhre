#!/usr/bin/env python3
"""Metrics v0: CTE, off-track count, lap detection, speed stats, CSV output."""

import csv
import math
import os
import sys
import time
from typing import Optional, Tuple

from lhr_metrics.alignment import TruthAligner
from lhr_metrics.course import CourseEnvelope, FOOTPRINT_MODEL, SWEEP_STEP_M
from lhr_metrics.reference import (
    CircuitProgress, paired_centerline, ReferenceTrack, world_geometry)
from lhr_metrics.track_error import TrackErrorAccumulator
from lhr_vehicle import load_vehicle, vehicle_sha256
from nav_msgs.msg import Odometry
from rcl_interfaces.msg import ParameterDescriptor
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import Bool, String
from visualization_msgs.msg import MarkerArray


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
    ('estimator', 'truth'),
    ('gyro_bias', 0.0),
    ('wheel_scale_error', 1.0),
    ('lidar_classify_sides', False),
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

SCORING_PARAMS = (
    ('off_track_threshold', 2.0),
    ('start_radius', 2.0),
    ('min_lap_time', 5.0),
    ('reference_world', ''),
    ('reference_closed', True),
    ('min_progress_fraction', 0.98),
    ('lap_start_speed', 0.1),
    ('max_odom_gap_sec', 1.0),
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

GATE_FIELDS = ('lap_gate_x_m', 'lap_gate_y_m', 'lap_gate_tangent_x', 'lap_gate_tangent_y')

CSV_HEADER = (
    ['run_id', 'vehicle_sha256']
    + [name for name, _ in RUN_PARAMS]
    + [name for name, _ in SCORING_PARAMS]
    + list(OUTCOMES)
    + ['mean_pos_error', 'max_pos_error', 'mean_yaw_error']
    + ['reference_sha256', 'reference_source', 'reference_length_m',
       'lap_progress_m', 'lap_time_s', 'lap_invalid_reason', 'lap_start_stamp_s',
       'scoring_odom_topic', 'course_sha256', 'footprint_model', 'sweep_step_m',
       'cones_hit', 'cone_hit_ids']
    + list(GATE_FIELDS)
)

ERROR_FIELDS = ('mean_pos_error', 'max_pos_error', 'mean_yaw_error')


def quat_to_yaw(q) -> float:
    """Extract the yaw angle (rad) from a quaternion."""
    return math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                      1.0 - 2.0 * (q.y * q.y + q.z * q.z))


def wrap_angle(angle: float) -> float:
    """Wrap an angle into [-pi, pi)."""
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


class MetricsNode(Node):
    """Score against latched truth geometry and detect a continuous directed circuit."""

    def __init__(self):
        super().__init__('metrics_node')

        # --- Parameters ---
        for name, default in SCORING_PARAMS:
            self.declare_parameter(name, default)
        self._scoring_params = {name: str(self.get_parameter(name).value)
                                for name, _ in SCORING_PARAMS}
        self.declare_parameter('output_csv', 'data/metrics.csv')
        # Dynamically typed, not pinned to string: a run id that looks
        # like a number arrives as one. Launch hands parameters over in
        # a YAML file, where 20261004_120000 is the integer
        # 20261004120000, and a typed declaration turns that into a
        # crash over what is only an identifier.
        self.declare_parameter(
            'run_id', '', ParameterDescriptor(dynamic_typing=True))
        self.declare_parameter('timeout_sec', 0.0)
        self.declare_parameter('truth_odom_topic', '')

        self._off_track_thresh = self.get_parameter(
            'off_track_threshold').get_parameter_value().double_value
        self._start_radius = self.get_parameter(
            'start_radius').get_parameter_value().double_value
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

        # Reference truth is latched once, never read from the planner.
        self._reference = self._lap = self._course = None
        self._boundary_off_count = 0
        self._boundary_off_distance = 0.
        self._last_scored_position = None
        self._reference_source = ''
        self._mission_state = ''
        self._last_stamp = None

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
        # --- Estimate-vs-truth stats (only when a truth topic is given) ---
        self._truth: Optional[Tuple[float, float, float]] = None
        self._aligner = TruthAligner()
        self._err_samples = 0
        self._pos_err_sum = 0.0
        self._pos_err_max = 0.0
        self._yaw_err_sum = 0.0

        # --- Lap detection state ---
        self._lap_completed = False
        # --- Publisher ---
        self._lap_pub = self.create_publisher(
            Bool, '/lhr/metrics/lap_complete', 10)

        # --- Subscribers ---
        self._truth_topic = self.get_parameter('truth_odom_topic').value
        reference_world = self.get_parameter('reference_world').value
        if reference_world:
            self._set_reference(*world_geometry(
                reference_world, acceleration=self._run_params['mission'] == 'acceleration'
                or self._run_params['track_style'] == 'acceleration'), source=reference_world)
        else:
            qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                             durability=DurabilityPolicy.TRANSIENT_LOCAL)
            self.create_subscription(
                MarkerArray, '/lhr/track/cones', self._reference_cb, qos)
        self.create_subscription(
            Odometry, '/lhr/vehicle/odom', self._odom_cb, 10)
        self.create_subscription(
            String, '/lhr/mission/status', self._status_cb, 10)

        truth_topic = self._truth_topic
        if truth_topic:
            self.create_subscription(
                Odometry, truth_topic, self._truth_cb, 10)

        self.get_logger().info(
            f'Metrics v0 ready  (run_id={self._run_id}, '
            f'course_scoring={FOOTPRINT_MODEL}, '
            f'truth={truth_topic or "none"})')

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------
    def _status_cb(self, msg: String):
        self._mission_state = msg.data
        # FINISHED is terminal in the mission manager, so it ends the
        # run even on a mission with no lap, such as acceleration.
        if msg.data == 'FINISHED':
            if self._run_params['mission'] == 'autocross' and not self._lap_completed:
                self.finish('invalid_completion')
            else:
                self.finish('mission_finished')
        elif msg.data == 'EMERGENCY':
            self.finish('emergency')

    def _set_reference(self, left, right, cones, source):
        closed = self.get_parameter('reference_closed').value
        track = ReferenceTrack(paired_centerline(left, right), closed)
        course = CourseEnvelope(left, right, cones, load_vehicle(), closed)
        lap = CircuitProgress(
            track, min_fraction=self.get_parameter('min_progress_fraction').value,
            gate_half_width=self._start_radius, min_lap_time=self._min_lap_time,
            start_speed=self.get_parameter('lap_start_speed').value,
            max_gap=self.get_parameter('max_odom_gap_sec').value,
            corridor=self._off_track_thresh, on_course=course.contains)
        self._reference, self._lap, self._course = track, lap, course
        self._reference_source = source

    def _reference_cb(self, message: MarkerArray):
        if self._reference is not None:
            return
        sides = []
        for namespace in ('left_cones', 'right_cones'):
            markers = sorted((m for m in message.markers if m.ns == namespace),
                             key=lambda marker: marker.id)
            if any(m.header.frame_id != 'map' for m in markers):
                self.get_logger().warning('Reference truth must use the map frame')
                return
            sides.append([(m.pose.position.x, m.pose.position.y) for m in markers])
        try:
            cones = []
            kinds = {'left_cones': 'blue', 'right_cones': 'yellow',
                     'start_finish': 'orange_large'}
            for marker in message.markers:
                if marker.ns not in kinds:
                    continue
                if marker.header.frame_id != 'map':
                    raise ValueError('cone truth must use the map frame')
                kind = marker.text or kinds[marker.ns]
                cones.append((f'{marker.ns}/{marker.id}', marker.pose.position.x,
                              marker.pose.position.y, quat_to_yaw(marker.pose.orientation), kind))
            self._set_reference(*sides, cones, '/lhr/track/cones')
        except ValueError as error:
            self.get_logger().warning(f'Waiting for complete reference truth: {error}')

    def _odom_cb(self, msg: Odometry):
        if self._truth_topic:
            # With an estimator, only truth can earn progress or a pass.
            # Estimates remain an error measurement, never a fallback plant pose.
            stamp = self._stamp_sec(msg.header.stamp)
            if (self._truth is not None and not self.finished and self._lap is not None
                    and self._lap.started_at is not None and self._mission_state in ('', 'DRIVING')
                    and stamp is not None and stamp >= self._lap.started_at):
                for errors in self._aligner.add_estimate(
                        stamp, msg.pose.pose.position.x, msg.pose.pose.position.y,
                        quat_to_yaw(msg.pose.pose.orientation)):
                    self._accumulate_error(*errors)
        else:
            self._score_odom(msg)

    def _score_odom(self, msg: Odometry):
        if self._reference is None or self.finished:
            return
        if msg.header.frame_id != 'map':
            self.get_logger().warning('Scored odometry must use the map frame')
            return

        px = msg.pose.pose.position.x
        py = msg.pose.pose.position.y

        now = self._stamp_sec(msg.header.stamp)
        if now is None:
            return
        vx, vy = msg.twist.twist.linear.x, msg.twist.twist.linear.y
        speed = math.hypot(vx, vy)
        yaw = quat_to_yaw(msg.pose.pose.orientation)
        if not all(map(math.isfinite, (px, py, yaw, speed))):
            self._lap.invalid_reason = 'nonfinite_odometry'
            self.finish('invalid_lap')
            return
        previous_reason = self._lap.invalid_reason
        completed = self._lap.observe(
            px, py, now, speed, self._mission_state in ('', 'DRIVING'))
        if self._lap.started_at is None:
            return
        if self._last_stamp is not None and now <= self._last_stamp:
            if self._lap.invalid_reason:
                self.finish('invalid_lap')
            return
        continuous = self._lap.invalid_reason in ('', 'off_course')
        outside, contacts = self._course.observe(px, py, yaw, continuous=continuous)
        if outside:
            self._boundary_off_count += 1
            if continuous and self._last_scored_position is not None:
                self._boundary_off_distance += math.dist(
                    (px, py), self._last_scored_position)
            self._lap.invalid_reason = self._lap.invalid_reason or 'vehicle_off_course'
        if contacts:
            self._lap.invalid_reason = self._lap.invalid_reason or 'cone_contact'
        self._last_stamp = now
        self._last_scored_position = (px, py)
        self._start_time = self._lap.started_at
        self._last_time = now

        # --- CTE ---
        self._cte.add(px, py, self._nearest_distance(px, py))

        # --- Speed ---
        self._speed_sum += speed
        if speed > self._speed_max:
            self._speed_max = speed

        if self._lap.invalid_reason:
            if not previous_reason:
                self.get_logger().warning(f'Run invalidated: {self._lap.invalid_reason}')
            self.finish('invalid_lap')
            return

        # --- Lap detection ---
        if completed:
            self._lap_completed = True
            self._lap_pub.publish(Bool(data=True))
            self.get_logger().info('Fixed-reference circuit completed')
            self.finish('lap')

    def _truth_cb(self, msg: Odometry):
        self._truth = (
            msg.pose.pose.position.x,
            msg.pose.pose.position.y,
            quat_to_yaw(msg.pose.pose.orientation))
        stamp = self._stamp_sec(msg.header.stamp)
        if stamp is not None and not self.finished:
            # Estimates that were waiting for truth at their stamp score now.
            for errors in self._aligner.add_truth(stamp, *self._truth):
                self._accumulate_error(*errors)
        self._score_odom(msg)

    # ------------------------------------------------------------------
    # Clock
    # ------------------------------------------------------------------
    def _stamp_sec(self, stamp) -> Optional[float]:
        """
        Seconds from a message stamp, so timing follows the run's clock.

        Unstamped samples cannot prove lap timing. Wait for a valid stamp
        rather than silently switching to wall time during a simulation pause.
        """
        if stamp.sec == 0 and stamp.nanosec == 0:
            if not self._warned_unstamped:
                self._warned_unstamped = True
                self.get_logger().warn('Dropping unstamped scoring odometry')
            return None
        return stamp.sec + stamp.nanosec * 1e-9

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------
    def _accumulate_error(self, pos_err: float, yaw_err: float):
        """Add one estimate's error against truth at the same stamp."""
        self._err_samples += 1
        self._pos_err_sum += pos_err
        self._yaw_err_sum += yaw_err
        if pos_err > self._pos_err_max:
            self._pos_err_max = pos_err

    def _nearest_distance(self, px: float, py: float) -> float:
        """Measure distance to the fixed reference's segments, never planner vertices."""
        return self._reference.project(px, py)[0]

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------
    def _build_row(self) -> dict:
        samples = self._cte.samples
        duration = (
            self._last_time - self._start_time if samples else 0.0)
        mean_speed = (self._speed_sum / samples) if samples else 0.0
        if self._err_samples:
            errors = {
                'mean_pos_error': f'{self._pos_err_sum / self._err_samples:.4f}',
                'max_pos_error': f'{self._pos_err_max:.4f}',
                'mean_yaw_error': f'{self._yaw_err_sum / self._err_samples:.4f}',
            }
        else:
            errors = {field: '' for field in ERROR_FIELDS}

        gate = self._lap.gate if self._lap else None
        gate_values = (*gate[0], *gate[1]) if gate else ('',)*4
        row = {
            'run_id': self._run_id,
            'vehicle_sha256': self._vehicle_sha,
            'outcome': self._outcome,
            'duration_s': f'{duration:.2f}',
            'samples': str(samples),
            'path_length_m': f'{self._cte.path_length:.2f}',
            'mean_cte': f'{self._cte.mean_cte:.4f}',
            'max_cte': f'{self._cte.max_cte:.4f}',
            'off_track_count': str(self._boundary_off_count),
            'off_track_dist_m': f'{self._boundary_off_distance:.2f}',
            'mean_speed': f'{mean_speed:.2f}',
            'max_speed': f'{self._speed_max:.2f}',
            'lap_completed': str(self._lap_completed).lower(),
            'reference_sha256': self._reference.sha256 if self._reference else '',
            'reference_source': self._reference_source,
            'scoring_odom_topic': self._truth_topic or '/lhr/vehicle/odom',
            'course_sha256': self._course.sha256 if self._course else '',
            'footprint_model': FOOTPRINT_MODEL,
            'sweep_step_m': str(SWEEP_STEP_M),
            'cones_hit': str(len(self._course.hit_ids)) if self._course else '0',
            'cone_hit_ids': ';'.join(sorted(self._course.hit_ids)) if self._course else '',
            'reference_length_m': f'{self._reference.length:.4f}' if self._reference else '',
            'lap_progress_m': f'{self._lap.progress:.4f}' if self._lap else '',
            'lap_time_s': (f'{self._lap.completed_at - self._lap.started_at:.4f}'
                           if self._lap and self._lap.completed_at is not None else ''),
            'lap_invalid_reason': self._lap.invalid_reason if self._lap else 'missing_reference',
            'lap_start_stamp_s': (f'{self._lap.started_at:.6f}'
                                  if self._lap and self._lap.started_at is not None else ''),
            **{name: f'{value:.6f}' if value != '' else ''
               for name, value in zip(GATE_FIELDS, gate_values)},
            **errors,
        }
        row.update(self._run_params)
        row.update(self._scoring_params)
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
    rclpy.try_shutdown()
    sys.exit(code)
