"""Project mapped cones and accumulate conservative camera color evidence."""

from collections import Counter, deque
from dataclasses import dataclass, field
import math

import numpy as np


FUSION_DEFAULTS = {
    'process_hz': 10.0,
    'map_max_age_sec': 0.5,
    'camera_max_age_sec': 0.5,
    'sample_height_m': 0.1625,
    'max_color_range_m': 20.0,
    'pixel_margin': 6.0,
    'vote_window': 5,
    'min_color_votes': 3,
    'min_color_fraction': 0.7,
    'id_reset_distance_m': 0.5,
}


def validate_settings(settings):
    """Reject invalid startup settings before creating subscriptions."""
    for name, default in FUSION_DEFAULTS.items():
        value = settings[name]
        if type(default) is int:
            valid = type(value) is int and value > 0
        else:
            valid = (type(value) in (float, int) and math.isfinite(value)
                     and value >= 0)
            if name not in ('sample_height_m', 'pixel_margin'):
                valid = valid and value > 0
        if not valid:
            raise ValueError(f'{name}: invalid fusion setting')
    if settings['map_max_age_sec'] > 2.0:
        raise ValueError('map_max_age_sec must not exceed 2.0')
    if settings['min_color_votes'] > settings['vote_window']:
        raise ValueError('min_color_votes must not exceed vote_window')
    if not .5 < settings['min_color_fraction'] <= 1.0:
        raise ValueError('min_color_fraction must be greater than 0.5 and at most 1.0')


def validate_calibration(info):
    """Accept only an ideal, full-image rectified left pinhole CameraInfo."""
    p = np.asarray(info.p, dtype=float)
    r = np.asarray(info.r, dtype=float)
    d = np.asarray(info.d, dtype=float)
    if (info.width < 1 or info.height < 1 or p.shape != (12,) or r.shape != (9,)
            or not np.isfinite(p).all() or not np.isfinite(r).all()
            or not np.isfinite(d).all() or np.any(d != 0.)
            or not np.allclose(r.reshape(3, 3), np.eye(3), rtol=0., atol=1e-9)
            or info.binning_x not in (0, 1) or info.binning_y not in (0, 1)):
        raise ValueError('Require finite, undistorted, unbinned rectified CameraInfo')
    expected = np.array([p[0], 0., p[2], 0., 0., p[5], p[6], 0., 0., 0., 1., 0.])
    if p[0] <= 0 or p[5] <= 0 or not np.allclose(p, expected, rtol=0., atol=1e-9):
        raise ValueError('Require a left-camera pinhole projection matrix')
    roi = info.roi
    if (roi.x_offset != 0 or roi.y_offset != 0 or roi.do_rectify
            or (roi.width, roi.height) not in ((0, 0), (info.width, info.height))):
        raise ValueError('Require full-image CameraInfo ROI')


def project_cones(cones, map_to_optical, info, sample_height_m, max_range_m):
    """Return visible ID-to-pixel coordinates without changing map positions."""
    validate_calibration(info)
    transform = np.asarray(map_to_optical, dtype=float)
    if transform.shape != (4, 4) or not np.isfinite(transform).all():
        raise ValueError('Require a finite map-to-optical transform')
    pixels = {}
    for cone_id, position in cones.items():
        if not np.isfinite(position).all():
            continue
        x, y, z = position
        sample = transform @ np.array([x, y, z + sample_height_m, 1.])
        x, y, z = sample[:3]
        if z <= 0. or np.linalg.norm(sample[:3]) > max_range_m:
            continue
        u, v = info.p[0] * x / z + info.p[2], info.p[5] * y / z + info.p[6]
        if 0. <= u < info.width and 0. <= v < info.height:
            pixels[cone_id] = (float(u), float(v))
    return pixels


def associate_regions(pixels, regions, pixel_margin):
    """Accept only matches unique on both the cone and color-region sides."""
    candidates = {
        cone_id: [index for index, region in enumerate(regions)
                  if (region.x - pixel_margin <= u < region.x + region.width + pixel_margin
                      and region.y - pixel_margin <= v < region.y + region.height + pixel_margin)]
        for cone_id, (u, v) in pixels.items()
    }
    counts = Counter(index for matches in candidates.values() for index in matches)
    return {cone_id: regions[matches[0]].color
            for cone_id, matches in candidates.items()
            if len(matches) == 1 and counts[matches[0]] == 1}


def resolve_cone_namespace(camera_color, lidar_namespace):
    """Use blue/yellow camera sides; keep LiDAR geometry for orange or unknown."""
    return {'blue': 'left_cones', 'yellow': 'right_cones'}.get(
        camera_color, lidar_namespace)


@dataclass
class _Evidence:
    reference: tuple
    generation: int
    votes: deque = field(default_factory=deque)
    label: str | None = None


class ColorEvidence:
    """Bound per-ID votes and invalidate evidence on map removal or movement."""

    def __init__(self, vote_window, min_votes, min_fraction, reset_distance):
        """Configure confirmation and the displacement gate for reused IDs."""
        self._window = vote_window
        self._minimum = min_votes
        self._fraction = min_fraction
        self._distance = reset_distance
        self._entries = {}
        self._generation = 0
        self._last_stamp = 0
        self._visible = {}

    def update_map(self, positions):
        """Return generation tokens for this snapshot, dropping removed IDs."""
        self._entries = {key: value for key, value in self._entries.items() if key in positions}
        for cone_id, position in positions.items():
            entry = self._entries.get(cone_id)
            if entry is None or math.dist(position, entry.reference) > self._distance:
                self._generation += 1
                self._entries[cone_id] = _Evidence(tuple(position), self._generation)
        return {cone_id: entry.generation for cone_id, entry in self._entries.items()}

    def observe(self, colors, stamp_ns, generations):
        """Vote once per increasing image stamp, only for still-current IDs."""
        if stamp_ns <= self._last_stamp:
            return
        self._last_stamp = stamp_ns
        self._visible = {}
        for cone_id, color in colors.items():
            entry = self._entries.get(cone_id)
            if entry is None or generations.get(cone_id) != entry.generation:
                continue
            self._visible[cone_id] = (entry.generation, color)
            entry.votes.append(color)
            while len(entry.votes) > self._window:
                entry.votes.popleft()
            winner, count = Counter(entry.votes).most_common(1)[0]
            entry.label = (winner if count >= self._minimum
                           and count / len(entry.votes) >= self._fraction else None)

    def labels(self):
        """Return measured colors, using None for unsupported or conflicting IDs."""
        return {cone_id: entry.label for cone_id, entry in self._entries.items()}

    def visible_labels(self, now_ns, max_age_ns):
        """Return confirmed colors supported by the latest fresh, unique matches."""
        if self._last_stamp <= 0 or not 0 <= now_ns - self._last_stamp <= max_age_ns:
            return {}
        return {cone_id: color for cone_id, (generation, color) in self._visible.items()
                if (cone_id in self._entries
                    and self._entries[cone_id].generation == generation
                    and self._entries[cone_id].label == color)}
