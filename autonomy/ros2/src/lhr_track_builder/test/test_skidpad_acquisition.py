"""Skidpad fitting, limited visibility, and bounded acquisition behavior."""

import math
from unittest.mock import Mock

from lhr_track_builder.skidpad_acquisition import (
    _entry_corridor, _fit_layout, _layout_residuals, _visible,
    ACQUISITION_DEFAULTS, SkidpadAcquisition)
from lhr_trackgen.publish_cones import generate_skidpad_track
from lhr_trackgen.skidpad_geometry import generate_skidpad_lanes
from nav_msgs.msg import Odometry
import numpy as np
import pytest
import rclpy
from rclpy.time import Time
from std_msgs.msg import String
from visualization_msgs.msg import Marker, MarkerArray


def test_view_has_forward_240_degree_sector_and_15m_radius():
    points = [(14, 0), (16, 0), (-1, 0), (-1, 2), (0, -14), (float('nan'), 0)]
    assert _visible(points, np.array([0, 0, 0]), 15, 240).tolist() == [
        [14, 0], [-1, 2], [0, -14]]
    assert _visible([(0, 15), (0, -15)], np.array([0, 0, math.pi / 2]), 15, 240).tolist() == [
        [0, 15]]


@pytest.mark.parametrize('yaw', [0.0, 0.7, -2.5])
def test_fit_recovers_noisy_transformed_layout_with_missing_cones(yaw):
    points = np.array(sum(generate_skidpad_track(), []))
    rotation = np.array([[math.cos(yaw), math.sin(yaw)], [-math.sin(yaw), math.cos(yaw)]])
    translation = np.array([8.0, -3.0])
    points = points @ rotation + translation
    pose = np.r_[translation - 16.8747 * rotation[1], yaw + math.pi / 2]
    points = _visible(points, pose, 15, 240)
    points += np.random.default_rng(4).normal(0, 0.025, points.shape)
    fit = _fit_layout(points, np.r_[translation + [0.8, -0.6], yaw + 0.08],
                      ACQUISITION_DEFAULTS)
    assert fit is not None
    assert fit['pose'][:2] == pytest.approx(translation, abs=0.10)
    assert fit['pose'][2] == pytest.approx(yaw, abs=0.02)


def test_fit_rejects_duplicate_support_one_circle_and_wrong_dimensions():
    left, right = generate_skidpad_track()
    assert _fit_layout([left[0]] * 40, [0, 0, 0], ACQUISITION_DEFAULTS) is None
    assert _fit_layout(left[:17], [0, 0, 0], ACQUISITION_DEFAULTS) is None
    points = np.array(left + right) * 1.4
    assert _fit_layout(points, [0, 0, 0], ACQUISITION_DEFAULTS) is None


def test_chord_guesses_recover_translation_without_staging_position():
    yaw = -2.9
    rotation = np.array([[math.cos(yaw), math.sin(yaw)], [-math.sin(yaw), math.cos(yaw)]])
    translation = np.array([75.0, -42.0])
    points = np.array(sum(generate_skidpad_track(), [])) @ rotation + translation
    vehicle = np.r_[translation - 16.8747 * rotation[1], yaw + math.pi / 2]
    points = _visible(points, vehicle, 15, 240)
    points += np.random.default_rng(5).normal(0, 0.025, points.shape)
    # The heading prior crosses +/-pi; its x/y coordinates are deliberately wrong.
    prior = [0.0, 0.0, yaw + 2 * math.pi + math.radians(20)]
    fit = _fit_layout(points, prior, ACQUISITION_DEFAULTS)
    assert fit is not None
    assert fit['pose'][:2] == pytest.approx(translation, abs=0.10)
    assert math.atan2(math.sin(fit['pose'][2] - yaw),
                      math.cos(fit['pose'][2] - yaw)) == pytest.approx(0, abs=0.02)
    assert 1 <= fit['hypotheses'] <= ACQUISITION_DEFAULTS['fit_max_hypotheses']
    shuffled = np.random.default_rng(8).permutation(points)
    assert _fit_layout(shuffled, prior, ACQUISITION_DEFAULTS)['pose'] == pytest.approx(fit['pose'])


