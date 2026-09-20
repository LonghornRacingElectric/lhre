"""Sanity checks on the checked-in vehicle.yaml and its loader."""

import math
from pathlib import Path

import lhr_vehicle
from lhr_vehicle import config_path, load_vehicle
import pytest


def test_config_path_without_ament_falls_back_to_the_source_tree(monkeypatch):
    packages = pytest.importorskip('ament_index_python.packages')

    def not_installed(name):
        raise packages.PackageNotFoundError(name)

    monkeypatch.setattr(packages, 'get_package_share_directory', not_installed)

    source_copy = (Path(lhr_vehicle.__file__).resolve().parents[1]
                   / 'config' / 'vehicle.yaml')
    if source_copy.exists():
        # Imported from the checkout: the fallback must be that copy.
        assert config_path() == source_copy
    else:
        # Imported from an install tree: no guessing, a clear error.
        with pytest.raises(FileNotFoundError):
            config_path()


def test_rejects_non_mapping_yaml(tmp_path):
    empty = tmp_path / 'vehicle.yaml'
    empty.write_text('# nothing here\n', encoding='utf-8')
    with pytest.raises(ValueError, match='mapping'):
        load_vehicle(empty)


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


def test_lidar_sits_on_the_car():
    veh = load_vehicle()
    lx, ly, lz = veh.lidar_position_m
    assert -0.5 < lx < veh.wheelbase_m + 1.0
    assert abs(ly) < veh.half_track_m
    assert 0.0 < lz < 1.5
