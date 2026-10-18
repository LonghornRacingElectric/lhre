"""Share competition cone envelopes and an explicitly idealized surface profile."""

from dataclasses import dataclass


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
