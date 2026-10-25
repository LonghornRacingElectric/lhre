"""Check empirical thinning is seeded, bounded and specific to small cones."""

from lhr_lidar_sim.return_profile import overcast_profile, thin_small_cones
import numpy as np
import pytest


def test_small_cone_thinning_preserves_ground_and_large_cones():
    ranges = np.array([5., 5., 5.])
    hit, on_cone = np.ones(3, dtype=bool), np.array([True, True, False])
    directions = np.array([[1., 0., 0.], [0., 1., 0.], [-1., 0., 0.]])
    cones = np.array([[5., 0., .325, .228], [0., 5., .505, .285]])

    class Reject:
        """Force every eligible return to drop without modifying other surfaces."""

        def random(self, count):
            """Return the maximum draw."""
            return np.ones(count)
    result = thin_small_cones(ranges, hit, on_cone, directions, cones, Reject(),
                              'acceptance_overcast')
    assert result.tolist() == [False, True, True]
    assert hit.all()


def test_thinning_is_reproducible_and_tracks_profile_probability():
    count = 20000
    args = (np.full(count, 10.3), np.ones(count, dtype=bool),
            np.ones(count, dtype=bool), np.tile([1., 0., 0.], (count, 1)),
            np.array([[10.3, 0.]]))
    a = thin_small_cones(*args, np.random.default_rng(7), 'acceptance_overcast')
    b = thin_small_cones(*args, np.random.default_rng(7), 'acceptance_overcast')
    assert np.array_equal(a, b)
    model = overcast_profile()
    expected = np.interp(10.3, model['range_m'], model['survival_probability'])
    assert a.mean() == pytest.approx(expected, abs=.015)
    assert all(0 <= p <= 1 for p in model['survival_probability'])
    assert {r['run'] for r in model['training']}.isdisjoint(
        {r['run'] for r in model['held_out']})
