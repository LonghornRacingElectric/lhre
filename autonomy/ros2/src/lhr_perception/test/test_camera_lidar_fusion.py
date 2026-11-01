"""Exercise fusion using real ROS messages and timestamped TF when available."""

from copy import deepcopy
import importlib.util
import time

import numpy as np
import pytest


FUSION_MODULE = 'lhr_perception.camera_lidar_fusion'


def test_fusion_node_is_available():
    """The installed executable must have an implementation."""
    assert importlib.util.find_spec(FUSION_MODULE) is not None


@pytest.fixture
def node():
    """Use the real node, isolating DDS from user simulations."""
    rclpy = pytest.importorskip('rclpy')
    from rclpy.context import Context
    from rclpy.parameter import Parameter
    from lhr_perception.camera_lidar_fusion import CameraLidarFusion
    context = Context()
    rclpy.init(args=[], context=context, domain_id=84)
    instance = CameraLidarFusion(
        context=context, parameter_overrides=[Parameter('use_sim_time', value=True)])
    yield instance
    instance.destroy_node()
    context.shutdown()


def snapshot(stamp=10, namespace='left_cones', x=0., cone_id=7):
    """Build a planar map; its displayed side must not become a camera color."""
    from visualization_msgs.msg import Marker, MarkerArray
    marker = Marker()
    marker.id, marker.ns = cone_id, namespace
    marker.header.frame_id, marker.header.stamp.sec = 'map', stamp
    marker.type = Marker.SPHERE
    marker.pose.position.x, marker.pose.position.z = x, 5.
    marker.pose.orientation.w = 1.
    marker.scale.x = marker.scale.y = marker.scale.z = .35
    return MarkerArray(markers=[marker])


def camera_pair(stamp=10, color=(0, 0, 255)):
    """Provide the ideal left calibration and a colored patch at the center."""
    from sensor_msgs.msg import CameraInfo, Image
    info = CameraInfo(width=100, height=80)
    info.header.frame_id, info.header.stamp.sec = 'camera_optical', stamp
    info.p = [100., 0., 50., 0., 0., 100., 40., 0., 0., 0., 1., 0.]
    info.r = [1., 0., 0., 0., 1., 0., 0., 0., 1.]
    info.d = [0.] * 5
    image = Image(width=100, height=80, step=300, encoding='rgb8')
    image.header = deepcopy(info.header)
    rgb = np.zeros((80, 100, 3), dtype=np.uint8)
    rgb[35:51, 45:56] = color
    image.data = rgb.tobytes()
    return image, info


def insert_tf(node, stamp=10, x=0., static=False):
    """Supply a map-to-optical transform; no latest-TF substitute is needed."""
    from geometry_msgs.msg import TransformStamped
    transform = TransformStamped()
    transform.header.frame_id, transform.child_frame_id = 'camera_optical', 'map'
    transform.header.stamp.sec = stamp
    transform.transform.rotation.w = 1.
    transform.transform.translation.x = x
    if static:
        node._tf_buffer.set_transform_static(transform, 'test')
    else:
        node._tf_buffer.set_transform(transform, 'test')


def observe(node, stamp, color=(0, 0, 255), tf=True):
    """Feed one independent image and run one fusion tick."""
    node._map_cb(snapshot(stamp))
    image, info = camera_pair(stamp, color)
    node._info_cb(info)
    node._image_cb(image)
    if tf:
        insert_tf(node, stamp)
    node._process()


def test_map_without_camera_is_unknown_and_positions_and_ids_are_preserved(node):
    """Inferred display namespaces are never used as measured colors."""
    original = snapshot()
    node._map_cb(original)
    output = node._output_snapshot()
    assert len(output.markers) == 1
    marker = output.markers[0]
    assert (marker.id, marker.ns) == (7, 'unknown_cones')
    assert marker.pose == original.markers[0].pose
    assert marker.header == original.markers[0].header
    assert marker.scale == original.markers[0].scale
    assert original.markers[0].ns == 'left_cones'


def test_colors_confirm_from_three_distinct_images_and_missing_camera_keeps_map(node):
    """The map is always available, with measured colors only after confirmation."""
    for stamp in (10, 11, 12):
        observe(node, stamp)
    assert node._output_snapshot().markers[0].ns == 'blue_cones'
    image, info = camera_pair(12, (255, 255, 0))
    node._info_cb(info)
    node._image_cb(image)
    node._process()
    assert node._output_snapshot().markers[0].ns == 'blue_cones'
    node._map_cb(snapshot(13))
    assert node._output_snapshot().markers[0].ns == 'blue_cones'


