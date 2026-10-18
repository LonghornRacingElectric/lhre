"""The FMU plant behind the shared simulator interface."""

import math

import pytest

pytest.importorskip('fmpy')
fmu_plant = pytest.importorskip('lhr_sim_bobsim.fmu_plant')
try:
    fmu_plant.find_fmu()
except FileNotFoundError:
    pytest.skip('OrionFMI.fmu not built (make -C simulation fmu)', allow_module_level=True)

from ackermann_msgs.msg import AckermannDriveStamped  # noqa: E402, I100
from lhr_sim_bobsim.fmu_node import SimFmu  # noqa: E402
import rclpy  # noqa: E402
from rclpy.parameter import Parameter  # noqa: E402


def test_drives_then_brakes_when_commands_stop_and_stops_on_nan():
    rclpy.init()
    node = SimFmu()
    try:
        command = AckermannDriveStamped()
        command.drive.speed = 4.
        for _ in range(150):
            node._cmd_cb(command)
            node._advance()
        assert node._v > 2.
        assert math.isfinite(node._vy)
        # No new command: after command_timeout_sec the plant brakes to rest.
        for _ in range(200):
            node._advance()
        assert abs(node._v) < .3
        command.drive.speed = math.nan
        node._cmd_cb(command)
        node._advance()
        assert node._plant.restarts == 0
    finally:
        node.destroy_node()
        rclpy.shutdown()


def test_counts_a_reset_and_can_end_the_run_on_it():
    rclpy.init()
    node = SimFmu()
    try:
        assert node._recoveries == {
            'standstill_resets': 0, 'rolling_resets': 0, 'failed_step_restarts': 0}
        node.set_parameters([Parameter('fail_on_recovery', value=True)])
        # What BobLib's motor does when the driveline rings backwards in a stop.
        plant = node._plant
        plant._fmu.setReal([plant._vr['motor.motor.shaftDirection']], [-1.])
        command = AckermannDriveStamped()
        command.drive.speed = 3.
        node._cmd_cb(command)
        node._advance()
        node._cmd_cb(command)
        with pytest.raises(RuntimeError, match='not continuous'):
            node._advance()
        assert node._recoveries['standstill_resets'] == 1
    finally:
        node.destroy_node()
        rclpy.shutdown()
