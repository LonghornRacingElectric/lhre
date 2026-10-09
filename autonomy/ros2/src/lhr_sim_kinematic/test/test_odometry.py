"""Check odometry reports velocity in the declared child frame."""

import math
from unittest.mock import Mock

from lhr_sim_kinematic.sim_node import SimKinematic
import pytest
import rclpy


@pytest.mark.parametrize('yaw', [0., math.pi / 2., math.pi])
def test_forward_speed_does_not_change_sign_with_map_heading(yaw):
    rclpy.init()
    node = SimKinematic()
    try:
        node._yaw, node._v = yaw, 5.
        node._odom_pub = Mock()
        node._step()
        odom = node._odom_pub.publish.call_args.args[0]
        assert odom.child_frame_id == 'base_link'
        assert odom.twist.twist.linear.x == 5.
        assert odom.twist.twist.linear.y == 0.
    finally:
        node.destroy_node()
        rclpy.shutdown()
