"""Share competition cone envelopes and an explicitly idealized surface profile."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ConeSpec:
    """Describe a nominal WEMAS cone; profile dimensions remain approximations."""

    height_m: float
    base_width_m: float
    mass_kg: float
    color: tuple
    stripe_color: tuple
    stripe_bands: tuple
    base_height_m: float = 0.01

    @property
    def radius_m(self):
        """Return the ideal body radius within the square footprint."""
        return self.base_width_m / 2.0


# Envelope, mass, colors and stripe counts: FSG Driverless Specification 2026,
# Table 1. Stripe heights and the 10 mm base thickness are approximations;
# the rule table does not define the molded body profile or band positions.
CONE_SPECS = {
    'blue': ConeSpec(.325, .228, .45, (0., .2, 1.), (1., 1., 1.), ((.16, .21),)),
    'yellow': ConeSpec(.325, .228, .45, (1., 1., 0.), (0., 0., 0.), ((.16, .21),)),
    'orange_small': ConeSpec(.325, .228, .45, (1., .35, 0.), (1., 1., 1.),
                             ((.16, .21),)),
    'orange_large': ConeSpec(.505, .285, 1.05, (1., .35, 0.), (1., 1., 1.),
                             ((.18, .23), (.33, .38))),
}


def cone_triangles(spec, segments=32):
    """Return triangles and face colors in a ground-origin cone frame."""
    faces, colors = [], []

    def add(vertices, color):
        faces.append(tuple(vertices))
        colors.append(color)

    levels = sorted({spec.base_height_m, spec.height_m,
                     *(z for band in spec.stripe_bands for z in band)})
    for low, high in zip(levels, levels[1:]):
        color = spec.color
        if any(a <= (low + high) / 2.0 <= b for a, b in spec.stripe_bands):
            color = spec.stripe_color
        for i in range(segments):
            angles = (2 * math.pi * i / segments, 2 * math.pi * (i + 1) / segments)

            def point(z, angle):
                radius = spec.radius_m * (1.0 - z / spec.height_m)
                return (radius * math.cos(angle), radius * math.sin(angle), z)
            a, b = (point(low, angle) for angle in angles)
            c, d = (point(high, angle) for angle in angles)
            add((a, b, d), color)
            if high < spec.height_m:
                add((a, d, c), color)
    half = spec.base_width_m / 2.0
    corners = [(-half, -half), (half, -half), (half, half), (-half, half)]
    bottom = [(x, y, 0.) for x, y in corners]
    top = [(x, y, spec.base_height_m) for x, y in corners]
    for i in range(4):
        j = (i + 1) % 4
        add((bottom[i], bottom[j], top[j]), spec.color)
        add((bottom[i], top[j], top[i]), spec.color)
    add((top[0], top[1], top[2]), spec.color)
    add((top[0], top[2], top[3]), spec.color)
    add((bottom[2], bottom[1], bottom[0]), spec.color)
    add((bottom[3], bottom[2], bottom[0]), spec.color)
    return faces, colors


def start_finish_gates(left, right):
    """Place two orange gate pairs outside the first boundary pair."""
    if len(left) < 2 or len(right) < 2:
        return []
    center = tuple((a + b) / 2 for a, b in zip(left[0], right[0]))
    tangent = tuple((a + b) / 2 - c for a, b, c in zip(left[1], right[1], center))
    norm = math.hypot(*tangent)
    if norm == 0:
        return []
    tx, ty = (v / norm for v in tangent)
    half_width = math.dist(left[0], right[0]) / 2 + 1.0
    return [(center[0] + along * tx - side * half_width * ty,
             center[1] + along * ty + side * half_width * tx)
            for along in (-1.0, 1.0) for side in (-1, 1)]
