#!/usr/bin/env python3
"""
Stop the car when the software stack stops being trustworthy.

The software half of the PDR safety loop: the checks in monitors.py, a
heartbeat that only advances while they pass, and a latched trip onto the
emergency topic mission_manager already handles (latch.py). The rule from
the PDR: software may only ever keep the stop loop from firing by actively
saying all is well. It can never block a stop, so silence, a crash or a
hang all read as a fault to whatever listens to the heartbeat.
"""

from ackermann_msgs.msg import AckermannDriveStamped
from diagnostic_msgs.msg import DiagnosticStatus, KeyValue
from lhr_safety.latch import SafetyLatch
from lhr_safety.monitors import Fault, SafetyConfig, SafetyMonitor
from lhr_vehicle import load_vehicle
from nav_msgs.msg import Odometry
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import PointCloud2
from std_msgs.msg import Bool, String, UInt32


class SafetyNode(Node):
    """Run the fault checks and act on them."""

    def __init__(self):
        super().__init__('safety_node')

        vehicle = load_vehicle()
        defaults = SafetyConfig(wheelbase_m=vehicle.wheelbase_m,
                                max_speed_mps=vehicle.max_speed_mps)
        self.declare_parameter('rate_hz', 20.0)
        self.declare_parameter('lidar_topic', '/lhr/lidar/points')
        self.declare_parameter('monitor_lidar', defaults.monitor_lidar)
        self.declare_parameter('lidar_timeout_s', defaults.lidar_timeout_s)
        self.declare_parameter('odom_timeout_s', defaults.odom_timeout_s)
        self.declare_parameter('cmd_timeout_s', defaults.cmd_timeout_s)
        self.declare_parameter('max_position_std_m', defaults.max_position_std_m)
        self.declare_parameter('max_speed_factor', defaults.max_speed_factor)
        self.declare_parameter('geofence_radius_m', defaults.geofence_radius_m)
        self.declare_parameter('steer_min_speed_mps', defaults.steer_min_speed_mps)
        self.declare_parameter('steer_yaw_rate_tol', defaults.steer_yaw_rate_tol)
        self.declare_parameter('steer_rel_tol', defaults.steer_rel_tol)
        self.declare_parameter('steer_window_s', defaults.steer_window_s)

        def param(name):
            return self.get_parameter(name).value

        self._config = SafetyConfig(
            wheelbase_m=vehicle.wheelbase_m,
            max_speed_mps=vehicle.max_speed_mps,
            monitor_lidar=bool(param('monitor_lidar')),
            lidar_timeout_s=float(param('lidar_timeout_s')),
            odom_timeout_s=float(param('odom_timeout_s')),
            cmd_timeout_s=float(param('cmd_timeout_s')),
            max_position_std_m=float(param('max_position_std_m')),
            max_speed_factor=float(param('max_speed_factor')),
            geofence_radius_m=float(param('geofence_radius_m')),
            steer_min_speed_mps=float(param('steer_min_speed_mps')),
            steer_yaw_rate_tol=float(param('steer_yaw_rate_tol')),
            steer_rel_tol=float(param('steer_rel_tol')),
            steer_window_s=float(param('steer_window_s')),
        )
        self._monitor = SafetyMonitor(self._config, self._now())
        self._latch = SafetyLatch()
        self._mission_state = ''

        self._emergency_pub = self.create_publisher(
            Bool, '/lhr/mission/emergency', 10)
        self._heartbeat_pub = self.create_publisher(
            UInt32, '/lhr/safety/heartbeat', 10)
        self._status_pub = self.create_publisher(
            DiagnosticStatus, '/lhr/safety/status', 10)

        if self._config.monitor_lidar:
            # Only arrival matters, so skip deserializing a cloud that can
            # be close to a megabyte.
            self.create_subscription(
                PointCloud2, param('lidar_topic'), self._lidar_cb,
                qos_profile_sensor_data, raw=True)
        self.create_subscription(
            Odometry, '/lhr/vehicle/odom', self._odom_cb, 10)
        self.create_subscription(
            AckermannDriveStamped, '/lhr/vehicle/cmd', self._cmd_cb, 10)
        self.create_subscription(
            String, '/lhr/mission/status', self._status_cb, 10)
        self.create_subscription(
            Bool, '/lhr/mission/reset', self._reset_cb, 10)

        self.create_timer(1.0 / float(param('rate_hz')), self._tick)
        self.get_logger().info(
            f'Safety monitor up (lidar check '
            f'{"on" if self._config.monitor_lidar else "off"}, geofence '
            f'{self._config.geofence_radius_m:.0f} m)')

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def _lidar_cb(self, _msg):
        self._monitor.on_lidar(self._now())

    def _cmd_cb(self, msg: AckermannDriveStamped):
        self._monitor.on_command(self._now(), msg.drive.steering_angle)

    def _odom_cb(self, msg: Odometry):
        pose, twist = msg.pose, msg.twist.twist
        self._monitor.on_odom(
            self._now(),
            pose.pose.position.x, pose.pose.position.y,
            twist.linear.x, twist.linear.y, twist.angular.z,
            pose.covariance[0], pose.covariance[7])

    def _status_cb(self, msg: String):
        self._mission_state = msg.data

    def _reset_cb(self, msg: Bool):
        if not msg.data:
            return
        cleared = self._latch.reset()
        if cleared != Fault.NONE:
            self.get_logger().info(f'Reset clears latched {cleared.name}')

    def _tick(self):
        fault = self._monitor.check(self._now())
        decision = self._latch.step(fault, self._mission_state)
        if decision.newly_armed:
            self._monitor.anchor_geofence()
        if decision.newly_tripped:
            self.get_logger().error(
                f'Safety trip: {fault.name} in {self._mission_state}')
        if decision.emergency:
            self._emergency_pub.publish(Bool(data=True))
        if decision.heartbeat is not None:
            self._heartbeat_pub.publish(UInt32(data=decision.heartbeat))
        self._status_pub.publish(self._status(decision, fault))

    def _status(self, decision, live: Fault) -> DiagnosticStatus:
        status = DiagnosticStatus()
        status.name = 'lhr_safety'
        status.hardware_id = 'safety_node'
        status.level = (DiagnosticStatus.OK if decision.shown == Fault.NONE
                        else DiagnosticStatus.ERROR)
        status.message = decision.shown.name
        status.values = [
            KeyValue(key='code', value=str(int(decision.shown))),
            KeyValue(key='latched', value=str(decision.emergency)),
            # While latched, message shows what tripped; this is what the
            # checks see now.
            KeyValue(key='live', value=live.name),
            KeyValue(key='heartbeat', value=str(self._latch.counter)),
            KeyValue(key='mission_state', value=self._mission_state),
        ]
        return status


def main():
    """Entry point."""
    rclpy.init()
    node = SafetyNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.try_shutdown()


if __name__ == '__main__':
    main()
