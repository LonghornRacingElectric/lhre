"""Scoring and the mission's lap latch must agree with an analytic fixed gate."""

import time

from geometry_msgs.msg import PoseStamped
from lhr_metrics.metrics_node import MetricsNode
from lhr_mission_manager.mission_manager_node import MissionManager
from nav_msgs.msg import Odometry, Path
import pytest
import rclpy
from std_msgs.msg import Bool, String
from visualization_msgs.msg import Marker, MarkerArray

SQUARE = [(0., 0.), (10., 0.), (10., 10.), (0., 10.)]


def reference(points=SQUARE):
    result = MarkerArray()
    for namespace, shift, base in [('left_cones', -.5, 0), ('right_cones', .5, 10000)]:
        for index, (x, y) in enumerate(points):
            marker = Marker()
            marker.header.frame_id = 'map'
            marker.ns, marker.id = namespace, base + index
            marker.pose.position.x, marker.pose.position.y = x, y + shift
            result.markers.append(marker)
    return result


def odom(x, y, stamp, speed=5.):
    result = Odometry()
    result.header.frame_id, result.child_frame_id = 'map', 'base_link'
    sec, nanosec = divmod(round(stamp*1e9), 1000000000)
    result.header.stamp.sec, result.header.stamp.nanosec = sec, nanosec
    result.pose.pose.position.x, result.pose.pose.position.y = float(x), float(y)
    result.pose.pose.orientation.w = 1.
    result.twist.twist.linear.x = speed
    return result


def circuit():
    for a, b in zip(SQUARE, SQUARE[1:] + SQUARE[:1]):
        for index in range(10):
            yield a[0] + index*(b[0]-a[0])/10, a[1] + index*(b[1]-a[1])/10
    yield SQUARE[0]


@pytest.fixture
def metrics(tmp_path, request):
    extra = []
    for name, value in getattr(request, 'param', {}).items():
        extra += ['-p', f'{name}:={value}']
    rclpy.init(args=['--ros-args', '-p', f'output_csv:={tmp_path}/metrics.csv',
                     '-p', 'mission:=autocross', *extra])
    node = MetricsNode()
    yield node
    node.destroy_node()
    rclpy.shutdown()


def test_full_square_with_rolling_planner_and_mission_agreement(metrics):
    metrics._reference_cb(reference())
    sha = metrics._reference.sha256
    metrics._reference_cb(reference([(x+100., y) for x, y in SQUARE]))
    assert metrics._reference.sha256 == sha
    mission = MissionManager()
    planner = metrics.create_publisher(Path, '/lhr/track/centerline', 10)
    try:
        initial = Path()
        initial.poses.append(PoseStamped())
        mission._centerline_cb(initial)
        mission._tick()
        mission._go_cb(Bool(data=True))
        mission._tick()
        assert mission._state == 'DRIVING'
        metrics._status_cb(String(data='DRIVING'))
        deadline = time.monotonic() + 3.
        while metrics._lap_pub.get_subscription_count() == 0 and time.monotonic() < deadline:
            rclpy.spin_once(mission, timeout_sec=.01)
        assert metrics._lap_pub.get_subscription_count() > 0
        for index, (x, y) in enumerate(circuit()):
            moving = PoseStamped()
            moving.pose.position.x, moving.pose.position.y = x, y
            planner.publish(Path(poses=[moving]))
            message = odom(x, y, 10.+index*.2)
            mission._odom_cb(message)
            metrics._odom_cb(message)
            if index < 40:
                assert not metrics._lap_completed
                assert not mission._lap_complete
        deadline = time.monotonic() + 3.
        while not mission._lap_complete and time.monotonic() < deadline:
            rclpy.spin_once(mission, timeout_sec=.01)
        assert metrics._lap_completed and mission._lap_complete
        assert mission._state == 'DRIVING'
        assert metrics._cte.max_cte == pytest.approx(0., abs=1e-10)
        row = metrics._build_row()
        assert float(row['lap_time_s']) == pytest.approx(8.)
        assert float(row['lap_gate_x_m']) == 0.
        assert float(row['lap_gate_y_m']) == 0.
        assert float(row['lap_start_stamp_s']) == 10.
        assert float(row['duration_s']) == pytest.approx(8.)
        assert float(row['lap_progress_m']) == pytest.approx(40.)
        assert not row['lap_invalid_reason']
        # A verified flag may finish the mission only after it stops.
        mission._odom_cb(odom(0., 0., 18.2, speed=0.))
        mission._tick()
        assert mission._state == 'FINISHED'
    finally:
        metrics.destroy_publisher(planner)
        mission.destroy_node()


