#!/usr/bin/env python3
"""IMU prediction, optional timestamped GNSS correction, odometry and TF out."""

import json
import math

from geometry_msgs.msg import Quaternion, TransformStamped, Vector3
from lhr_state_estimation.ekf import IDX_V, IDX_X, IDX_Y, IDX_YAW, IDX_YAW_RATE, VehicleEkf
from lhr_state_estimation.gnss import LocalENU, PositionHistory
from lhr_state_estimation.gyro_calibration import CALIBRATION_DEFAULTS, GyroCalibration
from lhr_state_estimation.stationary import STATIONARY_DEFAULTS, StationaryEvidence
from lhr_state_estimation.wheel_odometry import body_speed
from lhr_vehicle import load_vehicle
from nav_msgs.msg import Odometry
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.time import Time
from sensor_msgs.msg import Imu, JointState, NavSatFix, PointCloud2
from sensor_msgs_py import point_cloud2
from std_msgs.msg import String
from tf2_ros import TransformBroadcaster


def quat_to_yaw(q) -> float:
    """Extract the yaw angle (rad) from a quaternion."""
    return math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                      1.0 - 2.0 * (q.y * q.y + q.z * q.z))


def yaw_to_quat(yaw: float) -> Quaternion:
    """Convert a yaw angle (rad) to a z-axis quaternion."""
    q = Quaternion()
    q.z = math.sin(yaw / 2.0)
    q.w = math.cos(yaw / 2.0)
    return q


