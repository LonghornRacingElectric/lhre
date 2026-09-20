#!/usr/bin/env python3
"""
Render models/fsae_vehicle/model.sdf from lhr_vehicle's vehicle.yaml.

Run after editing vehicle.yaml and commit the regenerated model.sdf:

    python3 src/lhr_gazebo/scripts/generate_vehicle_model.py
"""

from pathlib import Path
from string import Template
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
MODEL_DIR = SCRIPT_DIR.parent / 'models' / 'fsae_vehicle'
TEMPLATE = MODEL_DIR / 'model.sdf.in'
OUTPUT = MODEL_DIR / 'model.sdf'

# The STL meshes were fitted by eye to the original placeholder geometry;
# their scale and offset move with the real dimensions relative to these.
MESH_REF_WHEELBASE_M = 1.6
MESH_REF_WHEEL_RADIUS_M = 0.2
BODY_MESH_REF_SCALE = 1.7
BODY_MESH_REF_OFFSET = (-0.07, -0.02)
TIRE_MESH_REF_SCALE = 2.17
TIRE_MESH_REF_OFFSET = (-0.59, -0.20, -0.61)


def _load_vehicle():
    try:
        from lhr_vehicle import load_vehicle
    except ImportError:
        sys.path.insert(0, str(SCRIPT_DIR.parents[1] / 'lhr_vehicle'))
        from lhr_vehicle import load_vehicle
    return load_vehicle()


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
    ix, iy, iz = veh.imu_position_m

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
        'imu_dx': ix - cx,
        'imu_dy': iy,
        'imu_dz': iz - cz,
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
        'max_steer_rate': veh.max_steer_rate_rad_s,
    }
    template = Template(TEMPLATE.read_text(encoding='utf-8'))
    return template.substitute({k: _fmt(v) for k, v in values.items()})


def main() -> int:
    veh = _load_vehicle()
    OUTPUT.write_text(render(veh), encoding='utf-8')
    print(f'wrote {OUTPUT.relative_to(SCRIPT_DIR.parents[2])} '
          f'(wheelbase {veh.wheelbase_m} m, track {veh.track_m} m, '
          f'max_steer {veh.max_steer_rad} rad)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
