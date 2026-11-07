"""Check pose queuing and lag against observed Gazebo poses, not service ACKs."""

from geometry_msgs.msg import TransformStamped
from lhr_gazebo.fmu_pose import FmuPose
from nav_msgs.msg import Odometry
import pytest
import rclpy
from rclpy.task import Future
from rclpy.time import Time
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
    node._started -= 10.
    with pytest.raises(RuntimeError, match='unavailable'):
        node._send()


@pytest.fixture
def coordinated():
    rclpy.init()
    result = FmuPose()
    result._client, result._control = Client(), Client()
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
