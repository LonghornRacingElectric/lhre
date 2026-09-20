"""The committed model.sdf must be what the generator renders from vehicle.yaml."""

import importlib.util
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parents[1]


def _generator():
    spec = importlib.util.spec_from_file_location(
        'generate_vehicle_model',
        PKG_DIR / 'scripts' / 'generate_vehicle_model.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_model_sdf_is_in_sync_with_vehicle_yaml():
    gen = _generator()
    rendered = gen.render(gen.load_source_vehicle())
    committed = gen.OUTPUT.read_text(encoding='utf-8')
    assert committed == rendered, (
        'models/fsae_vehicle/model.sdf is out of date: '
        'run scripts/generate_vehicle_model.py and commit the result')


def test_model_sdf_uses_lf_line_endings():
    gen = _generator()
    assert b'\r' not in gen.OUTPUT.read_bytes()


def test_steering_joint_limit_admits_the_inner_ackermann_angle():
    gen = _generator()
    veh = gen.load_source_vehicle()
    assert gen.inner_steer_limit(veh) > veh.max_steer_rad
