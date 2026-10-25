"""Segment candidate cone colors in RGB images without ROS dependencies."""

from dataclasses import dataclass

import cv2
import numpy as np


# OpenCV hue uses 0..179; saturation/value use 0..255.
DEFAULT_THRESHOLDS = {
    'blue': (100, 100, 50, 130, 255, 255),
    'yellow': (20, 100, 50, 35, 255, 255),
    'orange': (5, 100, 50, 19, 255, 255),
}
DEFAULT_MIN_AREA = 25


@dataclass(frozen=True)
class ColorRegion:
    """A connected color patch, with pixel bounds and foreground area."""

    color: str
    x: int
    y: int
    width: int
    height: int
    area: int


def validate_config(thresholds, min_area):
    """Reject invalid HSV bounds and pixel-area cutoffs."""
    if not isinstance(min_area, int) or isinstance(min_area, bool) or min_area < 1:
        raise ValueError('min_region_area must be a positive integer')
    for color, bounds in thresholds.items():
        if (color not in DEFAULT_THRESHOLDS
                or not isinstance(bounds, (list, tuple)) or len(bounds) != 6):
            raise ValueError(f'{color}: expected six HSV bounds')
        if any(not isinstance(v, (int, np.integer)) for v in bounds):
            raise ValueError(f'{color}: HSV bounds must be integers')
        for lo, hi, maximum in zip(bounds[:3], bounds[3:], (179, 255, 255)):
            if not 0 <= lo <= hi <= maximum:
                raise ValueError(f'{color}: invalid HSV range')


def decode_image(data, width, height, step, encoding):
    """Decode supported ROS byte layouts into an owned RGB array."""
    channels = {'rgb8': 3, 'bgr8': 3, 'rgba8': 4, 'bgra8': 4}.get(encoding)
    if channels is None:
        raise ValueError(f'Unsupported image encoding: {encoding}')
    if width < 1 or height < 1 or step < width * channels:
        raise ValueError('Invalid image dimensions or row stride')
    raw = np.frombuffer(data, dtype=np.uint8)
    if raw.size != height * step:
        raise ValueError('Image data length does not match height * step')
    pixels = raw.reshape(height, step)[:, :width * channels]
    rgb = pixels.reshape(height, width, channels)[:, :, :3]
    if encoding in ('bgr8', 'bgra8'):
        rgb = rgb[:, :, ::-1]
    return rgb.copy()


def classify_regions(rgb, thresholds=None, min_area=DEFAULT_MIN_AREA):
    """Return connected blue/yellow/orange candidates above the area cutoff."""
    if thresholds is None:
        thresholds = DEFAULT_THRESHOLDS
    validate_config(thresholds, min_area)
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError('Expected a uint8 RGB image')
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    regions = []
    for color, bounds in thresholds.items():
        mask = cv2.inRange(hsv, np.array(bounds[:3]), np.array(bounds[3:]))
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        for x, y, width, height, area in stats[1:count]:
            if area >= min_area:
                regions.append(ColorRegion(
                    color, int(x), int(y), int(width), int(height), int(area)))
    return regions


def annotate_regions(rgb, regions):
    """Draw labeled boxes on a copy of the RGB source frame."""
    result = rgb.copy()
    colors = {'blue': (0, 128, 255), 'yellow': (255, 255, 0), 'orange': (255, 128, 0)}
    for region in regions:
        color = colors[region.color]
        cv2.rectangle(result, (region.x, region.y),
                      (region.x + region.width - 1, region.y + region.height - 1),
                      color, 2)
        cv2.putText(result, region.color, (region.x, max(12, region.y - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
    return result
