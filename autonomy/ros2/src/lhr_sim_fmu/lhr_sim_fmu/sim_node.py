"""Publish the full BobLib FMU through the simulator clock, odometry and TF interface."""

import json
import math
from pathlib import Path

from lhr_sim_bobsim.plant import dependency_paths
from lhr_sim_fmu.plant import FmuPlant
from lhr_sim_kinematic.sim_node import SimKinematic
from lhr_vehicle import load_vehicle, vehicle_sha256
import rclpy
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.time import Time
from rosgraph_msgs.msg import Clock
from std_msgs.msg import String


class SimFmu(SimKinematic):
    """Own a co-simulation instance and publish only accepted model states."""

    def __init__(self):
        super().__init__('sim_fmu')
        self.declare_parameter('external_clock', False)
        self.declare_parameter('fmu_path', '')
        self.declare_parameter('pedal_gain', .1)
        self.declare_parameter('command_timeout_sec', .5)
        self._clock_origin_ns = None
        self._last_clock_ns = None
        if self.get_parameter('external_clock').value:
            if self._publish_clock:
                raise ValueError('external_clock and publish_clock are mutually exclusive')
            self._timer.cancel()
            clock_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
            self.create_subscription(Clock, '/clock', self._clock_cb, clock_qos)
        self._target_speed, self._target_steer = 0., 0.
        self._command_time = -math.inf
        root, _ = dependency_paths()
        artifact = self.get_parameter('fmu_path').value or root.parent/'out/fmu/OrionFMI.fmu'
        self._plant = FmuPlant(load_vehicle(), (self._x, self._y, self._yaw), Path(artifact),
                               pedal_gain=self.get_parameter('pedal_gain').value)
        provenance = {**self._plant.provenance, 'plant': 'fmu',
                      'fmu_pedal_gain': self.get_parameter('pedal_gain').value,
                      'command_timeout_sec': self.get_parameter('command_timeout_sec').value,
                      'autonomy_vehicle_sha256': vehicle_sha256()}
        qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self._provenance_pub = self.create_publisher(String, '/lhr/sim/provenance', qos)
        self._provenance_pub.publish(String(data=json.dumps(provenance)))
        self.get_logger().info('BobLib co-simulation ready: '+json.dumps(provenance))

    def _clock_cb(self, message):
        """Integrate fixed steps up to Gazebo time, never ahead of its clock."""
        stamp = Time.from_msg(message.clock).nanoseconds
        if self._last_clock_ns is not None and stamp < self._last_clock_ns:
            raise RuntimeError('Gazebo clock reset; restart the FMU with the world')
        self._last_clock_ns = stamp
        if self._clock_origin_ns is None:
            if stamp == 0:
                return
            self._clock_origin_ns = self._sim_ns = stamp
            self._publish_state(message.clock)
        while self._sim_ns + self._step_ns <= stamp:
            self._advance()
            self._sim_ns += self._step_ns
            self._publish_state(Time(nanoseconds=self._sim_ns).to_msg())

    def _cmd_cb(self, message):
        if not all(math.isfinite(v) for v in (message.drive.speed, message.drive.steering_angle)):
            self._target_speed = 0.
            return
        self._target_speed = message.drive.speed
        self._target_steer = message.drive.steering_angle
        self._command_time = self._plant.time

    def _advance(self):
        target = self._target_speed
        if self._plant.time-self._command_time > self.get_parameter('command_timeout_sec').value:
            target = 0.
        (self._x, self._y, self._yaw, self._v, self._vy,
         self._yaw_rate) = self._plant.step(self._dt, target, self._target_steer)

    def destroy_node(self):
        """Free native model resources before releasing ROS entities."""
        try:
            if hasattr(self, '_plant'):
                self._plant.close()
        finally:
            result = super().destroy_node()
        return result


def main():
    """Run the FMU plant; integration errors remain failing process exits."""
    rclpy.init()
    node = None
    try:
        node = SimFmu()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.try_shutdown()
