"""
Orion's physical parameters, loaded from one YAML file.

Every node that needs vehicle geometry calls ``load_vehicle()`` instead of
carrying its own constant, so the controller, the simulators and the
generated Gazebo model cannot drift apart. ``config/vehicle.yaml`` records
where each number came from.
"""

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Optional, Tuple

import yaml

CONFIG_NAME = 'vehicle.yaml'
SCHEMA = 'lhr.vehicle.v1'

Vec3 = Tuple[float, float, float]


@dataclass(frozen=True)
class StereoCamera:
    """One ideal rectified ZED 2i rig; mount pose is in base_link."""

    model: str
    lens_focal_length_mm: float
    position_m: Vec3
    rpy_rad: Vec3
    baseline_m: float
    image_width: int
    image_height: int
    update_rate_hz: float
    horizontal_fov_rad: float

    @property
    def fx_px(self) -> float:
        """Pixel focal length used by both rendering and calibration."""
        return self.image_width / (2.0 * math.tan(self.horizontal_fov_rad / 2.0))

    @property
    def fy_px(self) -> float:
        """The ideal simulation uses square pixels."""
        return self.fx_px

    @property
    def cx_px(self) -> float:
        """Horizontal principal point at image center."""
        return self.image_width / 2.0

    @property
    def cy_px(self) -> float:
        """Vertical principal point at image center."""
        return self.image_height / 2.0


@dataclass(frozen=True)
class Vehicle:
    """Vehicle parameters. Lengths in m, masses in kg, angles in rad."""

    name: str
    wheelbase_m: float
    track_m: float
    wheel_radius_m: float
    wheel_width_m: float
    body_length_m: float
    body_width_m: float
    body_height_m: float
    chassis_z_m: float
    chassis_mass_kg: float
    wheel_mass_kg: float
    max_steer_rad: float
    max_steer_rate_rad_s: float
    max_speed_mps: float
    lidar_position_m: Vec3
    stereo_camera: StereoCamera

    @property
    def chassis_center_x_m(self) -> float:
        """Chassis link origin, halfway along the wheelbase."""
        return self.wheelbase_m / 2.0

    @property
    def half_track_m(self) -> float:
        return self.track_m / 2.0


def _source_tree_config() -> Path:
    return Path(__file__).resolve().parents[1] / 'config' / CONFIG_NAME


def config_path() -> Path:
    """
    Locate vehicle.yaml.

    The installed copy when the package is built; the source-tree copy when
    this module is imported straight from the checkout (the model generator
    does that). Anything else is an error worth seeing, not a guess.
    """
    try:
        from ament_index_python.packages import (
            PackageNotFoundError, get_package_share_directory)
    except ImportError:
        installed = None
    else:
        try:
            installed = Path(get_package_share_directory('lhr_vehicle'))
        except PackageNotFoundError:
            installed = None

    candidates = []
    if installed is not None:
        candidates.append(installed / 'config' / CONFIG_NAME)
    candidates.append(_source_tree_config())
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f'{CONFIG_NAME} not found; looked in: '
        + ', '.join(str(c) for c in candidates))


def _vec3(values) -> Vec3:
    x, y, z = (float(v) for v in values)
    return (x, y, z)


def _camera_number(raw, field) -> float:
    value = raw.get(field)
    if (type(value) not in (int, float) or not math.isfinite(value)):
        raise ValueError(f'stereo_camera.{field}: require a finite number')
    return float(value)


def _camera_vec3(raw, field) -> Vec3:
    values = raw.get(field)
    if (not isinstance(values, (list, tuple)) or len(values) != 3
            or any(type(v) not in (int, float) or not math.isfinite(v)
                   for v in values)):
        raise ValueError(f'stereo_camera.{field}: require three finite numbers')
    return tuple(float(v) for v in values)


def _load_stereo_camera(raw) -> StereoCamera:
    if not isinstance(raw, dict):
        raise ValueError('stereo_camera: require a configuration mapping')
    if raw.get('model') != 'zed2i':
        raise ValueError('stereo_camera.model: require zed2i')
    lens = _camera_number(raw, 'lens_focal_length_mm')
    if lens != 2.1:
        raise ValueError('stereo_camera.lens_focal_length_mm: require 2.1')
    baseline = _camera_number(raw, 'baseline_m')
    rate = _camera_number(raw, 'update_rate_hz')
    fov = _camera_number(raw, 'horizontal_fov_deg')
    for field, value in (('baseline_m', baseline), ('update_rate_hz', rate)):
        if value <= 0:
            raise ValueError(f'stereo_camera.{field}: require a positive value')
    if not 0 < fov < 180:
        raise ValueError('stereo_camera.horizontal_fov_deg: require 0 < FOV < 180')
    for field in ('image_width', 'image_height'):
        if type(raw.get(field)) is not int or raw[field] <= 0:
            raise ValueError(f'stereo_camera.{field}: require a positive integer')
    return StereoCamera(
        model='zed2i', lens_focal_length_mm=lens,
        position_m=_camera_vec3(raw, 'position_m'),
        rpy_rad=_camera_vec3(raw, 'rpy_rad'), baseline_m=baseline,
        image_width=raw['image_width'], image_height=raw['image_height'],
        update_rate_hz=rate, horizontal_fov_rad=math.radians(fov),
    )


def load_vehicle(path: Optional[Path] = None) -> Vehicle:
    """Parse ``vehicle.yaml`` (or ``path``) into a ``Vehicle``."""
    source = Path(path) if path is not None else config_path()
    with open(source, encoding='utf-8') as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f'{source}: expected a mapping at the top level')
    if raw.get('schema') != SCHEMA:
        raise ValueError(
            f'{source}: expected schema {SCHEMA!r}, got {raw.get("schema")!r}')

    geometry = raw['geometry']
    mass = raw['mass']
    steering = raw['steering']
    limits = raw['limits']
    sensors = raw['sensors']

    return Vehicle(
        name=str(raw['vehicle']['name']),
        wheelbase_m=float(geometry['wheelbase_m']),
        track_m=float(geometry['track_m']),
        wheel_radius_m=float(geometry['wheel_radius_m']),
        wheel_width_m=float(geometry['wheel_width_m']),
        body_length_m=float(geometry['body_length_m']),
        body_width_m=float(geometry['body_width_m']),
        body_height_m=float(geometry['body_height_m']),
        chassis_z_m=float(geometry['chassis_z_m']),
        chassis_mass_kg=float(mass['chassis_kg']),
        wheel_mass_kg=float(mass['wheel_kg']),
        max_steer_rad=float(steering['max_angle_rad']),
        max_steer_rate_rad_s=float(steering['max_rate_rad_s']),
        max_speed_mps=float(limits['max_speed_mps']),
        lidar_position_m=_vec3(sensors['lidar']['position_m']),
        stereo_camera=_load_stereo_camera(sensors.get('stereo_camera')),
    )
