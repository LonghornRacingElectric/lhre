import numpy as np
import pytest

from tools.oval import oval_centerline, oval_layout

TEMPLATE = {"length_m": 60.0, "mean_radius_m": (15.0, 9.0), "radius_rate_m_per_rad": (2.0, -1.5)}
BELT = {"length_m": 40.0, "mean_radius_m": (25.0, 15.0), "radius_rate_m_per_rad": (0.0, 0.0)}


def test_template_matches_the_documented_layout():
    layout = oval_layout(**TEMPLATE)
    corners = layout["corners"]
    assert [corner["turn_deg"] for corner in corners] == pytest.approx([191.5, 168.5], abs=0.05)
    assert [corner["entry_radius_m"] for corner in corners] == pytest.approx([11.7, 11.2], abs=0.05)
    assert [corner["exit_radius_m"] for corner in corners] == pytest.approx([18.3, 6.8], abs=0.05)
    assert [corner["arc_m"] for corner in corners] == pytest.approx([50.1, 26.5], abs=0.05)
    assert layout["straights_m"] == pytest.approx([56.1, 63.3], abs=0.05)


def test_constant_radius_corners_give_the_belt_around_two_pulleys():
    layout = oval_layout(**BELT)
    belt_turn_rad = 2.0 * np.arccos((25.0 - 15.0) / 40.0)
    belt_straight_m = np.sqrt(40.0**2 - (25.0 - 15.0) ** 2)
    assert layout["corners"][1]["turn_deg"] == pytest.approx(np.degrees(belt_turn_rad))
    assert layout["straights_m"] == pytest.approx([belt_straight_m, belt_straight_m])


def test_radius_that_goes_through_zero_is_rejected():
    with pytest.raises(ValueError, match="radius"):
        oval_layout(length_m=60.0, mean_radius_m=(5.0, 5.0), radius_rate_m_per_rad=(5.0, -5.0))


def test_overlapping_corners_are_rejected():
    with pytest.raises(ValueError):
        oval_layout(length_m=5.0, mean_radius_m=(15.0, 9.0), radius_rate_m_per_rad=(0.0, 0.0))


def test_centerline_is_a_counter_clockwise_loop_with_the_layout_length():
    spacing_m = 0.5
    points = oval_centerline(**TEMPLATE, spacing_m=spacing_m)
    segments = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    signed_area = 0.5 * np.sum(points[:, 0] * np.roll(points[:, 1], -1) - np.roll(points[:, 0], -1) * points[:, 1])
    assert segments.sum() == pytest.approx(oval_layout(**TEMPLATE)["lap_m"], rel=1e-3)
    assert segments.max() <= 1.3 * spacing_m
    assert signed_area > 0.0


def test_centerline_starts_at_the_middle_of_corner_0():
    points = oval_centerline(**BELT, spacing_m=0.5)
    assert points[0] == pytest.approx([-25.0, 0.0])
