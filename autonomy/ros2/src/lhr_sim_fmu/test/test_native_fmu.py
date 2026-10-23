"""Opt-in checks on the real compiled Orion model, without substitute physics."""

import math
import os
from pathlib import Path

from lhr_sim_fmu.plant import FmuPlant
from lhr_vehicle import load_vehicle
import pytest

ARTIFACT = os.environ.get('LHR_TEST_FMU')
pytestmark = pytest.mark.skipif(not ARTIFACT, reason='Set LHR_TEST_FMU to a built Orion FMU')


def test_real_wait_launch_turn_stop_and_relaunch():
    plant = FmuPlant(load_vehicle(), (10., 5., .7), Path(ARTIFACT))
    try:
        assert plant.session.reference[1] == 0.
        assert plant.rear_state()[:3] == pytest.approx((10., 5., .7))
        for _ in range(250):
            waiting = plant.step(.02, 0., 0.)
        assert abs(waiting[3]) < .05
        for _ in range(250):
            moving = plant.step(.02, 5., 0.)
        assert 4. < moving[3] < 6.
        assert math.dist(waiting[:2], moving[:2]) > 10.
        for _ in range(100):
            turning = plant.step(.02, 5., .1)
        assert turning[2] > moving[2]+.2
        assert turning[5] > .1
        for _ in range(250):
            stopped = plant.step(.02, 0., 0.)
        assert abs(stopped[3]) < .05
        for _ in range(250):
            restarted = plant.step(.02, 5., 0.)
        assert restarted[3] > 4.
        for _ in range(100):
            right = plant.step(.02, 5., -.1)
        assert right[2] < restarted[2]-.2
        assert right[5] < -.1
    finally:
        plant.close()


def test_compiled_aero_references_are_our_car_instead_of_boblib_defaults():
    from fmpy import read_model_description
    from common.vehicle_io import load_yaml
    from lhr_sim_bobsim.plant import dependency_paths
    data = load_yaml(dependency_paths()[1])
    variables = {v.name: v for v in read_model_description(ARTIFACT).modelVariables}
    for prefix, key in [('FL', 'front_left_ride_height_ref_m'),
                        ('RL', 'rear_left_ride_height_ref_m')]:
        actual = [float(variables[f'pVehicle.pAero.{prefix}_RideHeightRef[{i}]'].start)
                  for i in range(1, 4)]
        assert actual == pytest.approx(data['aero'][key])
