#!/usr/bin/env python3
"""
Inject one fault into a running Gazebo stack, to test lhr_safety (#117).

Sim only, and only started when the launch sets fault:=<kind>. A topic
fault works by relaying: the launch remaps the producer onto an input topic
and this node republishes it, dropping, freezing or corrupting it once the
fault starts. A process fault kills or pauses a node instead. Either way
the node publishes one event on /lhr/sim/fault at the moment the fault
starts (and another when a timed fault ends), which the analysis measures
from.

The fault starts fault_after_s after the mission first reports DRIVING, so
the car is at speed, and lasts fault_duration_s (0 = until shutdown).
"""

from dataclasses import dataclass
import math
import signal
import subprocess
from typing import Optional

from ackermann_msgs.msg import AckermannDriveStamped
from nav_msgs.msg import Odometry
import rclpy
from rclpy.node import Node
from rclpy.qos import (DurabilityPolicy, qos_profile_sensor_data,
                       QoSProfile, ReliabilityPolicy)
from sensor_msgs.msg import PointCloud2
from std_msgs.msg import String


@dataclass(frozen=True)
class FaultKind:
    """What one fault kind does and which safety check should catch it."""

    expect: str
    # Topic faults: relay in -> out. The launch remaps the producer to `src`
    # (or a consumer to `dst`) to put this node in the middle.
    src: Optional[str] = None
    dst: Optional[str] = None
    msg: Optional[type] = None
    # Process faults: pkill -f pattern and the signal to send.
    process: Optional[str] = None
    sig: Optional[int] = None


EKF = 'lib/lhr_state_estimation/ekf_node'
CONTROLLER = 'lib/lhr_control/pursuit_node'
ODOM = '/lhr/vehicle/odom'
CMD = '/lhr/vehicle/cmd'

FAULTS = {
    'ekf_kill': FaultKind(
        'ODOM_STALE', process=EKF, sig=signal.SIGKILL),
    'controller_kill': FaultKind(
        'CMD_STALE', process=CONTROLLER, sig=signal.SIGKILL),
    'controller_pause': FaultKind(
        'CMD_STALE', process=CONTROLLER, sig=signal.SIGSTOP),
    'odom_nan': FaultKind(
        'EKF_DIVERGED', ODOM + '_raw', ODOM, Odometry),
    'odom_spike': FaultKind(
        'EKF_DIVERGED', ODOM + '_raw', ODOM, Odometry),
    'lidar_drop': FaultKind(
        'LIDAR_STALE', '/lhr/lidar/points_raw', '/lhr/lidar/points',
        PointCloud2),
    'cmd_nan': FaultKind(
        'CMD_INVALID', CMD + '_raw', CMD, AckermannDriveStamped),
    # Only the actuators see the frozen command; safety_node still reads
    # the real one, so the yaw rate stops matching it.
    'steer_freeze': FaultKind(
        'STEER_TRACKING', CMD, CMD + '_actuated', AckermannDriveStamped),
}


class FaultClock:
    """Decide when the fault starts and ends. ROS-free so it is testable."""

    def __init__(self, after_s: float, duration_s: float):
        self.after_s = after_s
        self._after = after_s
        self._duration = duration_s
        self._driving_since: Optional[float] = None
        self.started_at: Optional[float] = None
        self.ended = False

    def update(self, now: float, driving: bool) -> str:
        """Return 'start', 'end' or '' for this instant."""
        if self._driving_since is None:
            if driving:
                self._driving_since = now
            return ''
        if self.started_at is None:
            if now - self._driving_since >= self._after:
                self.started_at = now
                return 'start'
            return ''
        if (not self.ended and self._duration > 0.0
                and now - self.started_at >= self._duration):
            self.ended = True
            return 'end'
        return ''

    @property
    def active(self) -> bool:
        return self.started_at is not None and not self.ended


class FaultInjector(Node):
    """Relay or kill according to the fault kind, and announce it."""

    def __init__(self):
        super().__init__('fault_injector')
        self.declare_parameter('fault', '')
        self.declare_parameter('fault_after_s', 30.0)
        self.declare_parameter('fault_duration_s', 0.0)
        self._kind = self.get_parameter('fault').value
        if self._kind not in FAULTS:
            raise ValueError(
                f'Unknown fault {self._kind!r}, expected one of {sorted(FAULTS)}')
        self._fault = FAULTS[self._kind]
        # rclpy's Node keeps its own clock in an attribute named _clock.
        self._fault_clock = FaultClock(
            float(self.get_parameter('fault_after_s').value),
            float(self.get_parameter('fault_duration_s').value))
        self._driving = False
        self._spiked = False
        self._frozen_steer: Optional[float] = None

        latched = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE,
                             durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self._event_pub = self.create_publisher(String, '/lhr/sim/fault', latched)
        self.create_subscription(String, '/lhr/mission/status',
                                 self._status_cb, 10)

        f = self._fault
        if f.src is not None:
            # The LiDAR relay passes clouds through as bytes; nothing here
            # needs to look inside one.
            raw = f.msg is PointCloud2
            qos = qos_profile_sensor_data if raw else 10
            self._out = self.create_publisher(f.msg, f.dst, qos)
            self.create_subscription(f.msg, f.src, self._relay_cb, qos, raw=raw)

        self.create_timer(0.02, self._tick)
        self.get_logger().warning(
            f'Fault injector armed: {self._kind} (expect {f.expect}), '
            f'{self._fault_clock.after_s:.1f} s after DRIVING')

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def _status_cb(self, msg: String):
        self._driving = msg.data == 'DRIVING'

    def _tick(self):
        edge = self._fault_clock.update(self._now(), self._driving)
        if not edge:
            return
        f = self._fault
        if f.process is not None:
            if edge == 'start':
                self._signal(f.sig)
            elif f.sig == signal.SIGSTOP:
                self._signal(signal.SIGCONT)
        self._event_pub.publish(String(data=f'{edge} {self._kind} {f.expect}'))
        self.get_logger().warning(f'Fault {edge}: {self._kind}')

    def _signal(self, sig: int):
        subprocess.run(['pkill', f'-{int(sig)}', '-f', self._fault.process],
                       check=False)

    def _relay_cb(self, msg):
        if self._fault_clock.active:
            msg = self._corrupt(msg)
            if msg is None:
                return
        self._out.publish(msg)

    def _corrupt(self, msg):
        """Return the faulted message, or None to drop it."""
        kind = self._kind
        if kind == 'lidar_drop':
            return None
        if kind == 'odom_nan':
            msg.pose.pose.position.x = math.nan
        elif kind == 'odom_spike' and not self._spiked:
            # One bad sample, the case the held-sample check exists for.
            self._spiked = True
            msg.twist.twist.linear.x = 410.0
        elif kind == 'cmd_nan':
            msg.drive.steering_angle = math.nan
        elif kind == 'steer_freeze':
            if self._frozen_steer is None:
                self._frozen_steer = msg.drive.steering_angle
            msg.drive.steering_angle = self._frozen_steer
        return msg


def main():
    """Entry point."""
    rclpy.init()
    node = FaultInjector()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.try_shutdown()


if __name__ == '__main__':
    main()
