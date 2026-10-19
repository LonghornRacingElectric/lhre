"""Verify pure pursuit's open-path endpoints and zero-speed behavior."""

from ackermann_msgs.msg import AckermannDriveStamped
from lhr_control.pursuit_node import PurePursuit
from nav_msgs.msg import Odometry, Path
import rclpy
from std_msgs.msg import Float32, String


def test_open_path_does_not_wrap_and_stops_on_input_loss(monkeypatch):
    """An open path must never choose its beginning as a forward lookahead."""
    rclpy.init(args=['--ros-args', '-p', 'path_closed:=false'])
    node = PurePursuit()
    try:
        commands = []
        monkeypatch.setattr(node._cmd_pub, 'publish', commands.append)
        node._path = [(float(i), 0.) for i in range(20)]
        node._x = 18.8
        assert node._find_lookahead() == (19, 19., 0.)
        assert node._estimate_curvature(19) == 0
        odom = Odometry()
        odom.pose.pose.position.x = 18.8
        odom.pose.pose.orientation.w = 1.
        node._odom_cb(odom)
        node._status_cb(String(data='DRIVING'))
        node._path_time = node.get_clock().now()
        node._remaining_cb(Float32(data=0.))
        node._v_prev = 0.
        node._control_loop()
        assert isinstance(commands[-1], AckermannDriveStamped)
        assert commands[-1].drive.speed == 0
        node._x = 10.
        node._remaining_cb(Float32(data=10.))
        node._control_loop()
        assert 0 < commands[-1].drive.speed < node._v_min
        node._path_cb(Path())
        node._control_loop()
        assert commands[-1].drive.speed == 0
        node._path = [(float(i), 0.) for i in range(20)]
        node._path_time -= rclpy.duration.Duration(seconds=2)
        node._control_loop()
        assert commands[-1].drive.speed == 0
    finally:
        node.destroy_node()
        rclpy.shutdown()
