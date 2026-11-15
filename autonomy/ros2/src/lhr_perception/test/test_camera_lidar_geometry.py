"""Check camera projection and conservative color evidence without ROS."""

import importlib
import importlib.util
from types import SimpleNamespace

from lhr_perception.cone_color import classify_regions, ColorRegion
import numpy as np
import pytest


def fusion():
    """Fail clearly until the projection implementation exists."""
    assert importlib.util.find_spec('lhr_perception.camera_lidar_geometry') is not None
    return importlib.import_module('lhr_perception.camera_lidar_geometry')


def calibration():
    """Describe an ideal rectified left-camera view."""
    return SimpleNamespace(
        width=100, height=80, p=[100., 0., 50., 0., 0., 100., 40., 0., 0., 0., 1., 0.],
        r=[1., 0., 0., 0., 1., 0., 0., 0., 1.], d=[0.] * 5,
        binning_x=0, binning_y=0,
        roi=SimpleNamespace(x_offset=0, y_offset=0, width=0, height=0, do_rectify=False))


def test_projection_uses_optical_axes_and_preserves_input():
    """Optical x goes right and optical y goes down, with finite bounds."""
    points = {7: (1., -.5, 5.), 8: (0., 0., 2.)}
    pixels = fusion().project_cones(points, np.eye(4), calibration(), 0., 20.)
    assert pixels == {7: (70., 30.), 8: (50., 40.)}
    assert points == {7: (1., -.5, 5.), 8: (0., 0., 2.)}


def test_projection_applies_mount_rotation_translation_and_height_before_transform():
    """A forward-facing optical camera at z=1 looks at a sample above the ground."""
    # optical x=-map y, optical y=1-map z, optical z=map x-2.
    transform = np.array([[0., -1., 0., 0.], [0., 0., -1., 1.],
                          [1., 0., 0., -2.], [0., 0., 0., 1.]])
    points = {42: (7., -1., 0.)}
    assert fusion().project_cones(points, transform, calibration(), .5, 20.) == {
        42: (70., 50.)}


def test_projection_rejects_behind_outside_nonfinite_and_distant_cones():
    """Invalid projections cannot inherit unrelated image colors."""
    points = {0: (0., 0., 0.), 1: (0., 0., -1.), 2: (1., 0., 2.),
              3: (0., .8, 2.), 4: (0., 0., 20.01), 5: (float('nan'), 0., 3.),
              6: (float('inf'), 0., 3.), 7: (-1., -.8, 2.), 8: (0., 0., 20.)}
    assert fusion().project_cones(points, np.eye(4), calibration(), 0., 20.) == {
        7: (0., 0.), 8: (50., 40.)}


@pytest.mark.parametrize('field,value', [
    ('p', [0.] * 12), ('p', [float('nan')] * 12),
    ('p', [100., 0., 50., 1., 0., 100., 40., 0., 0., 0., 1., 0.]),
    ('r', [0.] * 9), ('d', [.1, 0., 0., 0., 0.]), ('binning_x', 2),
    ('width', 0), ('height', 0),
])
def test_rejects_unsupported_calibration(field, value):
    """Never substitute assumed optics for incompatible CameraInfo."""
    info = calibration()
    setattr(info, field, value)
    with pytest.raises(ValueError):
        fusion().validate_calibration(info)


def test_rejects_cropped_calibration_but_accepts_full_image_roi():
    """Only full-image pixel coordinates are supported."""
    info = calibration()
    info.roi.width, info.roi.height = 100, 80
    fusion().validate_calibration(info)
    info.roi.x_offset = 1
    with pytest.raises(ValueError):
        fusion().validate_calibration(info)


def test_region_matching_requires_unique_cone_and_unique_patch():
    """A patch shared by two cones and a cone in two patches are both rejected."""
    regions = [ColorRegion('blue', 10, 10, 10, 10, 100),
               ColorRegion('yellow', 30, 10, 10, 10, 100),
               ColorRegion('orange', 35, 10, 10, 10, 100)]
    assert fusion().associate_regions({1: (12., 12.)}, regions, 0.) == {1: 'blue'}
    assert fusion().associate_regions(
        {1: (12., 12.), 2: (15., 15.), 3: (36., 12.)}, regions, 0.) == {}
    assert fusion().associate_regions({1: (9., 12.)}, regions, 1.) == {1: 'blue'}
    assert fusion().associate_regions({1: (20., 12.)}, regions, 0.) == {}