def test_pending_image_retries_tf_but_never_uses_latest_pose(node):
    """A transform at a different timestamp must not color a cone."""
    observe(node, 10, tf=False)
    assert node._processed_stamp == 0
    insert_tf(node, 11, x=10.)
    node._process()
    assert node._processed_stamp == 0
    insert_tf(node, 10)
    node._process()
    assert node._processed_stamp == 10_000_000_000


@pytest.mark.parametrize('change', ['stamp', 'frame', 'size', 'distortion', 'crop'])
def test_incompatible_camera_info_never_votes(node, change):
    """Same-time, same-frame, same-size rectified calibration is required."""
    node._map_cb(snapshot())
    image, info = camera_pair()
    if change == 'stamp':
        info.header.stamp.sec = 9
    elif change == 'frame':
        info.header.frame_id = 'other_camera'
    elif change == 'size':
        info.width = 99
    elif change == 'distortion':
        info.d = [.1, 0., 0., 0., 0.]
    else:
        info.roi.x_offset = 1
    insert_tf(node)
    node._info_cb(info)
    node._image_cb(image)
    node._process()
    assert node._evidence.labels() == {7: None}
    assert node._processed_stamp == 0


def test_future_or_stale_map_cannot_color_current_image(node):
    """A map must predate the image and be inside the configured age gate."""
    node._map_cb(snapshot(11))
    image, info = camera_pair(10)
    insert_tf(node, 10)
    node._info_cb(info)
    node._image_cb(image)
    node._process()
    assert node._processed_stamp == 0
    node._map_cb(snapshot(12))
    image, info = camera_pair(13)
    insert_tf(node, 13)
    node._info_cb(info)
    node._image_cb(image)
    node._process()
    assert node._processed_stamp == 0


def test_namespace_transitions_and_removal_do_not_leave_duplicate_markers(node):
    """Source display transitions keep identity; output transitions delete old keys."""
    from visualization_msgs.msg import Marker, MarkerArray
    node._map_cb(snapshot())
    node._publish_map()
    moved = snapshot(11, 'right_cones')
    deletion = deepcopy(moved.markers[0])
    deletion.ns, deletion.action = 'left_cones', Marker.DELETE
    moved.markers.insert(0, deletion)
    node._map_cb(moved)
    assert node._evidence.labels() == {7: None}
    for stamp in (12, 13, 14):
        observe(node, stamp)
    node._published_keys = {('unknown_cones', 7)}
    output = node._output_snapshot()
    assert [(m.ns, m.id, m.action) for m in output.markers] == [
        ('unknown_cones', 7, Marker.DELETE), ('blue_cones', 7, Marker.ADD)]
    node._map_cb(MarkerArray())
    assert not node._current_map
    assert node._evidence.labels() == {}


def test_malformed_snapshots_are_rejected_atomically(node):
    """Duplicate IDs and mixed frames/times cannot partially replace the map."""
    from visualization_msgs.msg import MarkerArray
    node._map_cb(snapshot())
    for change in ('id', 'frame', 'stamp', 'nan'):
        first = snapshot(11).markers[0]
        second = deepcopy(first)
        second.id = 8
        if change == 'id':
            second.id, second.pose.position.x = 7, 2.
        elif change == 'frame':
            second.header.frame_id = 'base_link'
        elif change == 'stamp':
            second.header.stamp.sec = 12
        else:
            second.pose.position.x = float('nan')
        node._map_cb(MarkerArray(markers=[first, second]))
        assert list(node._current_map) == [7]
        assert node._last_map_stamp == 10_000_000_000


def test_zero_stamp_bad_image_and_replay_restart_do_not_reuse_evidence(node):
    """A replay reset discards previous colors, maps, and pending images."""
    for stamp in (10, 11, 12):
        observe(node, stamp)
    image, info = camera_pair(0)
    node._info_cb(info)
    node._image_cb(image)
    node._process()
    assert node._evidence.labels() == {7: 'blue'}
    image, info = camera_pair(13)
    image.encoding = 'mono8'
    node._info_cb(info)
    node._image_cb(image)
    node._process()
    assert node._evidence.labels() == {7: 'blue'}
    node._map_cb(snapshot(1))
    assert node._evidence.labels() == {7: None}
    assert len(node._map_history) == 1
    assert node._map_history[0][0] == 1_000_000_000