def test_robust_fit_handles_outliers_and_uses_previous_pose():
    points = np.array(sum(generate_skidpad_track(), []))
    points += np.random.default_rng(7).normal(0, 0.03, points.shape)
    points = np.vstack([points, [[30, 30], [-30, 25], [0, 35], [-25, -30]]])
    fit = _fit_layout(points, [100, 100, 0.1], ACQUISITION_DEFAULTS,
                      previous=np.array([0.08, -0.07, 0.01]))
    assert fit is not None
    assert fit['pose'] == pytest.approx([0, 0, 0], abs=0.04)
    assert min(fit['support']) >= 25


@pytest.mark.parametrize('ring', range(4))
@pytest.mark.parametrize('partial', [False, True])
@pytest.mark.parametrize('displacement', [-0.6, 1.2])
def test_displaced_cone_matches_fit_with_that_cone_omitted(ring, partial, displacement):
    left, right = generate_skidpad_track()
    points = np.array(left + right)
    colors = np.array([0] * len(left) + [1] * len(right))
    rings = np.array([0] * 17 + [3] * 13 + [1] * 13 + [2] * 17)
    centers = np.column_stack([np.where(rings < 2, -9.125, 9.125), np.zeros(len(points))])
    if partial:
        keep = [bool(len(_visible([point], np.array([0., -13.5, math.pi / 2]), 15, 240)))
                for point in points]
        points, colors, rings, centers = points[keep], colors[keep], rings[keep], centers[keep]
    candidates = np.flatnonzero(rings == ring)
    moved = candidates[np.argmin(points[candidates, 1])]
    radial = points[moved] - centers[moved]
    radial /= np.linalg.norm(radial)
    points += np.random.default_rng(23).normal(0, .015, points.shape)
    yaw = .7
    rotation = np.array([[math.cos(yaw), -math.sin(yaw)],
                         [math.sin(yaw), math.cos(yaw)]])
    translation = np.array([30., -20.])
    points = points @ rotation.T + translation
    reference = _fit_layout(np.delete(points, moved, axis=0), [0, 0, yaw],
                            ACQUISITION_DEFAULTS, colors=np.delete(colors, moved))
    assert reference is not None
    points[moved] += displacement * (radial @ rotation.T)
    fit = _fit_layout(points, [0, 0, yaw], ACQUISITION_DEFAULTS, colors=colors)
    assert fit is not None
    assert fit['pose'][:2] == pytest.approx(reference['pose'][:2], abs=1e-5)
    assert fit['pose'][2] == pytest.approx(reference['pose'][2], abs=1e-6)
    assert sum(fit['support']) == len(points) - 1
    assert fit['colored_support'] == reference['colored_support']


def test_trimming_cannot_hide_the_original_outlier_fraction():
    left, right = generate_skidpad_track()
    points = np.array(left + right)
    points[0, 0] += .6
    assert _fit_layout(points, [0, 0, 0], ACQUISITION_DEFAULTS) is not None
    limits = dict(ACQUISITION_DEFAULTS, fit_inlier_fraction=.99)
    assert _fit_layout(points, [0, 0, 0], limits) is None


def test_trimming_cannot_remove_required_boundary_support():
    left, right = generate_skidpad_track()
    points = np.array(left[:2] + left[17:] + right)
    colors = np.array([0] * (2 + 13) + [1] * len(right))
    assert _fit_layout(points, [0, 0, 0], ACQUISITION_DEFAULTS, colors=colors) is not None
    points[0, 0] += .6
    assert _fit_layout(points, [0, 0, 0], ACQUISITION_DEFAULTS, colors=colors) is None


def test_fit_rejects_narrow_arcs_and_wrong_entry_direction():
    angles = np.linspace(-0.15, 0.15, 8)
    points = [(cx + radius * math.cos(angle), radius * math.sin(angle))
              for cx in (-9.125, 9.125) for radius in (7.625, 10.625) for angle in angles]
    assert _fit_layout(points, [0, 0, 0], ACQUISITION_DEFAULTS) is None
    full = np.array(sum(generate_skidpad_track(), []))
    assert _fit_layout(full, [0, 0, math.pi / 2], ACQUISITION_DEFAULTS) is None


