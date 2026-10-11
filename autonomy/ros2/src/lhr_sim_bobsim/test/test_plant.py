"""Exercise the actual BobSim solver and ROS adapter contracts."""

import math

from ackermann_msgs.msg import AckermannDriveStamped
from lhr_sim_bobsim.plant import BobSimPlant
from lhr_sim_bobsim.sim_node import SimBobSim
from lhr_vehicle import load_vehicle
import numpy as np
import pytest
import rclpy


def test_acceleration_braking_and_rest():
    plant = BobSimPlant(load_vehicle(), (10., 5., .7))
    assert plant.rear_state()[:3] == pytest.approx((10., 5., .7))
    first = plant.step(.02, 4., 0.)
    assert 0 < first[3] < .1
    for _ in range(150):
        moving = plant.step(.02, 4., 0.)
    assert 3.5 < moving[3] < 4.1
    assert moving[0] > 10. and moving[1] > 5.
    for _ in range(150):
        stopped = plant.step(.02, 0., 0.)
    assert stopped[3:] == pytest.approx((0., 0., 0.), abs=.03)
    position = stopped[:3]
    for _ in range(20):
        stopped = plant.step(.02, 0., 0.)
    assert stopped[:3] == pytest.approx(position, abs=1e-6)


def test_cornering_frame_conversion_and_torque_limits():
    geometry = load_vehicle()
    plant = BobSimPlant(geometry, (0., 0., 0.))
    plant.state[3] = 6.
    for _ in range(100):
        rear = plant.step(.02, 6., .12)
    assert rear[2] > .1 and rear[1] > .1 and rear[5] > 0
    assert rear[4] == pytest.approx(plant.state[4] + plant.rear_x * plant.state[5])
    assert rear[0] == pytest.approx(plant.state[0] + plant.rear_x * math.cos(rear[2]))
    force = sum(t / r for t, r in zip(plant.last_torques, plant.parameters.wheel_radius_m))
    assert force <= plant.parameters.peak_drive_force_n
    assert np.isfinite(plant.state).all()
    before = plant.steering
    plant.step(.001, 100., 100.)
    assert abs(plant.steering - before) <= geometry.max_steer_rate_rad_s * .001 + 1e-12
    assert abs(plant.steering) <= geometry.max_steer_rad


def test_expired_and_invalid_ros_commands_brake():
    rclpy.init()
    node = SimBobSim()
    try:
        command = AckermannDriveStamped()
        command.drive.speed = 4.
        node._cmd_cb(command)
        for _ in range(20):
            node._advance()
        moving = node._v
        assert moving > .2
        for _ in range(150):
            node._advance()
        assert node._v < .03
        command.drive.speed = math.nan
        node._cmd_cb(command)
        assert node._target_speed == 0.
    finally:
        node.destroy_node()
        rclpy.shutdown()
