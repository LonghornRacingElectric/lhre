"""Published FSAE skidpad dimensions and illustrative orange lane placement."""

INNER_RADIUS = 7.625
OUTER_RADIUS = 10.625
CENTER_SEPARATION = 18.25
MID_RADIUS = (INNER_RADIUS + OUTER_RADIUS) / 2.0


def generate_skidpad_lanes():
    """Return small orange lanes and large orange gates, in the layout frame."""
    # Lane lengths/spacing are simulation choices, not prescribed cone coordinates.
    small = [(x, float(y)) for x in (-1.5, 1.5)
             for y in (*range(-20, -7, 2), *range(8, 35, 2))]
    large = [(x, y) for x in (-1.5, 1.5) for y in (-7.0, 7.0)]
    return small, large
