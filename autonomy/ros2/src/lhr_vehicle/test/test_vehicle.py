"""Sanity checks on the checked-in vehicle.yaml."""

import math

from lhr_vehicle import config_path, load_vehicle


def test_config_is_the_source_tree_copy_when_not_installed():
    assert config_path().name == 'vehicle.yaml'


def test_loads_orion():
    veh = load_vehicle()
    assert veh.name == 'orion'
    assert 1.4 < veh.wheelbase_m < 1.7
    assert 1.1 < veh.track_m < 1.3
    assert 0.15 < veh.wheel_radius_m < 0.25


def test_steering_limit_is_physical():
    veh = load_vehicle()
    assert 0.0 < veh.max_steer_rad < math.pi / 2
    assert veh.max_steer_rate_rad_s > 0.0


def test_sensors_sit_on_the_car():
    veh = load_vehicle()
    lx, ly, lz = veh.lidar_position_m
    assert -0.5 < lx < veh.wheelbase_m + 1.0
    assert abs(ly) < veh.half_track_m
    assert 0.0 < lz < 1.5
