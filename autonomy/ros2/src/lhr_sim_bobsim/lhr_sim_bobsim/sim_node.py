"""Run the BobSim plant with the existing ROS simulator interface."""

import hashlib
import json
import math
import subprocess

from lhr_sim_bobsim.plant import BobSimPlant, dependency_paths
from lhr_sim_kinematic.sim_node import SimKinematic
from lhr_vehicle import load_vehicle, vehicle_sha256
import rclpy
from rclpy.qos import DurabilityPolicy, QoSProfile
from std_msgs.msg import String


class SimBobSim(SimKinematic):
    """Publish dynamics through the shared clock, odometry and TF interface."""

    def __init__(self):
        super().__init__('sim_bobsim')
        for name, default in (('max_accel', 2.), ('max_decel', 3.),
                              ('speed_gain', 2.), ('command_timeout_sec', .5)):
            self.declare_parameter(name, default)
        self._target_speed, self._target_steer = 0., 0.
        self._command_time = -math.inf
        self._plant = BobSimPlant(load_vehicle(), (self._x, self._y, self._yaw),
                                  max_accel=self.get_parameter('max_accel').value,
                                  max_decel=self.get_parameter('max_decel').value,
                                  speed_gain=self.get_parameter('speed_gain').value)
        root, vehicle_file = dependency_paths()
        sha = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                                      text=True).strip()
        provenance = {
            'plant': 'bobsim', 'dof': 3, 'bobsim_sha': sha,
            'dynamics_vehicle_sha256': hashlib.sha256(vehicle_file.read_bytes()).hexdigest(),
            'autonomy_vehicle_sha256': vehicle_sha256(),
        }
        qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self._provenance_pub = self.create_publisher(String, '/lhr/sim/provenance', qos)
        self._provenance_pub.publish(String(data=json.dumps(provenance)))
        self.get_logger().info('BobSim 3 DOF ready: ' + json.dumps(provenance))

    def _cmd_cb(self, msg):
        if not all(math.isfinite(v) for v in (msg.drive.speed, msg.drive.steering_angle)):
            self._target_speed = 0.
            return
        self._target_speed = msg.drive.speed
        self._target_steer = msg.drive.steering_angle
        self._command_time = self._plant.time

    def _advance(self):
        target = self._target_speed
        if self._plant.time - self._command_time > self.get_parameter('command_timeout_sec').value:
            target = 0.
        (self._x, self._y, self._yaw, self._v, self._vy,
         self._yaw_rate) = self._plant.step(self._dt, target, self._target_steer)


def main():
    """Run the dynamics node."""
    rclpy.init()
    node = SimBobSim()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
