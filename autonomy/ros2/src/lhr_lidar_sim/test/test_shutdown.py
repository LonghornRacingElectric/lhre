"""Keep executor shutdown races separate from live sensor failures."""

from lhr_lidar_sim import lidar_sim_node
import pytest
import rclpy


@pytest.mark.parametrize('during_shutdown', [False, True])
def test_executor_runtime_error_only_exits_cleanly_after_shutdown(monkeypatch, during_shutdown):
    """A disposed subscription must not hide a runtime error while ROS is live."""
    def interrupted_spin(node):
        if during_shutdown:
            rclpy.shutdown()
        raise RuntimeError('subscription handle disposed')

    monkeypatch.setattr(rclpy, 'spin', interrupted_spin)
    if during_shutdown:
        lidar_sim_node.main()
    else:
        with pytest.raises(RuntimeError, match='subscription handle disposed'):
            lidar_sim_node.main()
    assert not rclpy.ok()
