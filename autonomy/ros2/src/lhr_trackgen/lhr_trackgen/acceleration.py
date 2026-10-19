"""FSAE 2026 acceleration cone layout (D.9.1 and DD.1/DD.4.2)."""

SMALL_CONE_SIZE = (0.228, 0.228, 0.325)
LARGE_CONE_SIZE = (0.285, 0.285, 0.505)


def generate_acceleration_track():
    """
    Return cone-center (x, y) lists grouped by marker namespace.

    Travel is along +x, with start at x=0 and finish at x=75 m.
    The clear width between cone bases is 4.9 m. The exit lane ends
    at x=150 m, the maximum permitted stopping distance after finishing.
    Entry length (12 m) and gate offsets (+/-1 m) are simulation choices;
    the rules specify their colors, not these exact positions or counts.
    """
    half_width = 4.9 / 2.0
    small_y = half_width + SMALL_CONE_SIZE[1] / 2.0
    large_y = half_width + LARGE_CONE_SIZE[1] / 2.0

    # Evenly distribute 13 intervals over 75 m (~5.77 m, approximately 6 m).
    boundary_x = [75.0 * i / 13 for i in range(14)]
    lane_x = [-12.0, -6.0] + [float(x) for x in range(81, 150, 6)] + [150.0]

    return {
        'left_cones': [(x, small_y) for x in boundary_x],
        'right_cones': [(x, -small_y) for x in boundary_x],
        'orange_small_cones': [(x, y) for x in lane_x for y in (small_y, -small_y)],
        'orange_large_cones': [
            (x, y) for x in (-1.0, 1.0, 74.0, 76.0)
            for y in (large_y, -large_y)
        ],
    }
