"""Association and accumulation for map-frame cone detections."""


def update_cone_map(
    detections: list[tuple[float, float]],
    cone_map: list[list[float]],
    association_radius: float,
) -> int:
    """Associate one scan to existing cones and return the number added."""
    radius_sq = association_radius ** 2
    candidates: list[tuple[float, int, int]] = []
    for detection_index, (mx, my) in enumerate(detections):
        for cone_index, entry in enumerate(cone_map):
            distance_sq = (mx - entry[0]) ** 2 + (my - entry[1]) ** 2
            if distance_sq < radius_sq:
                candidates.append((distance_sq, detection_index, cone_index))

    # Resolve closest pairs first. This makes association independent of map
    # insertion order and prevents one landmark from consuming two detections
    # from the same scan.
    matched_detections: set[int] = set()
    matched_cones: set[int] = set()
    for _, detection_index, cone_index in sorted(candidates):
        if detection_index in matched_detections:
            continue
        if cone_index in matched_cones:
            continue

        mx, my = detections[detection_index]
        entry = cone_map[cone_index]
        count = entry[2]
        entry[0] = (entry[0] * count + mx) / (count + 1)
        entry[1] = (entry[1] * count + my) / (count + 1)
        entry[2] = count + 1
        matched_detections.add(detection_index)
        matched_cones.add(cone_index)

    new_count = 0
    for detection_index, (mx, my) in enumerate(detections):
        if detection_index in matched_detections:
            continue
        cone_map.append([mx, my, 1.0])
        new_count += 1
    return new_count
