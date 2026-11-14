"""Check the coordinate and clearance calculations used in causal replay reports."""

import importlib.util
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


def analysis():
    source = Path(__file__).parents[1] / 'scripts/analyze_cone_path.py'
    spec = importlib.util.spec_from_file_location('cone_path_analysis', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_known_world_point_is_forward_in_a_rotated_vehicle_frame():
    module = analysis()
    pose = np.array([2., 3., 0., 0., 0., math.sin(math.pi/4), math.cos(math.pi/4)])
    np.testing.assert_allclose(module.local([[2., 5.]], pose), [[2., 0.]], atol=1e-12)


def test_pose_interpolation_handles_quaternion_sign_and_rejects_extrapolation():
    module = analysis()
    q = [0., 0., 0., 1.]
    rows = np.array([[1., 0., 0., 0., *q], [2., 2., 0., 0., *(-np.array(q))]])
    pose = module.pose_at(rows, 1.5)
    assert pose[0] == 1
    np.testing.assert_allclose(pose[3:], q)
    assert module.pose_at(rows, .9) is None
    assert module.pose_at(rows, 2.1) is None


def test_clearance_distinguishes_safe_corridor_from_cone_overlap():
    module = analysis()
    vehicle = SimpleNamespace(rear=-.3, front=1.8, half_width=.8)
    path = np.array([[0., 0.], [8., 0.]])
    safe = module.horizon_clearance(path, vehicle, np.array([[3., 3.]]), np.array([.2]))
    blocked = module.horizon_clearance(path, vehicle, np.array([[3., .5]]), np.array([.2]))
    assert safe == pytest.approx(2.0)
    assert blocked < 0


def test_path_horizon_starts_at_the_vehicle_projection():
    module = analysis()
    path = np.array([[-2., 0.], [3., 0.], [3., 4.]])
    trimmed = module.trim_path(path, np.array([0., 1.]))
    np.testing.assert_allclose(trimmed, [[0., 0.], [3., 0.], [3., 4.]])