class EkfNode(Node):
    """
    Estimate the vehicle state from inertial sensors.

    Every ordered IMU sample advances the filter at its sensor timestamp.
    The publish timer only emits the latest state, retaining its timestamp
    during dropouts. Rear-wheel correction is a diagnostic opt-in.
    """

    def __init__(self):
        super().__init__('ekf_node')

        veh = load_vehicle()
        self.declare_parameter('imu_topic', '/lhr/imu/data')
        self.declare_parameter('wheel_topic', '/lhr/sensor/wheel_speeds')
        self.declare_parameter('use_wheel_speeds', False)
        self.declare_parameter('use_gnss', False)
        self.declare_parameter('gnss_topic', '/lhr/gnss/fix')
        self.declare_parameter('gnss_frame_id', 'gnss_link')
        self.declare_parameter('gnss_origin_latitude_deg', 0.0)
        self.declare_parameter('gnss_origin_longitude_deg', 0.0)
        self.declare_parameter('gnss_origin_altitude_m', 0.0)
        self.declare_parameter('gnss_max_delay_s', 1.0)
        self.declare_parameter('gnss_gate_chi2', 25.0)
        self.declare_parameter('gnss_heading_gain', 1.0)
        self.declare_parameter('use_stationary_updates', False)
        for name, value in STATIONARY_DEFAULTS.items():
            self.declare_parameter(name, value)
        self.declare_parameter('calibrate_gyro', False)
        for name, value in CALIBRATION_DEFAULTS.items():
            self.declare_parameter(name, value)
        self.declare_parameter('odom_topic', '/lhr/vehicle/odom')
        self.declare_parameter('publish_hz', 50.0)
        self.declare_parameter('frame_id', 'map')
        self.declare_parameter('child_frame_id', 'base_link')
        self.declare_parameter('publish_tf', True)
        self.declare_parameter('wheel_radius', veh.wheel_radius_m)
        self.declare_parameter('rear_wheel_names', ['rl', 'rr'])
        # The car gets its start pose from a survey or GNSS; sim gets it from
        # the same launch arguments that place the vehicle.
        self.declare_parameter('init_x', 0.0)
        self.declare_parameter('init_y', 0.0)
        self.declare_parameter('init_yaw', 0.0)
        # Seed the pose from the first message on this topic instead, for
        # sources whose start pose the launch file does not know (Gazebo
        # spawns the car wherever the world puts it). Stands in for the
        # survey or GNSS fix that will seed the filter on the car.
        self.declare_parameter('init_pose_topic', '')
        # Tuning. Process terms are how much the model is trusted to hold
        # between corrections; measurement terms are sensor variances.
        self.declare_parameter('sigma_accel', 1.0)
        self.declare_parameter('sigma_yaw_accel', 1.0)
        self.declare_parameter('speed_variance', 0.01)
        self.declare_parameter('yaw_rate_variance', 0.0004)

        self._frame_id = self._str_param('frame_id')
        self._child_frame_id = self._str_param('child_frame_id')
        self._publish_tf = self.get_parameter(
            'publish_tf').get_parameter_value().bool_value
        self._wheel_radius = self._float_param('wheel_radius')
        self._use_wheels = self.get_parameter('use_wheel_speeds').value
        self._rear_names = list(self.get_parameter(
            'rear_wheel_names').get_parameter_value().string_array_value)
        self._speed_var = self._float_param('speed_variance')
        self._yaw_rate_var = self._float_param('yaw_rate_variance')
        if not math.isfinite(self._yaw_rate_var) or self._yaw_rate_var <= 0:
            raise ValueError('yaw_rate_variance must be finite and positive')
        self._gnss_heading_gain = self._float_param('gnss_heading_gain')
        if not math.isfinite(self._gnss_heading_gain) or not 0 <= self._gnss_heading_gain <= 1:
            raise ValueError('gnss_heading_gain must be finite and within [0, 1]')

        self._ekf = VehicleEkf(
            sigma_accel=self._float_param('sigma_accel'),
            sigma_yaw_accel=self._float_param('sigma_yaw_accel'))
        self._ekf.reset(
            x=self._float_param('init_x'),
            y=self._float_param('init_y'),
            yaw=self._float_param('init_yaw'))

        self._last_imu_stamp_ns = None
        self._previous_accel_x = 0.0
        self._warned_wheels = False
        self._gyro_bias = 0.
        self._calibration = None
        self._calibration_applied = False
        self._calibration_last_status = None
        self._calibration_last_report_ns = 0
        self._calibration_frame = None
        self._calibration_subscription = None
        if self.get_parameter('calibrate_gyro').value:
            self._calibration = GyroCalibration({
                name: self.get_parameter(name).value for name in CALIBRATION_DEFAULTS})
            from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
            self._calibration_pub = self.create_publisher(
                String, '/lhr/state_estimation/gyro_calibration',
                QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
            self._calibration_mount = np.asarray(veh.lidar_position_m)
            self._calibration_car = (
                -(veh.wheel_radius_m+.3), veh.chassis_center_x_m+veh.body_length_m/2+.25,
                veh.half_track_m+veh.wheel_width_m/2+.12)
            reliability = (ReliabilityPolicy.RELIABLE if self._str_param(
                'gyro_calibration_lidar_qos') == 'reliable' else ReliabilityPolicy.BEST_EFFORT)
            self._calibration_subscription = self.create_subscription(
                PointCloud2, self._str_param('gyro_calibration_lidar_topic'),
                self._calibration_cloud_cb, QoSProfile(depth=50, reliability=reliability))
        self._use_gnss = self.get_parameter('use_gnss').value
        self._gnss_pending = []
        self._last_gnss_received = -1
        self._gnss_lever = veh.gnss.position_m
        self._gnss_history = None
        if self._use_gnss:
            # The delayed history replays IMUs only. Silently losing intervening
            # wheel corrections would make the legacy comparison inconsistent.
            if self._use_wheels:
                raise ValueError('use_gnss requires use_wheel_speeds:=false')
            if (not math.isfinite(self._float_param('gnss_gate_chi2'))
                    or self._float_param('gnss_gate_chi2') <= 0):
                raise ValueError('gnss_gate_chi2 must be positive and finite')
            self._gnss_origin = LocalENU(
                self._float_param('gnss_origin_latitude_deg'),
                self._float_param('gnss_origin_longitude_deg'),
                self._float_param('gnss_origin_altitude_m'))
            self._gnss_history = PositionHistory(self._ekf, self._float_param('gnss_max_delay_s'))
            self._gnss_status = self.create_publisher(
                String, '/lhr/state_estimation/gnss_status', 10)
            from rclpy.qos import qos_profile_sensor_data
            self.create_subscription(NavSatFix, self._str_param('gnss_topic'),
                                     self._gnss_cb, qos_profile_sensor_data)

        self._stationary = None
        self._stationary_pending = None
        if self.get_parameter('use_stationary_updates').value:
            if self._calibration is None or not self._use_gnss:
                raise ValueError('stationary updates require calibrate_gyro and use_gnss')
            self._stationary = StationaryEvidence({
                name: self.get_parameter(name).value for name in STATIONARY_DEFAULTS})
            self._stationary_pub = self.create_publisher(
                String, '/lhr/state_estimation/stationary_status', 10)
            self.create_subscription(
                PointCloud2, self._str_param('gyro_calibration_lidar_topic'),
                self._stationary_cloud_cb, QoSProfile(depth=50, reliability=reliability))

        init_topic = self._str_param('init_pose_topic')
        self._awaiting_init = bool(init_topic)
        if init_topic:
            self.create_subscription(
                Odometry, init_topic, self._init_pose_cb, 10)

        self.create_subscription(
            Imu, self._str_param('imu_topic'), self._imu_cb, 1000)
        if self._use_wheels:
            self.create_subscription(
                JointState, self._str_param('wheel_topic'), self._wheel_cb, 50)
        self._odom_pub = self.create_publisher(
            Odometry, self._str_param('odom_topic'), 10)
        self._tf_bc = TransformBroadcaster(self)

        publish_hz = self._float_param('publish_hz')
        self.create_timer(1.0 / publish_hz, self._step)
        self.get_logger().info(
            f'EKF ready  (hz={publish_hz}, r={self._wheel_radius:.4f}, '
            f'rear={self._rear_names}, wheel_updates={self._use_wheels})')
        if self._calibration is not None:
            self.get_logger().info('Waiting for stationary gyro calibration; odometry withheld')

    # ------------------------------------------------------------------
    def _str_param(self, name: str) -> str:
        return self.get_parameter(name).get_parameter_value().string_value

    def _float_param(self, name: str) -> float:
        return self.get_parameter(name).get_parameter_value().double_value

    # ------------------------------------------------------------------
    # Sensor callbacks
    # ------------------------------------------------------------------
    def _init_pose_cb(self, msg: Odometry):
        if not self._awaiting_init:
            return
        x = msg.pose.pose.position.x
        y = msg.pose.pose.position.y
        yaw = quat_to_yaw(msg.pose.pose.orientation)
        self._ekf.reset(x=x, y=y, yaw=yaw)
        self._last_imu_stamp_ns = None
        if self._gnss_history is not None:
            self._gnss_history.records.clear()
            self._gnss_history.last_fix_ns = -1
            self._gnss_pending.clear()
            self._last_gnss_received = -1
        self._awaiting_init = False
        self.get_logger().info(
            f'seeded pose: x={x:.2f} y={y:.2f} yaw={yaw:.3f}')

    def _imu_cb(self, msg: Imu):
        if self._awaiting_init:
            return
        stamp_ns = msg.header.stamp.sec*1_000_000_000 + msg.header.stamp.nanosec
        if self._calibration is not None and not self._calibration_applied:
            g, a = msg.angular_velocity, msg.linear_acceleration
            self._calibration.add_imu(stamp_ns, [g.x, g.y, g.z], [a.x, a.y, a.z])
            self._report_calibration(stamp_ns)
            if not self._calibration_applied:
                return
        if self._last_imu_stamp_ns is not None and stamp_ns <= self._last_imu_stamp_ns:
            return
        accel_x = msg.linear_acceleration.x
        if self._last_imu_stamp_ns is not None:
            dt = (stamp_ns-self._last_imu_stamp_ns)*1e-9
            if dt > 0.1:
                self.get_logger().warning(f'IMU timestamp gap: {dt:.3f} s')
            # The interval integral must include short events between output
            # ticks; publish-rate sampling can miss an entire impact pulse.
            self._ekf.predict(dt, 0.5*(self._previous_accel_x+accel_x))
        gyro_z = msg.angular_velocity.z-self._gyro_bias
        if self._stationary is not None:
            g, a = msg.angular_velocity, msg.linear_acceleration
            self._stationary.add_imu(stamp_ns, [g.x, g.y, gyro_z], [a.x, a.y, a.z])
        self._ekf.update_yaw_rate(gyro_z, self._yaw_rate_var)
        self._last_imu_stamp_ns = stamp_ns
        self._previous_accel_x = accel_x
        if self._gnss_history is not None:
            self._gnss_history.record(
                stamp_ns, accel_x, gyro_z, self._yaw_rate_var)
            while self._gnss_pending and self._gnss_pending[0][0] <= stamp_ns:
                fix_stamp, position, covariance = self._gnss_pending.pop(0)
                accepted, reason, nis = self._gnss_history.correct(
                    fix_stamp, position, covariance, self._gnss_lever,
                    self._float_param('gnss_gate_chi2'), self._gnss_heading_gain)
                self._report_gnss(fix_stamp, accepted, reason, nis,
                                  self._gnss_history.last_correction)

        self._apply_stationary()

    def _apply_stationary(self):
        if self._stationary_pending is None or self._last_imu_stamp_ns is None:
            return
        stamp_ns = self._stationary_pending
        if stamp_ns > self._last_imu_stamp_ns:
            return
        self._stationary_pending = None
        applied = False
        if self._stationary.fresh(self._last_imu_stamp_ns):
            applied = self._gnss_history.correct_stationary_speed(
                stamp_ns, self._stationary.get('speed_variance'))
        self._stationary_pub.publish(String(data=json.dumps({
            'stamp_s': stamp_ns*1e-9, 'stationary': self._stationary.ready,
            'reason': self._stationary.reason, 'applied': applied})))

    def _stationary_cloud_cb(self, msg):
        if not self._calibration_applied or self._last_imu_stamp_ns is None:
            return
        stamp_ns = msg.header.stamp.sec*1_000_000_000+msg.header.stamp.nanosec
        if (stamp_ns <= 0 or msg.header.frame_id != self._calibration_frame
                or abs(self.get_clock().now().nanoseconds-stamp_ns)*1e-9
                > self._stationary.get('max_gap_s')):
            self._stationary.reset('stale_or_changed_frame')
        else:
            self._stationary.add_cloud(stamp_ns, self._scene_points(msg))
        if self._stationary.ready:
            self._stationary_pending = stamp_ns
            self._apply_stationary()
        else:
            self._stationary_pending = None
            self._stationary_pub.publish(String(data=json.dumps({
                'stamp_s': stamp_ns*1e-9, 'stationary': False,
                'reason': self._stationary.reason, 'applied': False})))

    def _scene_points(self, msg):
        points = point_cloud2.read_points_numpy(
            msg, field_names=('x', 'y', 'z'), skip_nans=True).reshape(-1, 3)
        points = points[np.isfinite(points).all(axis=1)]+self._calibration_mount
        rear, front, half_width = self._calibration_car
        body = ((points[:, 0] > rear) & (points[:, 0] < front)
                & (abs(points[:, 1]) < half_width))
        ranges = np.linalg.norm(points[:, :2]-self._calibration_mount[:2], axis=1)
        valid = (~body & (points[:, 2] > .05) & (points[:, 2] < .55)
                 & (ranges > .8) & (ranges < 20))
        return points[valid, :2]

    def _report_calibration(self, stamp_ns):
        status = self._calibration.status()
        if self._calibration.ready and not self._calibration_applied:
            self._gyro_bias = self._calibration.bias_z
            self._calibration_applied = True
            self.get_logger().info(
                f'Gyro calibration ready: z bias={self._gyro_bias:.8f} rad/s '
                f'({math.degrees(self._gyro_bias):.5f} deg/s)')
        changed = status['state'] != self._calibration_last_status
        if changed or stamp_ns-self._calibration_last_report_ns >= 1_000_000_000:
            self._calibration_pub.publish(String(data=json.dumps(status)))
            if changed and status['state'] == 'failed':
                self.get_logger().error(
                    f'Gyro calibration failed: {status["reason"]}; restart needed')
            self._calibration_last_status = status['state']
            self._calibration_last_report_ns = stamp_ns

    def _calibration_cloud_cb(self, msg: PointCloud2):
        if self._awaiting_init or self._calibration_applied or self._calibration.state == 'failed':
            return
        stamp_ns = msg.header.stamp.sec*1_000_000_000+msg.header.stamp.nanosec
        if not msg.header.frame_id or stamp_ns <= 0:
            return
        if self._calibration_frame is not None and self._calibration_frame != msg.header.frame_id:
            self._calibration.fail('lidar_frame_changed')
        else:
            self._calibration_frame = msg.header.frame_id
            self._calibration.add_cloud(stamp_ns, self._scene_points(msg))
        self._report_calibration(stamp_ns)

    def _report_gnss(self, stamp_ns, accepted, reason, nis=None, details=None):
        self._gnss_status.publish(String(data=json.dumps({
            'stamp_s': stamp_ns*1e-9, 'accepted': accepted, 'reason': reason,
            'nis': nis, 'state_stamp_s': (self._last_imu_stamp_ns or 0)*1e-9,
            **(details or {})})))

    def _gnss_cb(self, msg: NavSatFix):
        if not self._use_gnss or self._awaiting_init or self._last_imu_stamp_ns is None:
            return
        stamp_ns = msg.header.stamp.sec*1_000_000_000+msg.header.stamp.nanosec
        try:
            if (msg.status.status < 0 or msg.header.frame_id != self._str_param('gnss_frame_id')
                    or msg.position_covariance_type == NavSatFix.COVARIANCE_TYPE_UNKNOWN):
                raise ValueError('invalid_status_frame_or_covariance')
            altitude = msg.altitude if math.isfinite(msg.altitude) else self._gnss_origin.altitude
            position = self._gnss_origin.to_enu(msg.latitude, msg.longitude, altitude)[:2]
            covariance = np.asarray(msg.position_covariance).reshape(3, 3)[:2, :2].copy()
            if (not np.isfinite(covariance).all() or not np.allclose(covariance, covariance.T)
                    or np.linalg.eigvalsh(covariance).min() <= 0):
                raise ValueError('invalid_covariance')
            if stamp_ns <= self._last_gnss_received:
                raise ValueError('duplicate_or_reordered')
            if stamp_ns > self._last_imu_stamp_ns+int(self._float_param('gnss_max_delay_s')*1e9):
                raise ValueError('future_clock')
            self._last_gnss_received = stamp_ns
            self._gnss_pending.append((stamp_ns, position, covariance))
        except ValueError as error:
            self._report_gnss(stamp_ns, False, str(error))

    def _wheel_cb(self, msg: JointState):
        if not self._use_wheels:
            return
        speeds = []
        for name in self._rear_names:
            if name not in msg.name:
                if not self._warned_wheels:
                    self.get_logger().warn(
                        f'wheel joint {name!r} missing from {list(msg.name)}; '
                        'speed corrections are off')
                    self._warned_wheels = True
                return
            speeds.append(msg.velocity[msg.name.index(name)])

        self._ekf.update_speed(
            body_speed(speeds[0], speeds[1], self._wheel_radius),
            self._speed_var)

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------
    def _step(self):
        """Publish the latest sensor-timed state without repeating its acceleration."""
        if self._calibration_applied and self._calibration_subscription is not None:
            self.destroy_subscription(self._calibration_subscription)
            self._calibration_subscription = None
        if (self._calibration is not None and not self._calibration_applied
                and not self._awaiting_init):
            now = self.get_clock().now().nanoseconds
            self._calibration.tick(now)
            self._report_calibration(now)
        if self._awaiting_init or self._last_imu_stamp_ns is None:
            return

        x, y, yaw = self._ekf.pose
        v, yaw_rate = self._ekf.twist
        stamp = Time(nanoseconds=self._last_imu_stamp_ns).to_msg()

        odom = Odometry()
        odom.header.stamp = stamp
        odom.header.frame_id = self._frame_id
        odom.child_frame_id = self._child_frame_id
        odom.pose.pose.position.x = x
        odom.pose.pose.position.y = y
        odom.pose.pose.orientation = yaw_to_quat(yaw)
        # Body frame, per REP-103: the sim publishes the same magnitude.
        odom.twist.twist.linear = Vector3(x=v, y=0.0, z=0.0)
        odom.twist.twist.angular = Vector3(x=0.0, y=0.0, z=yaw_rate)

        p = self._ekf.P
        odom.pose.covariance[0] = p[IDX_X, IDX_X]
        odom.pose.covariance[7] = p[IDX_Y, IDX_Y]
        odom.pose.covariance[35] = p[IDX_YAW, IDX_YAW]
        odom.twist.covariance[0] = p[IDX_V, IDX_V]
        odom.twist.covariance[35] = p[IDX_YAW_RATE, IDX_YAW_RATE]
        self._odom_pub.publish(odom)

        if self._publish_tf:
            t = TransformStamped()
            t.header.stamp = stamp
            t.header.frame_id = self._frame_id
            t.child_frame_id = self._child_frame_id
            t.transform.translation.x = x
            t.transform.translation.y = y
            t.transform.rotation = yaw_to_quat(yaw)
            self._tf_bc.sendTransform(t)


def main():
    """Entry point."""
    rclpy.init()
    node = EkfNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.try_shutdown()
