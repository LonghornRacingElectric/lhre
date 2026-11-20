"""Check pose queuing and lag against observed Gazebo poses, not service ACKs."""

from geometry_msgs.msg import TransformStamped
from lhr_gazebo.fmu_pose import FmuPose
from nav_msgs.msg import Odometry
import pytest
import rclpy
from rclpy.parameter import Parameter
from rclpy.task import Future
from rclpy.time import Time
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import PointCloud2
from tf2_msgs.msg import TFMessage


@pytest.fixture
def node():
    rclpy.init(args=['--ros-args', '-p', 'lockstep:=false'])
    result = FmuPose()
    yield result
    result.destroy_node()
    rclpy.shutdown()


class Client:
    def __init__(self):
        self.requests = []
        self.futures = []

    def service_is_ready(self):
        return True

    def call_async(self, request):
        self.requests.append(request)
        future = Future()
        self.futures.append(future)
        return future


def odom(time, x):
    message = Odometry()
    message.header.stamp = Time(seconds=time).to_msg()
    message.pose.pose.position.x = x
    message.pose.pose.orientation.w = 1.
    return message


def test_pose_requests_coalesce_without_stale_queue(node):
    from ros_gz_interfaces.srv import SetEntityPose
    client = node._client = Client()
    node._truth(odom(1., 1.))
    node._truth(odom(1.02, 2.))
    node._truth(odom(1.04, 3.))
    assert len(client.requests) == 1
    client.futures[0].set_result(SetEntityPose.Response(success=True))
    node._send()
    assert [request.pose.position.x for request in client.requests] == [1., 3.]
    assert client.requests[-1].entity.name == 'fsae_vehicle'


def test_rejected_pose_fails_instead_of_leaving_frozen_sensor_car(node):
    from ros_gz_interfaces.srv import SetEntityPose
    node._client = Client()
    node._truth(odom(1., 1.))
    # Resolve a standalone future so an executor callback cannot swallow the error.
    node._future = Future()
    node._future.set_result(SetEntityPose.Response(success=False))
    with pytest.raises(RuntimeError, match='rejected'):
        node._send()


def test_lag_uses_observed_pose_and_does_not_match_future_state(node):
    node._client = Client()
    for t, x in ((1., 1.), (1.02, 2.), (1.04, 2.)):
        node._truth(odom(t, x))
    observed = TransformStamped()
    observed.child_frame_id = 'fsae_vehicle'
    observed.header.stamp = Time(seconds=1.03).to_msg()
    observed.transform.translation.x = 2.
    observed.transform.rotation.w = 1.
    node._render_pose(TFMessage(transforms=[observed]))
    sensor = PointCloud2()
    sensor.header.stamp = Time(seconds=1.035).to_msg()
    node._sensor(sensor, 'cloud')
    count, total, maximum = node._lag['cloud']
    assert count == 1
    assert total == pytest.approx(.015)
    assert maximum == pytest.approx(.015)


def test_unmatched_render_pose_cannot_claim_zero_lag(node):
    sensor = PointCloud2()
    sensor.header.stamp = Time(seconds=1.).to_msg()
    node._sensor(sensor, 'cloud')
    assert node._lag == {}


def test_lost_pose_response_fails_on_wall_time(node):
    node._client = Client()
    node._truth(odom(1., 1.))
    node._request_started -= 10.
    with pytest.raises(RuntimeError, match='response timed out'):
        node._send()


def test_missing_pose_service_cannot_wait_forever_on_paused_clock(node):
    from types import SimpleNamespace
    node._client = SimpleNamespace(service_is_ready=lambda: False)
    node.set_parameters([Parameter('startup_timeout_sec', value=5.)])
    node._started -= 10.
    with pytest.raises(RuntimeError, match='unavailable'):
        node._send()


def test_bootstrap_waits_for_gazebo_clock_not_just_bridge_services(coordinated):
    from std_msgs.msg import String
    node = coordinated
    node._clock_seen = False
    node._provenance(String(data='{"plant":"fmu"}'))
    assert not node._control.requests
    node._gazebo_clock(Clock())
    assert len(node._control.requests) == 1


def test_startup_can_exceed_service_timeout_but_is_bounded(coordinated, monkeypatch):
    node = coordinated
    monkeypatch.setattr('lhr_gazebo.fmu_pose.time.monotonic', lambda: node._started + 10.)
    node._send()
    assert not node._control.requests
    monkeypatch.setattr('lhr_gazebo.fmu_pose.time.monotonic', lambda: node._started + 61.)
    with pytest.raises(RuntimeError, match='unavailable'):
        node._send()