@pytest.mark.parametrize('seconds,nanoseconds', [(11, 900_000_000), (1, 0), (12, 0)])
def test_late_or_duplicate_image_preserves_map_votes_tf_and_orange(node, seconds, nanoseconds):
    """A delayed frame must not erase the map or let known orange enter pairing."""
    from rclpy.time import Time
    for stamp in (10, 11, 12):
        observe(node, stamp, color=(255, 100, 0))
    original = deepcopy(node._current_map)
    history = list(node._map_history)
    image, _ = camera_pair(seconds)
    image.header.stamp.nanosec = nanoseconds
    node._image_cb(image)
    assert node._current_map == original
    assert node._evidence.labels() == {7: 'orange'}
    assert list(node._map_history) == history
    assert node._last_image_stamp == node._processed_stamp == 12_000_000_000
    assert not node._images
    assert node._tf_buffer.can_transform('camera_optical', 'map', Time(seconds=12))
    assert node._classified_keys == {('orange_cones', 7)}
    # The next valid frame continues the same map and color evidence.
    observe(node, 13, color=(255, 100, 0))
    assert node._processed_stamp == 13_000_000_000
    assert node._evidence.labels() == {7: 'orange'}


def test_histories_are_bounded_even_when_tf_is_missing(node):
    """Fast publishers cannot build an unbounded queue while TF is absent."""
    for i in range(100):
        image, info = camera_pair(10)
        image.header.stamp.nanosec = info.header.stamp.nanosec = i
        node._image_cb(image)
        node._info_cb(info)
        msg = snapshot(10)
        msg.markers[0].header.stamp.nanosec = i
        node._map_cb(msg)
    assert len(node._images) <= 60
    assert len(node._infos) <= 60
    assert len(node._map_history) <= 30


def test_late_subscriber_receives_complete_colored_map(node):
    """Transient-local publication gives new viewers a full map immediately."""
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
    from visualization_msgs.msg import MarkerArray
    for stamp in (10, 11, 12):
        observe(node, stamp)
    viewer = rclpy.create_node('fusion_late_viewer', context=node.context)
    received = []
    viewer.create_subscription(
        MarkerArray, '/lhr/perception/cones_fused', received.append,
        QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                   reliability=ReliabilityPolicy.RELIABLE))
    executor = SingleThreadedExecutor(context=node.context)
    executor.add_node(viewer)
    deadline = time.monotonic() + 3.
    try:
        while not received and time.monotonic() < deadline:
            executor.spin_once(timeout_sec=.05)
        assert received
        adds = [m for m in received[-1].markers if m.action == m.ADD]
        assert [(m.id, m.ns) for m in adds] == [(7, 'blue_cones')]
    finally:
        executor.shutdown()
        viewer.destroy_node()


@pytest.mark.parametrize('reset_kind', ['map', 'clock'])
def test_reset_discards_previous_epoch_dynamic_tf_and_keeps_static_mount(node, reset_kind):
    """Reused timestamps must wait for the new pose, while rigid mounts survive."""
    from geometry_msgs.msg import TransformStamped
    from rclpy.time import Time
    mount = TransformStamped()
    mount.header.frame_id, mount.child_frame_id = 'camera_optical', 'rigid_mount'
    mount.transform.rotation.w = 1.
    node._tf_buffer.set_transform_static(mount, 'test')
    insert_tf(node, 10)
    observe(node, 11)
    node.get_clock().set_ros_time_override(Time(seconds=11.))
    node._process()
    if reset_kind == 'map':
        node._map_cb(snapshot(10))
    else:
        # Less than a second: TF's automatic jump callback does not clear this.
        node.get_clock().set_ros_time_override(Time(seconds=10.9))
        node._process()
    node._map_cb(snapshot(10))
    image, info = camera_pair(10)
    node._info_cb(info)
    node._image_cb(image)
    node._process()
    assert node._processed_stamp == 0
    assert node._tf_buffer.can_transform('camera_optical', 'rigid_mount', Time())
    insert_tf(node, 10)
    node._process()
    assert node._processed_stamp == 10_000_000_000


def test_deleteall_clears_map_pending_inputs_and_votes(node):
    """An upstream map reset cannot leave an old latched colored map."""
    from visualization_msgs.msg import Marker, MarkerArray
    observe(node, 10)
    reset = Marker(action=Marker.DELETEALL)
    node._map_cb(MarkerArray(markers=[reset]))
    assert not node._current_map
    assert not node._images and not node._infos and not node._map_history
    assert node._evidence.labels() == {}


def test_classified_map_uses_lidar_sides_before_camera_confirmation(node):
    """An absent camera must leave inferred sides available without fabricating color."""
    original = snapshot(namespace='right_cones')
    node._map_cb(original)
    marker = node._output_snapshot(classified=True).markers[0]
    assert marker.ns == 'right_cones'
    assert marker.id == 7
    assert marker.pose == original.markers[0].pose
    assert marker.header == original.markers[0].header
    assert node._output_snapshot().markers[0].ns == 'unknown_cones'
    node._map_cb(snapshot(11, namespace='cones'))
    assert node._output_snapshot(classified=True).markers[-1].ns == 'cones'


