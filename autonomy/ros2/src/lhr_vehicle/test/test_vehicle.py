"""Sanity checks on the checked-in vehicle.yaml and its loader."""

import hashlib
import math
from pathlib import Path

import lhr_vehicle
from lhr_vehicle import config_path, load_vehicle, vehicle_sha256
import pytest
import yaml


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


def test_vehicle_sha256_matches_the_file_on_disk():
    expected = hashlib.sha256(config_path().read_bytes()).hexdigest()
    assert vehicle_sha256() == expected
    assert len(vehicle_sha256()) == 64


def test_vehicle_sha256_changes_with_the_content(tmp_path):
    a = tmp_path / 'a.yaml'
    b = tmp_path / 'b.yaml'
    a.write_text('wheelbase_m: 1.5494\n', encoding='utf-8')
    b.write_text('wheelbase_m: 1.5495\n', encoding='utf-8')
    assert vehicle_sha256(a) != vehicle_sha256(b)


def test_stereo_optics_describe_a_wide_rectified_pair():
    """The lens label must produce pixel calibration, not a millimeter K value."""
    veh = load_vehicle()
    assert hasattr(veh, 'stereo_camera')
    camera = veh.stereo_camera
    assert camera.model == 'zed2i'
    assert camera.lens_focal_length_mm == 2.1
    assert camera.baseline_m == 0.12
    assert (camera.image_width, camera.image_height) == (1280, 720)
    assert camera.update_rate_hz == 30.0
    assert camera.position_m == (1.8, 0.0, 0.65)
    assert camera.rpy_rad == (0.0, 0.0, 0.0)
    assert camera.fx_px == pytest.approx(448.132824)
    assert camera.fy_px == camera.fx_px
    assert (camera.cx_px, camera.cy_px) == (640.0, 360.0)


@pytest.mark.parametrize('field, value', [
    ('baseline_m', 0), ('baseline_m', -1), ('baseline_m', float('inf')),
    ('baseline_m', float('nan')), ('baseline_m', True),
    ('update_rate_hz', 0), ('update_rate_hz', -1),
    ('update_rate_hz', float('nan')), ('update_rate_hz', float('inf')),
    ('image_width', 0), ('image_width', -1), ('image_width', 1280.5),
    ('image_width', True), ('image_width', '1280'),
    ('image_height', 0), ('image_height', -1), ('image_height', 720.5),
    ('image_height', False), ('image_height', '720'),
    ('horizontal_fov_deg', 0), ('horizontal_fov_deg', 180),
    ('horizontal_fov_deg', float('nan')), ('horizontal_fov_deg', float('inf')),
    ('position_m', [1, 2]), ('position_m', [1, 2, 'bad']),
    ('position_m', [1, 2, float('inf')]), ('position_m', None),
    ('rpy_rad', [0]), ('rpy_rad', [0, None, 0]),
    ('rpy_rad', [0, 0, float('nan')]),
    ('model', 'zedm'), ('lens_focal_length_mm', 4.6),
])
def test_invalid_stereo_configuration_names_the_field(tmp_path, field, value):
    """Malformed calibration must fail before entering the model or TF graph."""
    raw = yaml.safe_load(config_path().read_text())
    camera = raw['sensors'].setdefault('stereo_camera', {
        'model': 'zed2i', 'lens_focal_length_mm': 2.1,
        'baseline_m': 0.12, 'position_m': [1.8, 0, 0.65],
        'rpy_rad': [0, 0, 0], 'image_width': 1280, 'image_height': 720,
        'update_rate_hz': 30.0, 'horizontal_fov_deg': 110.0,
    })
    camera[field] = value
    source = tmp_path / 'vehicle.yaml'
    source.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match=field):
        load_vehicle(source)


def test_missing_stereo_configuration_is_an_explicit_error(tmp_path):
    """A stale installed vehicle configuration cannot silently omit the camera."""
    raw = yaml.safe_load(config_path().read_text())
    raw['sensors'].pop('stereo_camera', None)
    source = tmp_path / 'vehicle.yaml'
    source.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match='stereo_camera'):
        load_vehicle(source)
