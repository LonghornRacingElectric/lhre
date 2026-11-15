"""Estimator error is truth at the estimate's stamp, not whatever truth arrived last."""

import math

from lhr_metrics.alignment import TruthAligner
import pytest


def _drive(aligner, stamps, speed=5.0):
    """Truth for a car on +x at constant speed; return all errors it releases."""
    errors = []
    for stamp in stamps:
        errors += aligner.add_truth(stamp, speed * stamp, 0.0, 0.0)
    return errors


def test_late_truth_is_not_scored_as_error():
    # The estimate is exact, but its truth arrives 0.12 s later: at 5 m/s
    # comparing with the latest sample would have read 0.6 m.
    aligner = TruthAligner()
    _drive(aligner, [0.0, 0.02, 0.04])
    assert aligner.add_estimate(0.16, 0.8, 0.0, 0.0) == []
    errors = _drive(aligner, [0.06 + 0.02 * i for i in range(6)])
    assert len(errors) == 1
    assert errors[0][0] == pytest.approx(0.0, abs=1e-9)


def test_interpolates_between_truth_samples():
    aligner = TruthAligner()
    _drive(aligner, [0.0, 0.1])
    (pos, yaw), = aligner.add_estimate(0.05, 0.25, 0.3, 0.0)
    assert pos == pytest.approx(0.3)
    assert yaw == pytest.approx(0.0)


def test_estimates_older_than_the_buffer_are_skipped():
    aligner = TruthAligner(horizon_s=1.0)
    _drive(aligner, [10.0 + 0.1 * i for i in range(30)])
    assert aligner.add_estimate(10.5, 0.0, 0.0, 0.0) == []


def test_yaw_error_wraps():
    aligner = TruthAligner()
    aligner.add_truth(0.0, 0.0, 0.0, math.pi - 0.05)
    aligner.add_truth(0.1, 0.0, 0.0, math.pi - 0.05)
    (_, yaw), = aligner.add_estimate(0.05, 0.0, 0.0, -math.pi + 0.05)
    assert yaw == pytest.approx(0.1)


def test_pending_estimates_stay_bounded_without_truth():
    aligner = TruthAligner(horizon_s=1.0)
    for i in range(500):
        aligner.add_estimate(0.02 * i, 0.0, 0.0, 0.0)
    assert len(aligner._pending) <= 51
