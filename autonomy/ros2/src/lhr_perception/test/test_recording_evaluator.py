"""Check recording frame boundaries and independent gravity leveling."""

from lhr_perception.evaluate_recordings import frame_slices, level_rotation
import numpy as np
import pytest


def test_complete_frames_include_empty_intervals():
    assert frame_slices(np.array([0., .05, .31])) == [(0, 2), (2, 2), (2, 2)]


def test_out_of_order_timestamps_are_rejected():
    with pytest.raises(ValueError):
        frame_slices(np.array([.2, .1]))


def test_gravity_rotation_levels_a_tilted_stationary_capture():
    up = np.array([.1, .2, 1.])
    up /= np.linalg.norm(up)
    rotation = level_rotation(np.array([[0., 0., 0., *up]]))
    assert rotation @ up == pytest.approx([0., 0., 1.])
    assert rotation @ rotation.T == pytest.approx(np.eye(3))
