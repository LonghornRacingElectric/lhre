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
    rclpy.init()
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
