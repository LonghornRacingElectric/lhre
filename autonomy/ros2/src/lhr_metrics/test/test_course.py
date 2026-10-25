"""Whole-vehicle checks must catch excursions and contacts between truth samples."""

import math

from lhr_metrics.course import CourseEnvelope
from lhr_vehicle import load_vehicle
import pytest


def lane(half_width=1.75, cones=()):
    return CourseEnvelope([(-10., half_width), (20., half_width)],
                          [(-10., -half_width), (20., -half_width)],
                          cones, load_vehicle(), closed=False)


def test_envelope_covers_yaml_chassis_and_wheel_steering_sweep():
    vehicle, course = load_vehicle(), lane()
    assert course.front >= vehicle.chassis_center_x_m + vehicle.body_length_m/2
    assert course.rear <= vehicle.chassis_center_x_m - vehicle.body_length_m/2
    for axle in (0., vehicle.wheelbase_m):
        for side in (-1., 1.):
            for steering in (-vehicle.max_steer_rad, 0., vehicle.max_steer_rad):
                for x, y in ((-vehicle.wheel_radius_m, -vehicle.wheel_width_m/2),
                             (vehicle.wheel_radius_m, vehicle.wheel_width_m/2)):
                    px = axle + math.cos(steering)*x - math.sin(steering)*y
                    py = side*vehicle.half_track_m + math.sin(steering)*x + math.cos(steering)*y
                    assert course.rear <= px <= course.front
                    assert abs(py) <= course.half_width


def test_center_inside_lane_does_not_prove_whole_vehicle_is_inside():
    course = lane()
    assert course.contains(0., 1.)
    assert course.pose_check(0., 0., 0.) == (False, set())
    assert course.pose_check(0., 1., 0.) == (True, set())


def test_rotated_car_can_cross_boundary_while_same_center_is_safe():
    course = lane()
    assert not course.pose_check(0., 0., 0.)[0]
    assert course.pose_check(0., 0., math.pi/2)[0]


def test_open_course_end_cap_does_not_allow_car_to_extend_past_the_lane():
    assert lane().pose_check(19., 0., 0.)[0]
    assert not lane().pose_check(17., 0., 0.)[0]


@pytest.mark.parametrize('kind', ['blue', 'yellow', 'orange_small', 'orange_large'])
def test_each_nominal_cone_kind_is_a_contact_and_counted_once(kind):
    course = lane(4., [('cone', 3., 0., .4, kind)])
    assert course.observe(0., 0., 0.) == (False, set())
    assert course.observe(1., 0., 0.) == (False, {'cone'})
    course.observe(1., 0., 0.)
    assert course.hit_ids == {'cone'}


def test_contact_between_clear_endpoints_is_detected():
    course = lane(4., [('cone', 2., 0., 0., 'blue')])
    assert not course.pose_check(-2., 0., 0.)[1]
    assert not course.pose_check(5., 0., 0.)[1]
    course.observe(-2., 0., 0.)
    assert course.observe(5., 0., 0.) == (False, {'cone'})


def test_rotation_sweep_catches_excursion_between_valid_endpoints():
    course = lane(1.5)
    assert not course.observe(0., 0., 0.)[0]
    assert not course.pose_check(0., 0., math.pi)[0]
    assert course.observe(0., 0., math.pi)[0]


def test_rotation_sweep_catches_cone_between_clear_endpoints():
    course = lane(4., [('cone', 0., 1.5, 0., 'blue')])
    assert not course.observe(0., 0., -math.pi)[1]
    assert not course.pose_check(0., 0., 0.)[1]
    assert course.observe(0., 0., 0.)[1] == {'cone'}


def test_yaw_wrap_uses_short_rotation_instead_of_a_full_turn():
    course = lane(1.5)
    assert not course.observe(0., 0., math.pi-.01)[0]
    assert not course.observe(0., 0., -math.pi+.01)[0]


def test_telemetry_jump_does_not_claim_unobserved_contacts():
    course = lane(4., [('cone', 2., 0., 0., 'blue')])
    course.observe(-2., 0., 0.)
    assert not course.observe(5., 0., 0., continuous=False)[1]
    assert not course.hit_ids


def test_closed_course_island_is_outside_even_inside_the_outer_boundary():
    course = CourseEnvelope([(2., 2.), (8., 2.), (8., 8.), (2., 8.)],
                            [(-2., -2.), (12., -2.), (12., 12.), (-2., 12.)],
                            [], load_vehicle(), closed=True)
    assert course.contains(5., 0.)
    assert not course.contains(5., 5.)
    assert course.pose_check(5., 5., 0.)[0]


def test_course_hash_changes_when_boundary_contact_geometry_changes():
    plain = lane()
    cone = lane(cones=[('cone', 2., 0., 0., 'blue')])
    assert plain.sha256 == lane().sha256
    assert plain.sha256 != lane(2.).sha256
    assert plain.sha256 != cone.sha256
    assert cone.sha256 != lane(cones=[('cone', 2., 0., 0., 'orange_large')]).sha256


@pytest.mark.parametrize('pose', [(float('nan'), 0., 0.), (0., 0., float('inf'))])
def test_invalid_pose_is_rejected(pose):
    with pytest.raises(ValueError, match='finite'):
        lane().observe(*pose)


def test_boundary_fully_inside_footprint_is_not_missed_by_corner_tests():
    course = CourseEnvelope([(.6, -.1), (.8, -.1), (.8, .1), (.6, .1)],
                            [(-4., -4.), (4., -4.), (4., 4.), (-4., 4.)],
                            [], load_vehicle(), closed=True)
    from lhr_metrics.course import rectangle
    corners = rectangle(0., 0., 0., course.rear, course.front, course.half_width)
    assert all(course.contains(*corner) for corner in corners)
    assert course.pose_check(0., 0., 0.)[0]


def test_square_cone_base_tangency_counts_but_a_real_gap_does_not():
    from lhr_trackgen.cone_geometry import CONE_SPECS
    half = CONE_SPECS['blue'].base_width_m/2
    vehicle_half = lane().half_width
    touch = lane(4., [('cone', 1., vehicle_half+half, 0., 'blue')])
    clear = lane(4., [('cone', 1., vehicle_half+half+.001, 0., 'blue')])
    assert touch.pose_check(0., 0., 0.)[1] == {'cone'}
    assert not clear.pose_check(0., 0., 0.)[1]