def test_raw_image_regions_provide_colors_and_threshold_overrides():
    """Projection feeds the existing HSV segmentation, including user tuning."""
    image = np.zeros((80, 100, 3), dtype=np.uint8)
    image[25:36, 65:76] = [0, 0, 255]
    pixels = fusion().project_cones({7: (1., -.5, 5.)}, np.eye(4), calibration(), 0., 20.)
    assert fusion().associate_regions(pixels, classify_regions(image), 0.) == {7: 'blue'}
    assert fusion().associate_regions(pixels, classify_regions(
        image, {'yellow': (100, 100, 50, 130, 255, 255)}), 0.) == {7: 'yellow'}


def test_votes_confirm_conflict_relabel_and_do_not_count_duplicate_frames():
    """A stale image must not confirm a color, and later evidence can correct it."""
    votes = fusion().ColorEvidence(5, 3, .7, .5)
    generations = votes.update_map({7: (1., 2., 0.)})
    for stamp in (1, 2):
        votes.observe({7: 'blue'}, stamp, generations)
        assert votes.labels() == {7: None}
    votes.observe({7: 'blue'}, 2, generations)
    assert votes.labels() == {7: None}
    votes.observe({7: 'blue'}, 3, generations)
    assert votes.labels() == {7: 'blue'}
    votes.observe({7: 'yellow'}, 4, generations)
    assert votes.labels() == {7: 'blue'}
    votes.observe({7: 'yellow'}, 5, generations)
    assert votes.labels() == {7: None}
    for stamp in (6, 7):
        votes.observe({7: 'yellow'}, stamp, generations)
    assert votes.labels() == {7: 'yellow'}
    votes.observe({}, 8, generations)
    assert votes.labels() == {7: 'yellow'}


def test_map_id_reuse_and_cumulative_motion_reset_color_generation():
    """Queued old maps cannot give replacement IDs their previous labels."""
    votes = fusion().ColorEvidence(5, 3, .7, .5)
    old = votes.update_map({7: (1., 2., 0.)})
    for stamp in (1, 2, 3):
        votes.observe({7: 'blue'}, stamp, old)
    assert votes.labels()[7] == 'blue'
    votes.update_map({7: (1.3, 2., 0.)})
    assert votes.labels()[7] == 'blue'
    current = votes.update_map({7: (1.6, 2., 0.)})
    assert votes.labels()[7] is None
    votes.observe({7: 'yellow'}, 4, old)
    assert votes.labels()[7] is None
    votes.update_map({})
    votes.update_map({7: (1.6, 2., 0.)})
    for stamp in (5, 6, 7):
        votes.observe({7: 'yellow'}, stamp, current)
    assert votes.labels()[7] is None


@pytest.mark.parametrize('overrides', [
    {'map_max_age_sec': 0.}, {'map_max_age_sec': 2.1}, {'process_hz': float('nan')},
    {'sample_height_m': -.1}, {'max_color_range_m': 0.}, {'pixel_margin': -1.},
    {'vote_window': True}, {'vote_window': 0}, {'min_color_votes': 6},
    {'min_color_fraction': .5}, {'min_color_fraction': 1.1},
    {'id_reset_distance_m': 0.},
])
def test_invalid_fusion_settings_fail_at_startup(overrides):
    """Bad settings must fail before any image is processed."""
    config = dict(fusion().FUSION_DEFAULTS, **overrides)
    with pytest.raises(ValueError):
        fusion().validate_settings(config)


def test_rigid_transform_matrix_handles_rotation_translation_and_bad_quaternions():
    """Use TF's full rotation, not yaw alone, and reject invalid transforms."""
    from types import SimpleNamespace as NS
    transform = NS(rotation=NS(x=0., y=0., z=1., w=1.),
                   translation=NS(x=1., y=2., z=3.))
    from lhr_perception.transforms import rigid_transform_matrix
    matrix = rigid_transform_matrix(transform)
    assert matrix @ [2., 0., 0., 1.] == pytest.approx([1., 4., 3., 1.])
    transform.rotation.z = transform.rotation.w = 0.
    with pytest.raises(ValueError):
        rigid_transform_matrix(transform)


