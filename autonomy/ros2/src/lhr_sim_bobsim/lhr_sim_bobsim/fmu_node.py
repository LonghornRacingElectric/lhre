"""Run BobLib's full vehicle FMU with the existing ROS simulator interface."""

import hashlib
import json
import math

from lhr_sim_bobsim.fmu_plant import FmuPlant
from lhr_sim_bobsim.markers import ForceMarkers
from lhr_sim_kinematic.sim_node import SimKinematic
from lhr_vehicle import vehicle_sha256
import rclpy
from rclpy.qos import DurabilityPolicy, QoSProfile
from rclpy.time import Time
from std_msgs.msg import String


class SimFmu(SimKinematic):
    """Publish the FMU's motion through the shared clock, odometry and TF interface."""

    def __init__(self):
        super().__init__('sim_fmu')
        self.declare_parameter('command_timeout_sec', .5)
        # End the run at the first reset or restart, for runs that must have
        # continuous physics.
        self.declare_parameter('fail_on_recovery', False)
        self._target_speed, self._target_steer = 0., 0.
        self._time, self._command_time = 0., -math.inf
        self._plant = FmuPlant(self._x, self._y, self._yaw, self._max_steer)
        self._markers = ForceMarkers(self, self._child_frame_id)
        fmu = self._plant.fmu_path
        build = fmu.with_suffix('.json')
        provenance = {
            'plant': 'fmu', 'fmu': str(fmu),
            'fmu_sha256': hashlib.sha256(fmu.read_bytes()).hexdigest(),
            # make fmu's record: BobSim SHA, vehicle.yml and FMU hashes, tools.
            'fmu_build': json.loads(build.read_text()) if build.is_file() else None,
            'autonomy_vehicle_sha256': vehicle_sha256(),
        }
        qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self._provenance_pub = self.create_publisher(String, '/lhr/sim/provenance', qos)
        self._provenance_pub.publish(String(data=json.dumps(provenance)))
        self.get_logger().info('BobLib FMU ready: ' + json.dumps(provenance))
        self._recoveries = None
        self._recovery_pub = self.create_publisher(String, '/lhr/sim/recoveries', qos)
        self._report_recoveries()

    def _report_recoveries(self):
        plant = self._plant
        counts = {'standstill_resets': plant.resets, 'rolling_resets': plant.rolling_resets,
                  'failed_step_restarts': plant.restarts}
        if counts == self._recoveries:
            return
        first, self._recoveries = self._recoveries is None, counts
        self._recovery_pub.publish(String(data=json.dumps(counts)))
        if first:
            return
        self.get_logger().info('FMU recovery: ' + json.dumps(counts))
        if self.get_parameter('fail_on_recovery').value:
            raise RuntimeError('The FMU physics was not continuous: ' + json.dumps(counts))

    def _cmd_cb(self, msg):
        # FmuPlant stops on a command that is not finite.
        self._target_speed = msg.drive.speed
        self._target_steer = msg.drive.steering_angle
        self._command_time = self._time

    def _advance(self):
        target = self._target_speed
        if self._time - self._command_time > self.get_parameter('command_timeout_sec').value:
            target = 0.
        (self._x, self._y, self._yaw, self._v,
         self._yaw_rate) = self._plant.step(target, self._target_steer, self._dt)
        self._vy = self._plant.lateral_speed
        self._time += self._dt
        self._report_recoveries()

    def _step(self):
        super()._step()
        stamp = (Time(nanoseconds=self._sim_ns).to_msg() if self._publish_clock
                 else self.get_clock().now().to_msg())
        self._markers.publish(self._plant, stamp)


def main():
    """Run the FMU node."""
    rclpy.init()
    node = SimFmu()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
