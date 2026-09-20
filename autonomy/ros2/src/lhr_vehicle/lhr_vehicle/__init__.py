"""
Orion's physical parameters, loaded from one YAML file.

Every node that needs vehicle geometry calls ``load_vehicle()`` instead of
carrying its own constant, so the controller, the simulators and the
generated Gazebo model cannot drift apart. ``config/vehicle.yaml`` records
where each number came from.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import yaml

CONFIG_NAME = 'vehicle.yaml'
SCHEMA = 'lhr.vehicle.v1'

Vec3 = Tuple[float, float, float]


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
    )