def test_radial_jacobian_matches_central_differences():
    pose = np.array([3.0, -4.0, 0.6])
    points = np.array([[12, 5], [-5, 3], [15, -7], [-10, -8]], dtype=float)
    assignment = np.array([0, 1, 2, 3])
    _, jacobian = _layout_residuals(pose, points, assignment)
    numerical = np.empty_like(jacobian)
    for column in range(3):
        step = np.zeros(3)
        step[column] = 1e-6
        numerical[:, column] = (
            _layout_residuals(pose + step, points, assignment)[0]
            - _layout_residuals(pose - step, points, assignment)[0]) / 2e-6
    assert jacobian == pytest.approx(numerical, abs=1e-7)


def test_color_assignments_distinguish_inner_outer_on_each_circle():
    left, right = generate_skidpad_track()
    points = np.array(left + right)
    colors = np.array([0] * len(left) + [1] * len(right))
    fit = _fit_layout(points, [0, 0, 0], ACQUISITION_DEFAULTS, colors=colors)
    assert fit is not None
    assert min(fit['colored_support']) >= 13
    assert _fit_layout(points, [0, 0, 0], ACQUISITION_DEFAULTS, colors=1 - colors) is None


def test_entry_corridor_requires_both_sides_and_stops_before_last_cones():
    small, _ = generate_skidpad_lanes()
    pose = np.array([0, -16.8747, math.pi / 2])
    points = _visible(small, pose, 15, 240)
    path, available = _entry_corridor(points, pose, 1.875, 0.682)
    assert np.max(np.abs(path[:, 0])) < 1e-9
    assert path[-1, 1] <= -8 - 1.875 - 1.0 + 1e-6
    assert available > 4
    assert _entry_corridor(points[points[:, 0] > 0], pose, 1.875, 0.682) is None
    assert _entry_corridor(points * [2, 1], pose, 1.875, 0.682) is None


@pytest.fixture
def acquisition():
    rclpy.init()
    node = SkidpadAcquisition()
    node._test_time = 10.0
    node.get_clock = lambda: Mock(now=lambda: Time(seconds=node._test_time))
    for name in ('_path_pub', '_limit_pub', '_state_pub', '_view_pub',
                 '_layout_pub', '_complete_pub'):
        setattr(node, name, Mock())
    odom = Odometry()
    odom.header.frame_id = 'map'
    odom.header.stamp = Time(seconds=10).to_msg()
    odom.pose.pose.position.y = -13.5
    odom.pose.pose.orientation.z = math.sin(math.pi / 4)
    odom.pose.pose.orientation.w = math.cos(math.pi / 4)
    node._odom_cb(odom)
    node._status_cb(String(data='DRIVING'))
    yield node
    node.destroy_node()
    rclpy.shutdown()


def _observations(include_circles=True):
    msg = MarkerArray()
    groups = []
    if include_circles:
        left, right = generate_skidpad_track()
        groups.extend([('left_cones', left), ('right_cones', right)])
    small, large = generate_skidpad_lanes()
    groups.extend([('orange_small', small), ('orange_large', large)])
    for ns, points in groups:
        for index, (x, y) in enumerate(points):
            marker = Marker(ns=ns, id=index)
            marker.header.frame_id = 'map'
            marker.pose.position.x, marker.pose.position.y = float(x), float(y)
            msg.markers.append(marker)
    return msg


def test_approach_requires_go_and_stops_on_stale_inputs(acquisition):
    node = acquisition
    node._cones_cb(_observations(False))
    node._status_cb(String(data='READY'))
    node._tick()
    assert node._phase == 'APPROACH'
    assert node._limit_pub.publish.call_args.args[0].data == 0.0
    node._status_cb(String(data='DRIVING'))
    node._tick()
    assert 0 < node._limit_pub.publish.call_args.args[0].data <= 0.5
    node._test_time += 0.6
    node._tick()
    assert node._phase == 'HOLD'
    assert node._limit_pub.publish.call_args.args[0].data == 0.0