@pytest.mark.parametrize('camera,lidar,expected', [
    ('blue', 'right_cones', 'left_cones'), ('yellow', 'left_cones', 'right_cones'),
    ('orange', 'left_cones', 'left_cones'), ('orange', 'right_cones', 'right_cones'),
    ('orange', 'cones', 'cones'), (None, 'left_cones', 'left_cones'),
    (None, 'right_cones', 'right_cones'), (None, 'cones', 'cones'),
])
def test_camera_side_overrides_lidar_only_when_available(camera, lidar, expected):
    """Missing camera labels use inferred sides without inventing physical color."""
    assert fusion().resolve_cone_namespace(camera, lidar) == expected


def test_camera_eligibility_expires_and_ambiguous_or_conflicting_frames_use_fallback():
    """Confirmed history alone must not override LiDAR after losing camera visibility."""
    votes = fusion().ColorEvidence(5, 3, .7, .5)
    generations = votes.update_map({7: (1., 2., 0.)})
    for stamp in (100, 200):
        votes.observe({7: 'blue'}, stamp, generations)
        assert votes.visible_labels(stamp, 50) == {}
    votes.observe({7: 'blue'}, 300, generations)
    assert votes.visible_labels(300, 50) == {7: 'blue'}
    assert votes.visible_labels(350, 50) == {7: 'blue'}
    assert votes.visible_labels(351, 50) == {}
    assert votes.visible_labels(299, 50) == {}
    votes.observe({}, 400, generations)
    assert votes.labels() == {7: 'blue'}
    assert votes.visible_labels(400, 50) == {}
    votes.observe({7: 'yellow'}, 500, generations)
    assert votes.labels() == {7: 'blue'}
    assert votes.visible_labels(500, 50) == {}
    # A duplicate or old image cannot re-enable a camera label after conflict.
    votes.observe({7: 'blue'}, 500, generations)
    assert votes.visible_labels(500, 50) == {}
    votes.observe({7: 'blue'}, 600, generations)
    assert votes.visible_labels(600, 50) == {7: 'blue'}


def test_old_generation_visibility_cannot_override_reused_ids():
    """Both ID movement and disappearance revoke live camera eligibility."""
    votes = fusion().ColorEvidence(5, 3, .7, .5)
    original = votes.update_map({7: (1., 2., 0.)})
    for stamp in (100, 200, 300):
        votes.observe({7: 'blue'}, stamp, original)
    assert votes.visible_labels(300, 50) == {7: 'blue'}
    votes.update_map({7: (1.6, 2., 0.)})
    assert votes.visible_labels(300, 50) == {}
    votes.update_map({})
    votes.update_map({7: (1.6, 2., 0.)})
    votes.observe({7: 'blue'}, 400, original)
    assert votes.visible_labels(400, 50) == {}


@pytest.mark.parametrize('value', [0., -.1, float('inf'), float('nan'), True])
def test_invalid_camera_max_age_is_rejected(value):
    """A broken freshness gate must fail at startup."""
    with pytest.raises(ValueError, match='camera_max_age_sec'):
        fusion().validate_settings(dict(fusion().FUSION_DEFAULTS, camera_max_age_sec=value))


def test_confirmed_orange_color_memory_survives_camera_loss_until_id_reset():
    """Inspection keeps physical orange without changing its inferred planning side."""
    votes = fusion().ColorEvidence(5, 3, .7, .5)
    generations = votes.update_map({7: (1., 2., 0.)})
    for stamp in (100, 200, 300):
        votes.observe({7: 'orange'}, stamp, generations)
    assert votes.labels() == {7: 'orange'}
    assert votes.visible_labels(1000, 50) == {}
    votes.observe({}, 1100, generations)
    assert votes.labels() == {7: 'orange'}
    votes.update_map({7: (2., 2., 0.)})
    assert votes.labels() == {7: None}
