"""Checks on arc-length-weighted cross-track error."""

from lhr_metrics.track_error import TrackErrorAccumulator
import pytest


def test_empty_accumulator_reports_nothing():
    acc = TrackErrorAccumulator(2.0)
    assert acc.samples == 0
    assert acc.path_length == 0.0
    assert acc.mean_cte == 0.0
    assert acc.off_track_distance == 0.0


def test_single_sample_reports_its_own_error():
    # No arc length to average over, so the one value stands; reporting
    # 0.0 here would read as "sitting on the centerline".
    acc = TrackErrorAccumulator(2.0)
    acc.add(0.0, 0.0, 3.5)
    assert acc.mean_cte == pytest.approx(3.5)
    assert acc.path_length == 0.0


def test_constant_offset_averages_to_that_offset():
    acc = TrackErrorAccumulator(2.0)
    for x in range(11):
        acc.add(float(x), 0.0, 1.25)
    assert acc.path_length == pytest.approx(10.0)
    assert acc.mean_cte == pytest.approx(1.25)


def test_max_cte_tracks_the_worst_sample():
    acc = TrackErrorAccumulator(2.0)
    for x, cte in enumerate([0.1, 4.2, 0.3]):
        acc.add(float(x), 0.0, cte)
    assert acc.max_cte == pytest.approx(4.2)


def test_off_track_distance_measures_metres_not_samples():
    # Threshold 1.0, error 2.0 held over the last 5 m of a 10 m path.
    acc = TrackErrorAccumulator(1.0)
    for x in range(6):
        acc.add(float(x), 0.0, 0.0)
    for x in range(5, 11):
        acc.add(float(x), 0.0, 2.0)
    assert acc.path_length == pytest.approx(10.0)
    assert acc.off_track_distance == pytest.approx(5.0)
    # The sample count cannot say that: it depends on how often odom
    # happened to fire while the car was out there.
    assert acc.off_track_samples == 6


def _half_bad_path(step: float):
    """Build a 10 m path: zero error for 5 m, then 2.0 m of error."""
    points = [(float(x), 0.0, 0.0) for x in range(6)]
    n = int(round(5.0 / step))
    points += [(5.0 + i * step, 0.0, 2.0) for i in range(n + 1)]
    return points


def test_sampling_density_does_not_change_the_distance_weighted_mean():
    # Same path driven twice, sampled 10x more densely the second time.
    # This is the whole reason the accumulator weights by distance: a
    # sample-weighted mean reports two different laps here.
    sparse = _half_bad_path(1.0)
    dense = _half_bad_path(0.1)

    means = []
    for points in (sparse, dense):
        acc = TrackErrorAccumulator(1.0)
        for x, y, cte in points:
            acc.add(x, y, cte)
        assert acc.path_length == pytest.approx(10.0)
        means.append(acc.mean_cte)

    assert means[0] == pytest.approx(1.0)
    assert means[1] == pytest.approx(means[0])

    # And the number it replaces does not survive the same comparison.
    sample_means = [
        sum(cte for _, _, cte in points) / len(points)
        for points in (sparse, dense)
    ]
    assert sample_means[0] == pytest.approx(1.0)
    assert sample_means[1] > 1.7
