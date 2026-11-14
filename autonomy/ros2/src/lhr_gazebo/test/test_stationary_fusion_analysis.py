"""A stable but displaced estimate is inaccurate without temporal jitter."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest


def test_fixed_bias_is_not_reported_as_jitter():
    path = Path(__file__).parents[1]/'scripts/analyze_stationary_fusion.py'
    spec = importlib.util.spec_from_file_location('fusion_analysis', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    times = np.linspace(0, 10, 101)
    truth = np.column_stack([times, times*0, times*0, times*0])
    estimate = truth.copy()
    estimate[:, 1] = 1
    result = module.summarize(estimate, truth, 0, 10)
    assert result['position_error_p95_m'] == 1
    assert result['temporal_radial_scatter_p95_m'] == 0
    assert result['position_step_max_m'] == 0
    with pytest.raises(ValueError, match='coverage'):
        module.summarize(estimate[:3], truth, 0, 10)