def test_false_early_return_and_index_coverage_changes_do_not_count(metrics):
    metrics._reference_cb(reference())
    for index, x in enumerate([0., 4., 9., 8., 4., 0.]):
        metrics._odom_cb(odom(x, 0., 10.+index))
        # A partial or reordered observed map is not a new scoring reference.
        metrics._reference_cb(reference(SQUARE[:2] if index % 2 else list(reversed(SQUARE))))
    assert not metrics._lap_completed
    assert metrics._outcome != 'lap'


def test_pre_drive_wait_and_paused_simulation_do_not_add_time_or_progress(metrics):
    metrics._reference_cb(reference())
    metrics._status_cb(String(data='READY'))
    metrics._odom_cb(odom(0., 0., 1., speed=0.))
    metrics._odom_cb(odom(0., 0., 100., speed=0.))
    assert metrics._cte.samples == 0
    metrics._status_cb(String(data='DRIVING'))
    metrics._odom_cb(odom(0., 0., 100.2))
    for x, y in circuit():
        metrics._odom_cb(odom(x, y, 100.2))
    assert metrics._lap.progress == 0.
    assert metrics._build_row()['duration_s'] == '0.00'
    assert not metrics._lap_completed


def test_missing_reference_or_stamp_never_falls_back_to_planner_or_wall_clock(metrics):
    metrics._reference_cb(MarkerArray())
    metrics._odom_cb(odom(0., 0., 10.))
    assert metrics._cte.samples == 0
    assert metrics.finalize() == 1
    metrics._reference_cb(reference())
    metrics._odom_cb(odom(0., 0., 0.))
    assert metrics._cte.samples == 0
    assert metrics._lap.started_at is None


def test_late_first_observation_does_not_credit_unseen_course(metrics):
    metrics._reference_cb(reference())
    for index, (x, y) in enumerate(list(circuit())[20:]):
        metrics._odom_cb(odom(x, y, 100.+index*.2))
    assert not metrics._lap_completed
    assert metrics._lap.progress == pytest.approx(20.)


def test_external_finished_without_a_verified_autocross_lap_is_failure(metrics):
    metrics._reference_cb(reference())
    metrics._odom_cb(odom(0., 0., 10.))
    metrics._status_cb(String(data='FINISHED'))
    assert metrics._outcome == 'invalid_completion'
    assert metrics.finalize() == 1


@pytest.mark.parametrize('metrics', [{'truth_odom_topic': '/test/truth'}], indirect=True)
def test_biased_estimate_does_not_change_truth_cte_or_lap(metrics):
    metrics._reference_cb(reference())
    for index, (x, y) in enumerate(circuit()):
        metrics._truth_cb(odom(x, y, 10.+index*.2))
        metrics._odom_cb(odom(x+100., y+100., 10.+index*.2))
    assert metrics._lap_completed
    assert metrics._cte.max_cte == pytest.approx(0.)
    assert float(metrics._build_row()['mean_pos_error']) > 100.
    assert metrics._build_row()['scoring_odom_topic'] == '/test/truth'


@pytest.mark.parametrize('metrics', [{'truth_odom_topic': '/test/truth'}], indirect=True)
def test_estimate_cannot_hide_truth_off_course_or_replace_missing_truth(metrics):
    metrics._reference_cb(reference())
    metrics._odom_cb(odom(0., 0., 10.))
    assert metrics._cte.samples == 0
    metrics._truth_cb(odom(0., 0., 10.))
    metrics._odom_cb(odom(1., 0., 10.2))
    metrics._truth_cb(odom(100., 100., 10.2))
    assert metrics.finished
    assert metrics._outcome == 'invalid_lap'
    assert metrics._cte.max_cte > 100.
    assert not metrics._lap_completed
    assert metrics.finalize() == 1


def test_telemetry_gap_ends_the_run_with_a_failure(metrics):
    metrics._reference_cb(reference())
    metrics._odom_cb(odom(0., 0., 10.))
    metrics._odom_cb(odom(1., 0., 12.))
    assert metrics.finished
    assert metrics._outcome == 'invalid_lap'
    assert metrics._lap.invalid_reason == 'odometry_gap'
    assert metrics.finalize() == 1
