"""Exercise color regions, camera encodings, and malformed frame rejection."""

import importlib
import importlib.util

import numpy as np
import pytest


def _module():
    assert importlib.util.find_spec('lhr_perception.cone_color') is not None, (
        'Camera color classifier is missing')
    return importlib.import_module('lhr_perception.cone_color')


def test_classifies_regions_and_rejects_noise_and_neutral_background():
    """Hue swaps or accepting small/neutral patches must change these results."""
    image = np.full((80, 120, 3), 120, dtype=np.uint8)
    image[10:30, 10:25] = [0, 0, 255]
    image[10:30, 40:55] = [255, 255, 0]
    image[10:30, 70:85] = [255, 100, 0]
    image[50:52, 10:12] = [0, 0, 255]
    image[50:70, 40:55] = [255, 255, 255]
    regions = _module().classify_regions(image)
    assert [(r.color, r.x, r.y, r.width, r.height, r.area) for r in regions] == [
        ('blue', 10, 10, 15, 20, 300),
        ('yellow', 40, 10, 15, 20, 300),
        ('orange', 70, 10, 15, 20, 300),
    ]
    annotated = _module().annotate_regions(image, regions)
    assert annotated.shape == image.shape
    assert not np.array_equal(annotated, image)
    assert np.array_equal(image[10, 10], [0, 0, 255])


def test_thresholds_and_minimum_area_are_configurable():
    """Overrides must affect segmentation and the inclusive pixel-area cutoff."""
    image = np.zeros((10, 10, 3), dtype=np.uint8)
    image[2:4, 2:4] = [255, 0, 0]
    module = _module()
    assert module.classify_regions(image, min_area=4) == []
    thresholds = {'blue': (0, 100, 100, 0, 255, 255)}
    assert module.classify_regions(image, thresholds, min_area=5) == []
    assert module.classify_regions(image, thresholds, min_area=4)[0].area == 4


@pytest.mark.parametrize('encoding,pixel', [
    ('rgb8', [10, 20, 30]), ('bgr8', [30, 20, 10]),
    ('rgba8', [10, 20, 30, 255]), ('bgra8', [30, 20, 10, 255]),
])
def test_decode_padded_rows_and_channel_order(encoding, pixel):
    """Row padding and BGR order must not contaminate the next RGB pixel."""
    step = len(pixel) + 2
    data = bytes(pixel + [99, 99] + pixel + [88, 88])
    decoded = _module().decode_image(data, 1, 2, step, encoding)
    assert decoded.tolist() == [[[10, 20, 30]], [[10, 20, 30]]]


@pytest.mark.parametrize('width,height,step,encoding,data', [
    (1, 1, 1, 'mono8', b'\x00'),
    (2, 1, 3, 'rgb8', bytes(3)),
    (1, 2, 3, 'rgb8', bytes(3)),
    (0, 1, 0, 'rgb8', b''),
])
def test_rejects_unsupported_or_malformed_images(width, height, step, encoding, data):
    """Invalid messages must be dropped instead of reshaped or misclassified."""
    with pytest.raises(ValueError):
        _module().decode_image(data, width, height, step, encoding)


@pytest.mark.parametrize('thresholds,min_area', [
    ({'blue': (0, 0, 0, 180, 255, 255)}, 4),
    ({'blue': (20, 0, 0, 10, 255, 255)}, 4),
    ({'blue': (0, 0, 0)}, 4),
    ({'blue': None}, 4),
    ({'blue': 'invalid'}, 4),
    ({'blue': (0, 0, 0, 10, 255, 255)}, 0),
])
def test_rejects_invalid_configuration(thresholds, min_area):
    """Bad settings must fail at startup rather than silently discard colors."""
    with pytest.raises(ValueError):
        _module().validate_config(thresholds, min_area)
