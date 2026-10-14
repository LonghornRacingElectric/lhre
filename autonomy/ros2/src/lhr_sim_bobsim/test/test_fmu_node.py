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
