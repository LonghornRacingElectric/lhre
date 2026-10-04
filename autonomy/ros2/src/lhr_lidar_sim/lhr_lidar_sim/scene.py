#!/usr/bin/env python3
"""
Ray casting against a flat ground plane and upright cones. No ROS.

Everything here works in a frame translated to the sensor origin but
still axis-aligned with the world, which keeps the ground a horizontal
plane instead of a tilted one. The caller rotates the resulting points
into the sensor frame afterwards.

FSAE cone geometry matches the Gazebo world generator: 0.325 m tall,
which is the FSG small-cone height. Changing it in one place and not
the other would make the two simulators disagree about what a cone is.
"""

import numpy as np

CONE_HEIGHT_M = 0.325
# Half the 0.228 m square base of an FSG small cone, as an inscribed
# circle. The silhouette is what decides points-per-cone, so the
# approximation is a slight under-estimate rather than a flattering one.
CONE_BASE_RADIUS_M = 0.114

# Rays almost parallel to the cone surface give a vanishing quadratic
# leading term; below this they are treated as missing it. The grazing
# hit they would contribute is a single point at an extreme incidence
# angle, which a real sensor would be unlikely to return anyway.
_PARALLEL_EPS = 1e-9

# How negative a discriminant may be, relative to the quadratic's own
# scale, and still count as a tangency rather than a miss. See the use
# site: this is what makes an apex-aimed ray resolve the same way on
# every machine.
_TANGENT_REL_EPS = 1e-12


def ground_ranges(dirs: np.ndarray, sensor_z: float) -> tuple:
    """
    Return the range from the sensor to the z=0 plane.

    Returns (ranges, hit). Rays pointing level or up never arrive, which
    is most of them: the Mid-360's field of view runs from -7 to +52
    degrees, so only the bottom 7 degrees can see the ground at all.
    """
    dz = dirs[:, 2]
    ranges = np.full(dirs.shape[0], np.inf)
    going_down = dz < -_PARALLEL_EPS
    # dirs are unit vectors, so the ray parameter *is* the range.
    ranges[going_down] = -sensor_z / dz[going_down]
    return ranges, going_down & (ranges > 0.0)


def _candidate_rays(dirs: np.ndarray, ax: float, ay: float,
                    radius: float) -> np.ndarray:
    """
    Return indices of rays whose azimuth could reach this cone.

    A cone 10 m away is about 1.3 degrees wide, so this discards over
    99% of a 20,000-beam frame before any quadratic is solved. Without
    it the full cross product of beams and cones would be solved every
    frame for no benefit.
    """
    horizontal = float(np.hypot(ax, ay))
    if horizontal <= radius:
        return np.arange(dirs.shape[0])

    half_width = float(np.arcsin(radius / horizontal))
    # One beam spacing of slack, so a cone edge is never clipped by the
    # cull itself rather than by geometry.
    margin = half_width * 0.5 + 1e-3
    cone_az = float(np.arctan2(ay, ax))
    ray_az = np.arctan2(dirs[:, 1], dirs[:, 0])
    delta = np.abs((ray_az - cone_az + np.pi) % (2.0 * np.pi) - np.pi)
    return np.flatnonzero(delta <= half_width + margin)


def cone_ranges(dirs: np.ndarray, cones_xy: np.ndarray, sensor_z: float,
                height: float = CONE_HEIGHT_M,
                radius: float = CONE_BASE_RADIUS_M,
                max_range: float = np.inf) -> tuple:
    """
    Return the nearest cone hit along each ray.

    An upright right circular cone with its apex up satisfies
    ``sin^2(a) * wz^2 == cos^2(a) * (wx^2 + wy^2)`` for ``w`` measured
    from the apex, with half-angle ``a`` given by ``tan(a) = r / h``.
    Substituting a ray ``t * d`` gives a quadratic in ``t``; a hit
    counts only where it lies between the apex and the ground, which is
    what keeps the infinite double cone from inventing returns above
    the cone or mirrored below the ground.
    """
    count = dirs.shape[0]
    best = np.full(count, np.inf)

    if cones_xy.size == 0:
        return best, np.zeros(count, dtype=bool)

    half_angle = np.arctan2(radius, height)
    cos_sq = float(np.cos(half_angle) ** 2)
    sin_sq = float(np.sin(half_angle) ** 2)

    apex_z = height - sensor_z

    for cone_x, cone_y in cones_xy:
        # Cull on the sensor-to-apex distance before anything else.
        if np.hypot(cone_x, cone_y) - radius > max_range:
            continue

        idx = _candidate_rays(dirs, cone_x, cone_y, radius)
        if idx.size == 0:
            continue

        d = dirs[idx]
        dx, dy, dz = d[:, 0], d[:, 1], d[:, 2]

        a = sin_sq * dz ** 2 - cos_sq * (dx ** 2 + dy ** 2)
        b = 2.0 * (cos_sq * (dx * cone_x + dy * cone_y)
                   - sin_sq * dz * apex_z)
        c = sin_sq * apex_z ** 2 - cos_sq * (cone_x ** 2 + cone_y ** 2)

        usable = np.abs(a) > _PARALLEL_EPS
        disc = b ** 2 - 4.0 * a * c

        # A ray aimed at the apex touches the cone at exactly one point,
        # so the discriminant is algebraically zero and lands either
        # side of it on rounding: measured at -1e-16 relative to b^2,
        # which is double-precision epsilon. Without this the nearest
        # point of a cone is the one direction that can miss it, and
        # whether it does depends on the platform.
        scale = np.maximum(b * b, np.abs(4.0 * a * c))
        grazing = (disc < 0.0) & (disc > -_TANGENT_REL_EPS * scale)
        disc = np.where(grazing, 0.0, disc)

        usable &= disc >= 0.0
        if not np.any(usable):
            continue

        sqrt_disc = np.sqrt(np.where(usable, disc, 0.0))
        hit_t = np.full(idx.size, np.inf)
        for sign in (-1.0, 1.0):
            t = np.where(usable, (-b + sign * sqrt_disc) / (2.0 * a),
                         np.inf)
            point_z = t * dz
            # In front of the sensor, below the apex, above the ground:
            # the last two bounds are what make the cone finite.
            ok = (usable & (t > 0.0) & (point_z <= apex_z)
                  & (point_z >= -sensor_z))
            hit_t = np.where(ok & (t < hit_t), t, hit_t)

        improved = hit_t < best[idx]
        if np.any(improved):
            target = idx[improved]
            best[target] = hit_t[improved]

    return best, np.isfinite(best)


def cast(dirs: np.ndarray, cones_xy: np.ndarray, sensor_z: float,
         max_range: float = np.inf) -> tuple:
    """
    Return the range to the nearest of ground or cone.

    Returns (ranges, hit, on_cone). The nearest surface wins, so a cone
    occludes the patch of ground behind it rather than both returning.

    ``on_cone`` says which surface produced each return. Counting
    points on a cone by their position instead would have to guess at
    the base, where a cone return and a ground return sit millimetres
    apart, and quietly counts ground as cone. The caster already knows,
    so it says.
    """
    cone_r, cone_hit = cone_ranges(dirs, cones_xy, sensor_z,
                                   max_range=max_range)
    ground_r, ground_hit = ground_ranges(dirs, sensor_z)

    on_cone = cone_hit & (~ground_hit | (cone_r <= ground_r))
    ranges = np.where(on_cone, cone_r,
                      np.where(ground_hit, ground_r, np.inf))
    return ranges, cone_hit | ground_hit, on_cone
