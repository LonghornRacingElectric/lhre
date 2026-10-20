import os
from pathlib import Path

import numpy as np
import pytest

SIM = Path(__file__).resolve().parents[1]
VEHICLE = Path(os.environ.get("BOBSIM_VEHICLE", SIM.parent / "vehicle" / "vehicle.yml"))


def _outside_aero_grids(path):
    from engines.dynpy.parameters import load_reduced_vehicle_parameters

    aero = load_reduced_vehicle_parameters(path).aero
    if aero is None:
        return []
    heights = aero.ride_heights(0.0, np.eye(3))
    found = []
    for axle, height, grid in (
        ("front", float(np.mean(heights[:2])), aero.front_grid),
        ("rear", float(np.mean(heights[2:])), aero.rear_grid),
    ):
        if not grid[0] <= height <= grid[-1]:
            found.append(
                f"The {axle} static ride height is {height:.4f} m. "
                f"The {axle} CFD grid covers {grid[0]:.4f} m to {grid[-1]:.4f} m."
            )
    return found


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="Our aero ride-height references sit above both CFD grids. See docs/bobsim.md#aero-ride-height-references.",
)
def test_static_ride_heights_lie_inside_the_aero_grids():
    outside = _outside_aero_grids(VEHICLE)
    assert not outside, "DynPy and BobLib clamp the aero map at the grid edge. " + " ".join(outside)
