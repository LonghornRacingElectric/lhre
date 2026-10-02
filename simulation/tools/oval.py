import numpy as np
from scipy.optimize import fsolve


def unit(heading):
    return np.stack((np.cos(heading), np.sin(heading)), axis=-1)


def corners(length_m, mean_radius_m, radius_rate_m_per_rad):
    (radius_0, radius_1), (rate_0, rate_1) = mean_radius_m, radius_rate_m_per_rad

    def residual(unknowns):
        mid_heading, turn_1 = unknowns
        turn_0 = 2.0 * np.pi - turn_1
        return (
            length_m * np.sin(mid_heading) * np.cos(turn_1 / 2.0) - (radius_0 - radius_1),
            length_m * np.cos(mid_heading) * np.sin(turn_1 / 2.0) + (rate_0 * turn_0 + rate_1 * turn_1) / 2.0,
        )

    (mid_heading, turn_1), _, status, message = fsolve(residual, (np.pi / 2.0, np.pi), full_output=True)
    if status != 1 or not 0.0 < turn_1 < 2.0 * np.pi:
        raise ValueError(f"no tangent straights for these parameters: {message}")
    bottom, top = mid_heading - turn_1 / 2.0, mid_heading + turn_1 / 2.0
    centers = (np.zeros(2), np.array([length_m, 0.0]))
    heading_ranges = ((top, bottom + 2.0 * np.pi), (bottom, top))
    result = [
        {"center_m": center, "mean_radius_m": radius, "radius_rate_m_per_rad": rate, "headings_rad": headings}
        for center, radius, rate, headings in zip(centers, mean_radius_m, radius_rate_m_per_rad, heading_ranges)
    ]
    for index, corner in enumerate(result):
        if np.min(corner_curve(corner, np.array(corner["headings_rad"]))[1]) <= 0.0:
            raise ValueError(f"corner {index} radius goes non-positive; reduce |R_{index}'|")
    return result


def corner_curve(corner, heading):
    start, end = corner["headings_rad"]
    radius = corner["mean_radius_m"] + corner["radius_rate_m_per_rad"] * (heading - (start + end) / 2.0)
    point = corner["center_m"] + corner["radius_rate_m_per_rad"] * unit(heading) + np.asarray(radius)[..., None] * unit(heading - np.pi / 2.0)
    return point, radius


def straight_lengths(corner_list):
    lengths = []
    for index, corner in enumerate(corner_list):
        heading = corner["headings_rad"][1]
        chord = corner_curve(corner_list[1 - index], corner_list[1 - index]["headings_rad"][0])[0] - corner_curve(corner, heading)[0]
        length = float(np.dot(unit(heading), chord))
        if length <= 0.0 or abs(np.dot(unit(heading - np.pi / 2.0), chord)) > 1e-6 * np.linalg.norm(chord):
            raise ValueError("straight is not tangent to both corners; parameters give no C1 oval")
        lengths.append(length)
    return lengths


def oval_layout(length_m, mean_radius_m, radius_rate_m_per_rad):
    corner_list = corners(length_m, mean_radius_m, radius_rate_m_per_rad)
    straights = straight_lengths(corner_list)
    summary = []
    for corner in corner_list:
        start, end = corner["headings_rad"]
        entry_radius, exit_radius = corner_curve(corner, np.array((start, end)))[1]
        summary.append(
            {
                "turn_deg": float(np.degrees(end - start)),
                "entry_radius_m": float(entry_radius),
                "exit_radius_m": float(exit_radius),
                "arc_m": float(corner["mean_radius_m"] * (end - start)),
            }
        )
    return {"corners": summary, "straights_m": straights, "lap_m": sum(corner["arc_m"] for corner in summary) + sum(straights)}


def oval_centerline(length_m, mean_radius_m, radius_rate_m_per_rad, spacing_m):
    first, second = corner_list = corners(length_m, mean_radius_m, radius_rate_m_per_rad)
    straights = straight_lengths(corner_list)

    def arc(corner, start, end):
        count = max(1, int(np.ceil(corner["mean_radius_m"] * (end - start) / spacing_m)))
        return corner_curve(corner, np.linspace(start, end, count, endpoint=False))[0]

    def straight(corner, length):
        heading = corner["headings_rad"][1]
        count = max(1, int(np.ceil(length / spacing_m)))
        return corner_curve(corner, heading)[0] + np.linspace(0.0, length, count, endpoint=False)[:, None] * unit(heading)

    start_0, end_0 = first["headings_rad"]
    middle_0 = (start_0 + end_0) / 2.0
    return np.vstack(
        (
            arc(first, middle_0, end_0),
            straight(first, straights[0]),
            arc(second, *second["headings_rad"]),
            straight(second, straights[1]),
            arc(first, start_0, middle_0),
        )
    )
