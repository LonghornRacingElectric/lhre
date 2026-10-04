"""Checks on the Mid-360 beam pattern."""

import math

from lhr_lidar_sim.mid360 import (
    apply_range_model, beam_angles, Mid360Config, unit_directions)
import numpy as np
import pytest


def test_point_rate_sets_points_per_frame():
    cfg = Mid360Config()
    assert cfg.points_per_frame() == 20_000
    assert Mid360Config(frame_rate_hz=20.0).points_per_frame() == 10_000


def test_elevation_stays_inside_the_datasheet_field_of_view():
    cfg = Mid360Config()
    _, el = beam_angles(cfg, 0.0)
    assert np.min(el) >= math.radians(cfg.el_min_deg) - 1e-9
    assert np.max(el) <= math.radians(cfg.el_max_deg) + 1e-9
    # The field of view is asymmetric and mostly above the horizon,
    # which is the whole reason mount pitch matters.
    assert np.degrees(np.min(el)) == pytest.approx(-7.0, abs=0.2)
    assert np.degrees(np.max(el)) == pytest.approx(52.0, abs=0.2)


def test_azimuth_covers_the_full_circle():
    az, _ = beam_angles(Mid360Config(), 0.0)
    counts, _ = np.histogram(az, bins=36, range=(0.0, 2.0 * np.pi))
    # Every 10 degree sector gets beams: 360 degrees means 360.
    assert np.all(counts > 0)


def test_pattern_does_not_repeat_between_frames():
    cfg = Mid360Config()
    az0, el0 = beam_angles(cfg, 0.0)
    # One whole second later, an exact multiple of the frame period: a
    # repeating scanner would land on the same directions here.
    az1, el1 = beam_angles(cfg, 1.0)
    assert not np.allclose(az0, az1)
    assert not np.allclose(el0, el1)
    moved = np.hypot(az0 - az1, el0 - el1)
    assert np.median(moved) > 1e-3


def test_directions_are_unit_vectors():
    az, el = beam_angles(Mid360Config(), 0.25, count=5_000)
    dirs = unit_directions(az, el)
    assert dirs.shape == (5_000, 3)
    assert np.allclose(np.linalg.norm(dirs, axis=1), 1.0)


def test_uniform_profile_spreads_elevation_more_evenly():
    # The sine sweep lingers at its turning points, so beams bunch at
    # the edges of the field of view. The triangle sweep does not. The
    # mount study is re-run against 'uniform' precisely because this
    # difference is a guess about the real device.
    spreads = {}
    for profile in ('rosette', 'uniform'):
        cfg = Mid360Config(elevation_profile=profile)
        _, el = beam_angles(cfg, 0.0)
        counts, _ = np.histogram(np.degrees(el), bins=20,
                                 range=(-7.0, 52.0))
        fractions = counts / counts.sum()
        spreads[profile] = fractions.std()
    assert spreads['uniform'] < spreads['rosette']


def test_unknown_elevation_profile_is_rejected():
    with pytest.raises(ValueError, match='elevation_profile'):
        beam_angles(Mid360Config(elevation_profile='spiral'), 0.0)


def test_range_noise_is_seeded_and_repeats():
    cfg = Mid360Config(range_noise_std_m=0.05)
    base = np.full(1_000, 10.0)
    hit = np.ones(1_000, dtype=bool)

    first = base.copy()
    apply_range_model(first, hit, cfg, np.random.default_rng(7))
    second = base.copy()
    apply_range_model(second, hit, cfg, np.random.default_rng(7))
    third = base.copy()
    apply_range_model(third, hit, cfg, np.random.default_rng(8))

    assert np.array_equal(first, second)
    assert not np.array_equal(first, third)
    assert first.std() == pytest.approx(0.05, rel=0.15)


def test_returns_beyond_max_range_are_dropped():
    cfg = Mid360Config(range_noise_std_m=0.0, max_range_m=40.0)
    ranges = np.array([1.0, 39.0, 41.0, 100.0])
    hit = np.ones(4, dtype=bool)
    kept = apply_range_model(ranges, hit, cfg, np.random.default_rng(0))
    assert kept.tolist() == [True, True, False, False]


def test_dropout_removes_roughly_its_fraction():
    cfg = Mid360Config(range_noise_std_m=0.0, dropout_rate=0.3)
    ranges = np.full(20_000, 10.0)
    hit = np.ones(20_000, dtype=bool)
    kept = apply_range_model(ranges, hit, cfg, np.random.default_rng(1))
    assert kept.mean() == pytest.approx(0.7, abs=0.02)
