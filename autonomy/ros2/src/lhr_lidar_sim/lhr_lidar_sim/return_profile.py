"""Apply a recorded small-cone return-density profile without changing other surfaces."""

from functools import lru_cache
import json
from pathlib import Path

import numpy as np


@lru_cache(maxsize=1)
def overcast_profile():
    """Load the derived profile pinned to the October 4 acceptance recordings."""
    path = Path(__file__).parent / 'patterns' / 'acceptance_overcast.json'
    return json.loads(path.read_text())


def thin_small_cones(ranges, hit, on_cone, directions, cones, rng, profile):
    """Thin only small-cone returns using seeded empirical range probabilities."""
    if profile == 'baseline':
        return hit
    if profile != 'acceptance_overcast':
        raise ValueError(f'Unknown return profile: {profile}')
    eligible = hit & on_cone
    if cones.size and cones.shape[1] == 4:
        # The profile was measured on small cones, not large orange cones.
        small = cones[cones[:, 2] <= .325, :2]
        indices = np.flatnonzero(eligible)
        surface_xy = directions[indices, :2] * ranges[indices, None]
        selected = np.zeros(len(indices), dtype=bool)
        for center in small:
            selected |= np.linalg.norm(surface_xy - center, axis=1) <= .228 / np.sqrt(2.)
        eligible[indices] &= selected
    indices = np.flatnonzero(eligible)
    model = overcast_profile()
    survival = np.interp(ranges[indices], model['range_m'], model['survival_probability'])
    result = hit.copy()
    result[indices] &= rng.random(len(indices)) < survival
    return result
