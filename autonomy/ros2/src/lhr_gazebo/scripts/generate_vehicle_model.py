#!/usr/bin/env python3
"""
Render models/fsae_vehicle/model.sdf from lhr_vehicle's vehicle.yaml.

Run after editing vehicle.yaml and commit the regenerated model.sdf:

    python3 src/lhr_gazebo/scripts/generate_vehicle_model.py

``--check`` renders without writing and fails if the committed model.sdf is
out of date; lhr_gazebo's tests run the same comparison so CI catches a
YAML edit that forgot the regenerate step.
"""

import argparse
import math
from pathlib import Path
from string import Template
import sys

PKG_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PKG_DIR.parent
TEMPLATE = PKG_DIR / 'templates' / 'model.sdf.in'
OUTPUT = PKG_DIR / 'models' / 'fsae_vehicle' / 'model.sdf'
# Always the source-tree YAML: an installed copy can predate the edit.
VEHICLE_YAML = SRC_DIR / 'lhr_vehicle' / 'config' / 'vehicle.yaml'

# The STL meshes were fitted by eye to the original placeholder geometry;
# their scale and offset move with the real dimensions relative to these.
MESH_REF_WHEELBASE_M = 1.6
MESH_REF_WHEEL_RADIUS_M = 0.2
BODY_MESH_REF_SCALE = 1.7
BODY_MESH_REF_OFFSET = (-0.07, -0.02)
TIRE_MESH_REF_SCALE = 2.17
TIRE_MESH_REF_OFFSET = (-0.59, -0.20, -0.61)


def load_source_vehicle():
    try:
        from lhr_vehicle import load_vehicle
    except ImportError:
        sys.path.insert(0, str(SRC_DIR / 'lhr_vehicle'))
        from lhr_vehicle import load_vehicle
    return load_vehicle(VEHICLE_YAML)


def inner_steer_limit(veh) -> float:
    """
    Inner-wheel angle at full lock.

    ``max_steer_rad`` is the bicycle-model center angle. The per-wheel
    steering joints see the Ackermann inner angle, which is larger, so
    that is what their limit has to allow.
    """
    turn_radius = veh.wheelbase_m / math.tan(veh.max_steer_rad)
    return math.atan(veh.wheelbase_m / (turn_radius - veh.half_track_m))


def _fmt(value: float) -> str:
    return f'{value:.4f}'.rstrip('0').rstrip('.')


def render(veh) -> str:
    body_ratio = veh.wheelbase_m / MESH_REF_WHEELBASE_M
    tire_ratio = veh.wheel_radius_m / MESH_REF_WHEEL_RADIUS_M
    m, L, W, H = (veh.chassis_mass_kg, veh.body_length_m,
                  veh.body_width_m, veh.body_height_m)
    mw, r, w = veh.wheel_mass_kg, veh.wheel_radius_m, veh.wheel_width_m
    cx, cz = veh.chassis_center_x_m, veh.chassis_z_m
    lx, ly, lz = veh.lidar_position_m

    values = {
        'wheelbase': veh.wheelbase_m,
        'track': veh.track_m,
        'half_track': veh.half_track_m,
        'wheel_radius': r,
        'wheel_width': w,
        'chassis_x': cx,
        'chassis_z': cz,
        'chassis_mass': m,
        'chassis_ixx': m / 12.0 * (W * W + H * H),
        'chassis_iyy': m / 12.0 * (L * L + H * H),
        'chassis_izz': m / 12.0 * (L * L + W * W),
        'body_length': L,
        'body_width': W,
        'body_height': H,
        'body_mesh_scale': BODY_MESH_REF_SCALE * body_ratio,
        'body_mesh_dx': BODY_MESH_REF_OFFSET[0] * body_ratio,
        'body_mesh_dy': BODY_MESH_REF_OFFSET[1] * body_ratio,
        'lidar_dx': lx - cx,
        'lidar_dy': ly,
        'lidar_dz': lz - cz,
        'wheel_mass': mw,
        'wheel_i_spin': 0.5 * mw * r * r,
        'wheel_i_perp': 0.25 * mw * r * r + mw * w * w / 12.0,
        'tire_mesh_scale': TIRE_MESH_REF_SCALE * tire_ratio,
        'tire_mesh_dx': TIRE_MESH_REF_OFFSET[0] * tire_ratio,
        'tire_mesh_dy': TIRE_MESH_REF_OFFSET[1] * tire_ratio,
        'tire_mesh_dz': TIRE_MESH_REF_OFFSET[2] * tire_ratio,
        'max_steer': veh.max_steer_rad,
        'joint_steer_limit': inner_steer_limit(veh),
        'max_steer_rate': veh.max_steer_rate_rad_s,
    }
    template = Template(TEMPLATE.read_text(encoding='utf-8'))
    return template.substitute({k: _fmt(v) for k, v in values.items()})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument('--check', action='store_true',
                        help='fail if the committed model.sdf is out of date')
    args = parser.parse_args(argv)

    veh = load_source_vehicle()
    rendered = render(veh)
    rel = OUTPUT.relative_to(SRC_DIR.parent)

    if args.check:
        current = OUTPUT.read_text(encoding='utf-8') if OUTPUT.exists() else ''
        if current != rendered:
            print(f'{rel} is out of date with {VEHICLE_YAML.name}; '
                  f'run {Path(__file__).name} and commit the result')
            return 1
        print(f'{rel} is in sync with {VEHICLE_YAML.name}')
        return 0

    OUTPUT.write_text(rendered, encoding='utf-8', newline='\n')
    print(f'wrote {rel} (wheelbase {veh.wheelbase_m} m, track {veh.track_m} m, '
          f'center lock {veh.max_steer_rad} rad, '
          f'joint limit {inner_steer_limit(veh):.4f} rad)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