def test_fit_requires_new_frames_and_standstill_then_hands_off(acquisition):
    node = acquisition
    node._cones_cb(_observations())
    node._tick()
    for _ in range(8):
        node._tick()
    assert node._stable == 1
    node._speed = 0.2
    for _ in range(4):
        node._cones_cb(_observations())
        node._tick()
    assert node._phase == 'HOLD'
    assert node._limit_pub.publish.call_args.args[0].data == 0.0
    node._speed = 0
    for _ in range(9):
        node._test_time += 0.2
        node._odom_time = node.get_clock().now().nanoseconds / 1e9
        node._cones_cb(_observations())
        node._tick()
    assert node._phase == 'ENTRY'
    assert node._route is not None
    assert len(node._path_pub.publish.call_args.args[0].poses) > 1
    assert node._limit_pub.publish.call_args.args[0].data > 0.0
    # Stale inputs during a run latch a hold rather than restarting acquisition.
    node._test_time += 1.0
    node._tick()
    node._odom_time = node.get_clock().now().nanoseconds / 1e9
    node._cones_cb(_observations(False))
    node._tick()
    assert node._phase == 'HOLD'
    assert node._fault
    assert node._limit_pub.publish.call_args.args[0].data == 0.0


def test_approach_budget_and_invalid_frame_prevent_motion(acquisition):
    node = acquisition
    node._cones_cb(_observations(False))
    node._distance = 4.6
    node._tick()
    assert node._limit_pub.publish.call_args.args[0].data == 0.0
    node._distance = 5
    node._tick()
    assert node._phase == 'HOLD'
    node._odom_cb(Odometry())
    assert node._fault
    node._distance = 0
    node._tick()
    assert node._limit_pub.publish.call_args.args[0].data == 0.0


def test_ready_route_waits_for_go(acquisition):
    node = acquisition
    node._status_cb(String(data='READY'))
    for _ in range(15):
        node._test_time += 0.2
        node._odom_time = node.get_clock().now().nanoseconds / 1e9
        node._cones_cb(_observations())
        node._tick()
    assert node._route is not None
    assert node._phase == 'ACQUIRED'
    assert node._route.progress == 0
    assert node._limit_pub.publish.call_args.args[0].data == 0
    node._status_cb(String(data='DRIVING'))
    node._test_time += 0.2
    node._odom_time = node.get_clock().now().nanoseconds / 1e9
    node._cones_cb(_observations())
    node._tick()
    assert node._phase == 'ENTRY'
    assert node._limit_pub.publish.call_args.args[0].data > 0


def test_frozen_odometry_does_not_refresh_watchdog(acquisition):
    node = acquisition
    before = node._odom_time
    node._test_time += 1.0
    msg = Odometry()
    msg.header.frame_id = 'map'
    msg.header.stamp = Time(seconds=10).to_msg()
    msg.pose.pose.orientation.w = 1.0
    node._odom_cb(msg)
    assert node._odom_time == before
    node._cones_cb(_observations(False))
    node._tick()
    assert node._limit_pub.publish.call_args.args[0].data == 0.0


def test_camera_acquisition_rejects_inferred_sides_stale_and_duplicate_frames(acquisition):
    node = acquisition
    node._require_camera_colors = True
    raw = _observations()
    node._cones_cb(raw)
    node._tick()
    assert not node._circular and not node._orange
    assert node._limit_pub.publish.call_args.args[0].data == 0
    observed = _observations()
    for index, marker in enumerate(observed.markers):
        marker.id = index
        marker.ns = {'left_cones': 'blue_cones', 'right_cones': 'yellow_cones',
                     'orange_small': 'orange_cones', 'orange_large': 'orange_cones'}[marker.ns]
        marker.header.stamp = Time(seconds=10.).to_msg()
    node._cones_cb(observed)
    node._tick()
    frame = node._frame
    stable = node._stable
    for _ in range(6):
        node._cones_cb(observed)
        node._tick()
    assert node._frame == frame
    assert node._stable == stable == 1
    assert node._gates and node._lane_map
    node._test_time = 11.
    node._cones_cb(observed)
    node._tick()
    assert node._cone_time == 10.
    assert node._limit_pub.publish.call_args.args[0].data == 0


def test_camera_orange_roles_are_validated_by_route_geometry(acquisition):
    node = acquisition
    node._require_camera_colors = True
    observed = _observations()
    observed.markers = [m for m in observed.markers
                        if m.ns != 'orange_large' or m.pose.position.y < 0]
    for index, marker in enumerate(observed.markers):
        marker.id = index
        marker.ns = {'left_cones': 'blue_cones', 'right_cones': 'yellow_cones',
                     'orange_small': 'orange_cones', 'orange_large': 'orange_cones'}[marker.ns]
    for _ in range(15):
        node._test_time += .2
        node._odom_time = node.get_clock().now().nanoseconds / 1e9
        for marker in observed.markers:
            marker.header.stamp = Time(seconds=node._test_time).to_msg()
        node._cones_cb(observed)
        node._tick()
    assert node._route is not None
    assert node._phase == 'ENTRY'
    assert node._limit_pub.publish.call_args.args[0].data > 0