def test_camera_overrides_opposite_lidar_side_then_timer_expires_it(node):
    """Camera loss returns to the current LiDAR side without waiting for a new map."""
    from rclpy.time import Time
    from visualization_msgs.msg import Marker
    for stamp in (10, 11, 12):
        observe(node, stamp)
    node.get_clock().set_ros_time_override(Time(seconds=12.))
    node._map_cb(snapshot(12, namespace='right_cones'))
    assert node._output_snapshot(classified=True).markers[-1].ns == 'left_cones'
    assert node._classified_keys == {('left_cones', 7)}
    node.get_clock().set_ros_time_override(Time(seconds=12.6))
    output = node._output_snapshot(classified=True)
    assert [(m.ns, m.id, m.action) for m in output.markers] == [
        ('left_cones', 7, Marker.DELETE), ('right_cones', 7, Marker.ADD)]
    node._process()
    assert node._classified_keys == {('right_cones', 7)}
    assert node._output_snapshot().markers[-1].ns == 'blue_cones'


@pytest.mark.parametrize('reason', ['blank', 'conflict', 'ambiguous'])
def test_unusable_camera_observations_return_to_inferred_side(node, reason):
    """Old confirmed blue must not override LiDAR when the newest match is unusable."""
    from rclpy.time import Time
    for stamp in (10, 11, 12):
        observe(node, stamp)
    node.get_clock().set_ros_time_override(Time(seconds=13.))
    source = snapshot(13, namespace='right_cones')
    if reason == 'ambiguous':
        second = deepcopy(source.markers[0])
        second.id, second.pose.position.x = 8, .1
        source.markers.append(second)
    node._map_cb(source)
    image, info = camera_pair(13, (255, 255, 0) if reason == 'conflict' else (0, 0, 255))
    if reason == 'blank':
        image.data = bytes(image.height * image.step)
    node._info_cb(info)
    node._image_cb(image)
    insert_tf(node, 13)
    node._process()
    assert node._evidence.labels()[7] == 'blue'
    adds = [m for m in node._output_snapshot(classified=True).markers if m.action == m.ADD]
    assert all(m.ns == 'right_cones' for m in adds)


def test_observed_orange_is_not_reclassified_as_a_track_side(node):
    """A confirmed orange observation remains distinct from fallback boundary sides."""
    from rclpy.time import Time
    for stamp in (10, 11, 12):
        observe(node, stamp, color=(255, 100, 0))
    node.get_clock().set_ros_time_override(Time(seconds=12.))
    marker = node._output_snapshot(classified=True).markers[-1]
    assert marker.ns == 'orange_cones'
    assert marker.id == 7


@pytest.mark.parametrize('topic', ['/lhr/perception/cones_fused',
                                   '/lhr/perception/cones_classified'])
def test_slow_viewer_can_replace_map_from_latest_snapshot_alone(node, topic):
    """A depth-one subscriber may skip removal messages but must not keep old IDs."""
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
    from visualization_msgs.msg import Marker, MarkerArray
    node._map_cb(snapshot(10))
    viewer = rclpy.create_node('fusion_slow_viewer', context=node.context)
    received = []
    viewer.create_subscription(
        MarkerArray, topic, received.append,
        QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                   reliability=ReliabilityPolicy.RELIABLE))
    executor = SingleThreadedExecutor(context=node.context)
    executor.add_node(viewer)
    deadline = time.monotonic() + 3.
    try:
        while not received and time.monotonic() < deadline:
            executor.spin_once(timeout_sec=.05)
        assert received
        displayed = {(m.ns, m.id) for m in received[-1].markers if m.action == Marker.ADD}
        assert any(cone_id == 7 for _, cone_id in displayed)
        received.clear()
        node._map_cb(MarkerArray())
        node._map_cb(snapshot(20, cone_id=8))
        node._map_cb(snapshot(21, cone_id=8))
        latest = None
        deadline = time.monotonic() + 3.
        while latest is None and time.monotonic() < deadline:
            executor.spin_once(timeout_sec=.05)
            latest = next((msg for msg in received if any(
                m.action == Marker.ADD and m.header.stamp.sec == 21 for m in msg.markers)), None)
        assert latest is not None
        # Deliberately miss every transition sample, as a KEEP_LAST(1) viewer can.
        for marker in latest.markers:
            if marker.action == Marker.DELETEALL:
                displayed.clear()
            elif marker.action == Marker.DELETE:
                displayed.discard((marker.ns, marker.id))
            elif marker.action == Marker.ADD:
                displayed.add((marker.ns, marker.id))
        assert len(displayed) == 1
        assert next(iter(displayed))[1] == 8
    finally:
        executor.shutdown()
        viewer.destroy_node()
