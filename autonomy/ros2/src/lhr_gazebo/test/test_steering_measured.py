"""joint_cmd_adapter reports Gazebo's steering joints as the one angle the car's encoder will."""

from lhr_gazebo.joint_cmd_adapter import bicycle_angle, JointCmdAdapter
import pytest
import rclpy
from sensor_msgs.msg import JointState


class _Recorder:

    def __init__(self):
        self.msgs = []

    def publish(self, msg):
        self.msgs.append(msg)


@pytest.fixture
def adapter():
    rclpy.init()
    node = JointCmdAdapter()
    node._pub_measured = _Recorder()
    yield node
    node.destroy_node()
    rclpy.shutdown()


@pytest.mark.parametrize('center', [-0.55, -0.3, -0.05, 0.0, 1e-4, 0.05, 0.3, 0.55])
def test_bicycle_angle_inverts_the_ackermann_split(adapter, center):
    left, right = adapter._ackermann_angles(center)
    assert bicycle_angle(left, right) == pytest.approx(center, abs=1e-6)


def test_joint_states_become_one_measured_angle(adapter):
    left, right = adapter._ackermann_angles(0.2)
    msg = JointState()
    msg.header.stamp.sec = 12
    # Order and extra joints are not guaranteed; names decide.
    msg.name = ['front_right_steering_joint', 'other_joint', 'front_left_steering_joint']
    msg.position = [right, 9.0, left]
    adapter._joints_cb(msg)
    (out,) = adapter._pub_measured.msgs
    assert out.name == ['steering']
    assert out.position[0] == pytest.approx(0.2, abs=1e-9)
    assert out.header.stamp.sec == 12


def test_a_missing_steering_joint_publishes_nothing(adapter):
    msg = JointState()
    msg.name = ['front_left_steering_joint']
    msg.position = [0.1]
    adapter._joints_cb(msg)
    assert adapter._pub_measured.msgs == []