def test_entry_span_tolerates_centroid_error_without_relaxing_side_support():
    points = [(x, y) for x in (-1.5, 1.5) for y in (-12.02, -10.02, -8.04)]
    pose = np.array([0., -16.8747, math.pi / 2])
    assert _entry_corridor(points, pose, 1.875, .682) is not None
    assert _entry_corridor(points[:-1], pose, 1.875, .682) is None
    short = [(x, y) for x in (-1.5, 1.5) for y in (-11.5, -10., -8.)]
    assert _entry_corridor(short, pose, 1.875, .682) is None


def test_camera_history_survives_occlusion_but_not_map_removal_or_id_displacement(acquisition):
    node = acquisition
    node._require_camera_colors = True
    observed = _observations()
    for index, marker in enumerate(observed.markers):
        marker.id = index
        marker.ns = {'left_cones': 'blue_cones', 'right_cones': 'yellow_cones',
                     'orange_small': 'orange_cones', 'orange_large': 'orange_cones'}[marker.ns]
        marker.header.stamp = Time(seconds=10.).to_msg()
    node._cones_cb(observed)
    key = next(iter(node._circle_map))
    target = next(m for m in observed.markers if m.id == key)
    target.ns = 'unknown_cones'
    node._test_time = 10.1
    for marker in observed.markers:
        marker.header.stamp = Time(seconds=10.1).to_msg()
    node._cones_cb(observed)
    assert key in node._circle_map
    target.pose.position.x += .6
    node._cones_cb(observed)
    assert key not in node._circle_map
    orange_key = next(iter(node._lane_map))
    observed.markers = [m for m in observed.markers if m.id != orange_key]
    node._cones_cb(observed)
    assert orange_key not in node._lane_map
    assert orange_key not in node._gates


def test_unknown_lidar_geometry_does_not_become_measured_color(acquisition):
    node = acquisition
    node._require_camera_colors = True
    observed = _observations()
    for index, marker in enumerate(observed.markers):
        marker.id = index
        marker.ns = {'left_cones': 'blue_cones', 'right_cones': 'yellow_cones',
                     'orange_small': 'orange_cones', 'orange_large': 'orange_cones'}[marker.ns]
        if marker.ns in ('blue_cones', 'yellow_cones') and index % 3 == 0:
            marker.ns = 'unknown_cones'
        marker.header.stamp = Time(seconds=10.).to_msg()
    node._cones_cb(observed)
    node._tick()
    assert -1 in node._circular_colors
    assert node._fit is not None
    assert all(color in (0, 1) for _, color in node._circle_map.values())


def test_measured_gate_history_is_used_only_after_entering_planner_view(acquisition):
    node = acquisition
    node._require_camera_colors = True
    node._pose[1] = -17.
    observed = _observations()
    for index, marker in enumerate(observed.markers):
        marker.id = index
        marker.ns = {'left_cones': 'blue_cones', 'right_cones': 'yellow_cones',
                     'orange_small': 'orange_cones', 'orange_large': 'orange_cones'}[marker.ns]
        marker.header.stamp = Time(seconds=10.).to_msg()
    node._cones_cb(observed)
    assert node._gates
    for _ in range(15):
        node._test_time += .2
        node._odom_time = node.get_clock().now().nanoseconds / 1e9
        for marker in observed.markers:
            marker.header.stamp = Time(seconds=node._test_time).to_msg()
        node._cones_cb(observed)
        node._tick()
    assert node._route is None
    # The now-visible timing cones are occluded; their measured positions survive.
    node._pose[1] = -13.5
    for marker in observed.markers:
        if abs(marker.pose.position.y) < 1.:
            marker.ns = 'unknown_cones'
    for _ in range(15):
        node._test_time += .2
        node._odom_time = node.get_clock().now().nanoseconds / 1e9
        for marker in observed.markers:
            marker.header.stamp = Time(seconds=node._test_time).to_msg()
        node._cones_cb(observed)
        node._tick()
    assert node._route is not None
