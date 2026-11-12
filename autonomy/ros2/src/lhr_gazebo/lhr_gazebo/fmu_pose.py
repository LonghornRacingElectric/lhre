"""Move Gazebo's sensor-only car and measure observed pose age at sensor stamps."""

from collections import deque
import json
import math
import time

from nav_msgs.msg import Odometry
import rclpy
from rclpy.clock import Clock, ClockType
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, qos_profile_sensor_data, QoSProfile
from ros_gz_interfaces.msg import Entity
from ros_gz_interfaces.srv import ControlWorld, SetEntityPose
from sensor_msgs.msg import Image, PointCloud2
from std_msgs.msg import String
from tf2_msgs.msg import TFMessage


def stamp_ns(stamp):
    """Convert a ROS stamp without floating point cancellation."""
    return stamp.sec * 1_000_000_000 + stamp.nanosec


def pose_key(pose):
    """Describe the planar model origin in either a pose or transform message."""
    position = pose.position if hasattr(pose, 'position') else pose.translation
    orientation = pose.orientation if hasattr(pose, 'orientation') else pose.rotation
    sign = -1. if orientation.w < 0 else 1.
    return (position.x, position.y, sign*orientation.z, sign*orientation.w)


class FmuPose(Node):
    """Keep at most one pose request in flight; never build a stale request queue."""

    def __init__(self):
        super().__init__('fmu_pose')
        self.declare_parameter('world_name', 'fsae_world')
        self.declare_parameter('truth_topic', '/lhr/vehicle/odom_truth')
        self.declare_parameter('request_timeout_sec', 5.0)
        self.declare_parameter('lockstep', True)
        self.declare_parameter('step_sec', .02)
        self.declare_parameter('physics_step_sec', .001)
        self._lockstep = self.get_parameter('lockstep').value
        step = self.get_parameter('step_sec').value
        physics = self.get_parameter('physics_step_sec').value
        if not (math.isfinite(step) and math.isfinite(physics) and 0 < physics <= step < .1):
            raise ValueError('Require 0 < physics_step_sec <= step_sec < 0.1')
        self._step_ns = round(step*1e9)
        self._physics_ns = round(physics*1e9)
        if self._physics_ns <= 0 or self._step_ns % self._physics_ns:
            raise ValueError('FMU step must be an integer number of Gazebo physics steps')
        self._control = (self.create_client(
            ControlWorld, '/world/' + self.get_parameter('world_name').value + '/control')
            if self._lockstep else None)
        self._control_future = None
        self._cycle_target_ns = None
        self._bootstrap = True
        self._applied_ns = self._sent_ns = None
        self._ready = not self._lockstep
        self._cycle_started = time.monotonic()
        self._client = self.create_client(
            SetEntityPose, '/world/' + self.get_parameter('world_name').value + '/set_pose')
        self._pending = self._future = None
        self._history = deque(maxlen=10000)
        self._observed = deque(maxlen=1000)
        self._started = time.monotonic()
        self._request_started = None
        self._last_log = {}
        self._lag = {}
        self._unmatched = {}
        self._lag_pub = self.create_publisher(String, '/lhr/sim/fmu_pose_lag', 100)
        if self._lockstep:
            qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
            self.create_subscription(String, '/lhr/sim/provenance', self._provenance, qos)
        self.create_subscription(Odometry, self.get_parameter('truth_topic').value,
                                 self._truth, 1)
        self.create_subscription(TFMessage, '/lhr/sim/render_pose', self._render_pose, 10)
        for topic, kind in (('/lhr/lidar/points', PointCloud2),
                            ('/lhr/sensor/zed2i/left/image_rect', Image),
                            ('/lhr/sensor/zed2i/right/image_rect', Image)):
            self.create_subscription(kind, topic, lambda msg, t=topic: self._sensor(msg, t),
                                     qos_profile_sensor_data)
        # A stopped Gazebo clock must not hide a missing service or lost response.
        self.create_timer(.02, self._send, clock=Clock(clock_type=ClockType.STEADY_TIME))

    def _truth(self, message):
        self._history.append((stamp_ns(message.header.stamp), pose_key(message.pose.pose)))
        if self._lockstep and self._cycle_target_ns is not None:
            stamp = stamp_ns(message.header.stamp)
            if self._bootstrap:
                if not 0 < stamp <= 2*self._physics_ns:
                    raise RuntimeError('Unexpected initial Gazebo clock in lockstep mode')
                self._cycle_target_ns = stamp
                self._bootstrap = False
            if stamp > self._cycle_target_ns:
                raise RuntimeError('Gazebo ran ahead of the coordinated FMU step')
            if stamp < self._cycle_target_ns:
                return
        self._pending = message
        self._send()

    def _provenance(self, message):
        if json.loads(message.data).get('plant') == 'fmu':
            self._ready = True
            self._send()

    def _step_world(self, target_ns):
        request = ControlWorld.Request()
        request.world_control.pause = True
        request.world_control.multi_step = (
            2 if self._cycle_target_ns is None
            else (target_ns-self._cycle_target_ns)//self._physics_ns)
        self._cycle_target_ns = target_ns
        self._cycle_started = time.monotonic()
        self._control_future = self._control.call_async(request)
        self._control_future.add_done_callback(lambda _: self._send())

    def _send(self):
        timeout = self.get_parameter('request_timeout_sec').value
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('request_timeout_sec must be finite and positive')
        if self._future is not None:
            if not self._future.done():
                if time.monotonic() - self._request_started > timeout:
                    raise RuntimeError('Gazebo set_pose response timed out')
                return
            if not self._future.result().success:
                raise RuntimeError('Gazebo rejected FMU pose')
            self._future = None
            self._applied_ns = self._sent_ns
        if self._lockstep and self._control_future is not None:
            if not self._control_future.done():
                if time.monotonic() - self._cycle_started > timeout:
                    raise RuntimeError('Gazebo world-control response timed out')
                return
            if not self._control_future.result().success:
                raise RuntimeError('Gazebo rejected the coordinated world step')
            self._control_future = None
        services_ready = self._client.service_is_ready() and (
            not self._lockstep or self._control.service_is_ready())
        if not services_ready or not self._ready:
            if time.monotonic() - self._started > timeout:
                raise RuntimeError('Gazebo pose/control service or FMU readiness is unavailable')
            return
        if self._lockstep:
            if self._cycle_target_ns is None:
                self._step_world(self._physics_ns)
                return
            if self._applied_ns == self._cycle_target_ns:
                self._step_world(self._cycle_target_ns + self._step_ns)
                return
            if time.monotonic() - self._cycle_started > timeout:
                raise RuntimeError('FMU did not produce the coordinated Gazebo state')
        if self._pending is None:
            return
        request = SetEntityPose.Request()
        request.entity.name, request.entity.type = 'fsae_vehicle', Entity.MODEL
        request.pose = self._pending.pose.pose
        self._sent_ns = stamp_ns(self._pending.header.stamp)
        self._pending = None
        self._future = self._client.call_async(request)
        self._request_started = time.monotonic()
        self._future.add_done_callback(lambda _: self._send())

    def _render_pose(self, message):
        for transform in message.transforms:
            if transform.child_frame_id != 'fsae_vehicle':
                continue
            key = pose_key(transform.transform)
            # Observe Gazebo's actual model pose, not the set_pose queue acknowledgement.
            rendered_ns = stamp_ns(transform.header.stamp)
            matched = next((stamp for stamp, pose in reversed(self._history)
                            if stamp <= rendered_ns
                            and all(abs(a-b) < 1e-6 for a, b in zip(key, pose))), None)
            if matched is not None:
                self._observed.append((stamp_ns(transform.header.stamp), matched))

    def _sensor(self, message, topic):
        stamp = stamp_ns(message.header.stamp)
        matched = next(((render, state) for render, state in reversed(self._observed)
                        if render <= stamp), None)
        if matched is None:
            self._lag_pub.publish(String(data=json.dumps({
                'topic': topic, 'sensor_stamp_sec': stamp*1e-9, 'matched': False,
                'lockstep': self._lockstep})))
            self._unmatched[topic] = self._unmatched.get(topic, 0)+1
            if stamp-self._last_log.get(topic, -10**18) >= 1_000_000_000:
                self._last_log[topic] = stamp
                self.get_logger().warning(
                    f'FMU pose lag unavailable sensor={topic}: no matched Gazebo pose')
            return
        render, state = matched
        lag = (stamp-state)*1e-9
        self._lag_pub.publish(String(data=json.dumps({
            'topic': topic, 'sensor_stamp_sec': stamp*1e-9, 'matched': True,
            'state_stamp_sec': state*1e-9, 'lag_sec': lag,
            'observation_age_sec': (stamp-render)*1e-9, 'lockstep': self._lockstep})))
        count, total, maximum = self._lag.get(topic, (0, 0., -math.inf))
        self._lag[topic] = count+1, total+lag, max(maximum, lag)
        if stamp-self._last_log.get(topic, -10**18) >= 1_000_000_000:
            self._last_log[topic] = stamp
            self.get_logger().info(
                f'FMU pose lag sensor={topic} samples={count+1} '
                f'mean_s={(total+lag)/(count+1):.6f} max_s={max(maximum, lag):.6f} '
                f'latest_s={lag:.6f} observation_age_s={(stamp-render)*1e-9:.6f} '
                f'unmatched={self._unmatched.get(topic, 0)}')


def main():
    """Run the pose follower and fail if Gazebo stops accepting poses."""
    rclpy.init()
    node = FmuPose()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