def test_slow_fmu_step_has_its_own_deadline(coordinated, monkeypatch):
    node = coordinated
    node._ready = node._startup_complete = True
    node._cycle_target_ns = 21_000_000
    node._bootstrap = False
    monkeypatch.setattr('lhr_gazebo.fmu_pose.time.monotonic', lambda: node._cycle_started + 10.)
    node._send()
    monkeypatch.setattr('lhr_gazebo.fmu_pose.time.monotonic', lambda: node._cycle_started + 31.)
    with pytest.raises(RuntimeError, match='did not produce'):
        node._send()


def test_world_control_response_keeps_short_service_deadline(coordinated, monkeypatch):
    node = coordinated
    node._startup_complete = True
    node._step_world(node._physics_ns)
    monkeypatch.setattr('lhr_gazebo.fmu_pose.time.monotonic', lambda: node._cycle_started + 6.)
    with pytest.raises(RuntimeError, match='world-control response timed out'):
        node._send()


@pytest.mark.parametrize('name', [
    'request_timeout_sec', 'startup_timeout_sec', 'step_timeout_sec'])
@pytest.mark.parametrize('value', ['0.0', '-1.0', '.nan', '.inf'])
def test_invalid_timeouts_rejected_at_startup(name, value):
    rclpy.init(args=['--ros-args', '-p', f'{name}:={value}'])
    try:
        with pytest.raises(ValueError, match=f'{name} must be finite and positive'):
            FmuPose()
    finally:
        rclpy.shutdown()


def test_pose_observer_subscribes_to_selected_rendered_cloud():
    rclpy.init(args=['--ros-args', '-p', 'lidar_topic:=/lhr/lidar/gazebo_points'])
    node = FmuPose()
    try:
        topics = {subscription.topic_name for subscription in node.subscriptions}
        assert '/lhr/lidar/gazebo_points' in topics
        assert '/lhr/lidar/points' not in topics
    finally:
        node.destroy_node()
        rclpy.shutdown()


@pytest.fixture
def coordinated():
    rclpy.init()
    result = FmuPose()
    result._client, result._control = Client(), Client()
    result._gazebo_clock(Clock())
    yield result
    result.destroy_node()
    rclpy.shutdown()


def test_world_waits_for_fmu_readiness_and_applied_pose(coordinated):
    from ros_gz_interfaces.srv import ControlWorld, SetEntityPose
    from std_msgs.msg import String
    node = coordinated
    node._send()
    assert node._control.requests == []
    node._provenance(String(data='{"plant":"fmu"}'))
    assert len(node._control.requests) == 1
    assert node._control.requests[0].world_control.pause
    node._control.futures[0].set_result(ControlWorld.Response(success=True))
    node._send()
    assert not node._client.requests
    assert len(node._control.requests) == 1
    node._truth(odom(.001, 0.))
    assert len(node._client.requests) == 1
    assert len(node._control.requests) == 1
    node._client.futures[0].set_result(SetEntityPose.Response(success=True))
    node._send()
    assert len(node._control.requests) == 2
    assert node._control.requests[0].world_control.multi_step == 2
    assert node._control.requests[-1].world_control.multi_step == 20
    assert node._cycle_target_ns == 21_000_000
    node._control.futures[-1].set_result(ControlWorld.Response(success=True))
    node._send()
    assert len(node._control.requests) == 2
    node.set_parameters([Parameter('step_timeout_sec', value=5.)])
    node._cycle_started -= 10.
    with pytest.raises(RuntimeError, match='did not produce'):
        node._send()


def test_external_unpause_cannot_silently_run_ahead(coordinated):
    node = coordinated
    node._cycle_target_ns = 21_000_000
    node._bootstrap = False
    with pytest.raises(RuntimeError, match='ran ahead'):
        node._truth(odom(.041, 1.))


def test_rejected_world_control_stops_the_coordinator(coordinated):
    from ros_gz_interfaces.srv import ControlWorld
    node = coordinated
    node._control_future = Future()
    node._control_future.set_result(ControlWorld.Response(success=False))
    with pytest.raises(RuntimeError, match='rejected the coordinated'):
        node._send()


def test_bootstrap_accepts_latest_clock_in_initial_two_ticks(coordinated):
    from ros_gz_interfaces.srv import ControlWorld
    from std_msgs.msg import String
    node = coordinated
    node._provenance(String(data='{"plant":"fmu"}'))
    node._control.futures[0].set_result(ControlWorld.Response(success=True))
    node._truth(odom(.002, 0.))
    assert node._cycle_target_ns == 2_000_000
    assert len(node._client.requests) == 1
