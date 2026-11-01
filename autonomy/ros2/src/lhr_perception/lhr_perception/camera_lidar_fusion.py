"""Attach camera color evidence to a separate, persistent LiDAR inspection map."""

from collections import deque
from copy import deepcopy

from lhr_perception.camera_lidar_geometry import (
    associate_regions,
    ColorEvidence,
    FUSION_DEFAULTS,
    project_cones,
    resolve_cone_namespace,
    validate_calibration,
    validate_settings,
)
from lhr_perception.cone_color import (
    classify_regions, decode_image, DEFAULT_MIN_AREA, DEFAULT_THRESHOLDS, validate_config)
from lhr_perception.transforms import rigid_transform_matrix
import numpy as np
import rclpy
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy, qos_profile_sensor_data, QoSProfile, ReliabilityPolicy)
from rclpy.time import Time
from sensor_msgs.msg import CameraInfo, Image
from std_msgs.msg import ColorRGBA
from tf2_ros import Buffer, TransformException, TransformListener
from visualization_msgs.msg import Marker, MarkerArray


HISTORY_NS = 2_000_000_000
MARKER_COLORS = {
    'blue_cones': (0., .3, 1.), 'left_cones': (0., .3, 1.),
    'yellow_cones': (1., 1., 0.), 'right_cones': (1., 1., 0.),
    'orange_cones': (1., .5, 0.), 'unknown_cones': (.5, .5, .5), 'cones': (.5, .5, .5),
}


def _stamp(header):
    return header.stamp.sec * 1_000_000_000 + header.stamp.nanosec


class CameraLidarFusion(Node):
    """Use exact image calibration and stamped TF without changing the planner."""

    def __init__(self, **kwargs):
        """Validate settings and subscribe to bounded camera and map streams."""
        super().__init__('camera_lidar_fusion', **kwargs)
        for name, value in FUSION_DEFAULTS.items():
            self.declare_parameter(name, value)
        self._settings = {name: self.get_parameter(name).value for name in FUSION_DEFAULTS}
        validate_settings(self._settings)
        self.declare_parameter('min_region_area', DEFAULT_MIN_AREA)
        for color, bounds in DEFAULT_THRESHOLDS.items():
            self.declare_parameter(f'{color}_hsv', list(bounds))
        self._thresholds = {color: self.get_parameter(f'{color}_hsv').value
                            for color in DEFAULT_THRESHOLDS}
        self._min_area = self.get_parameter('min_region_area').value
        validate_config(self._thresholds, self._min_area)
        self._tf_buffer = Buffer(cache_time=Duration(seconds=2.), node=self)
        self._tf_listener = TransformListener(self._tf_buffer, self)
        self._images = deque(maxlen=60)
        self._infos = deque(maxlen=60)
        self._map_history = deque(maxlen=30)
        self._current_map = {}
        self._published_keys = set()
        self._classified_keys = set()
        self._last_image_stamp = self._last_map_stamp = self._processed_stamp = 0
        self._newest_stamp = 0
        self._last_clock = self.get_clock().now().nanoseconds
        self._evidence = self._new_evidence()
        map_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                             durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self._pub = self.create_publisher(
            MarkerArray, '/lhr/perception/cones_fused', map_qos)
        self._classified_pub = self.create_publisher(
            MarkerArray, '/lhr/perception/cones_classified', map_qos)
        self.create_subscription(
            MarkerArray, '/lhr/sensor/cones_detected', self._map_cb, map_qos)
        self.create_subscription(
            Image, '/lhr/sensor/zed2i/left/image_rect', self._image_cb, qos_profile_sensor_data)
        self.create_subscription(
            CameraInfo, '/lhr/sensor/zed2i/left/camera_info', self._info_cb,
            qos_profile_sensor_data)
        self.create_timer(1. / self._settings['process_hz'], self._process)

    def _new_evidence(self):
        return ColorEvidence(self._settings['vote_window'], self._settings['min_color_votes'],
                             self._settings['min_color_fraction'],
                             self._settings['id_reset_distance_m'])

    def _reset(self):
        # Replayed timestamps must not resolve to poses from the old run.
        # TF's clear() preserves the static rig chain.
        self._tf_buffer.clear()
        self._images.clear()
        self._infos.clear()
        self._map_history.clear()
        self._current_map.clear()
        self._evidence = self._new_evidence()
        self._last_image_stamp = self._last_map_stamp = self._processed_stamp = 0
        self._newest_stamp = 0
        self._publish_map()

    def _prune(self, stamp):
        self._newest_stamp = max(stamp, self._newest_stamp)
        oldest = self._newest_stamp - HISTORY_NS
        for queue in (self._images, self._infos, self._map_history):
            retained = [entry for entry in queue if entry[0] >= oldest]
            queue.clear()
            queue.extend(retained)

    def _warn(self, error):
        self.get_logger().warning(str(error), throttle_duration_sec=5.)

    def _map_cb(self, msg):
        if not msg.markers or any(m.action == Marker.DELETEALL for m in msg.markers):
            self._reset()
            return
        adds = [m for m in msg.markers if m.action == Marker.ADD
                and m.ns in ('cones', 'left_cones', 'right_cones')]
        if not adds:
            self._warn('Fusion expects a complete LiDAR map snapshot')
            return
        stamp = _stamp(adds[0].header)
        current = {}
        for marker in adds:
            p = marker.pose.position
            if (stamp <= 0 or _stamp(marker.header) != stamp
                    or marker.header.frame_id != 'map'
                    or not np.isfinite((p.x, p.y, p.z)).all()
                    or (marker.id in current and current[marker.id] != marker)):
                self._warn('Rejecting malformed LiDAR map snapshot')
                return
            current[marker.id] = marker
        if stamp < self._last_map_stamp:
            self._reset()
        self._last_map_stamp = stamp
        self._current_map = current
        positions = self._positions(current)
        generations = self._evidence.update_map(positions)
        self._map_history.append((stamp, positions, generations))
        self._prune(stamp)
        self._publish_map()

    @staticmethod
    def _positions(markers):
        return {key: (m.pose.position.x, m.pose.position.y, m.pose.position.z)
                for key, m in markers.items()}

    def _image_cb(self, msg):
        stamp = _stamp(msg.header)
        if stamp <= 0 or not msg.header.frame_id:
            self._warn('Fusion requires positive image stamps and an optical frame')
            return
        if stamp < self._last_image_stamp:
            # A late image is not evidence that the map or ROS clock restarted.
            return
        self._last_image_stamp = stamp
        if stamp > self._processed_stamp:
            self._images.append((stamp, msg))
            self._prune(stamp)

    def _info_cb(self, msg):
        stamp = _stamp(msg.header)
        if stamp > 0:
            self._infos.append((stamp, msg))
            self._prune(stamp)

    def _process(self):
        now = self.get_clock().now().nanoseconds
        if now < self._last_clock:
            self._reset()
        self._last_clock = now
        self._prune(now)
        for stamp, image in reversed(self._images):
            if stamp <= self._processed_stamp:
                continue
            info = next((info for time, info in reversed(self._infos)
                         if time == stamp and info.header.frame_id == image.header.frame_id
                         and (info.width, info.height) == (image.width, image.height)), None)
            snapshot = next((entry for entry in reversed(self._map_history)
                             if 0 <= stamp - entry[0]
                             <= self._settings['map_max_age_sec'] * 1e9), None)
            if info is None or snapshot is None:
                continue
            try:
                validate_calibration(info)
                transform = self._tf_buffer.lookup_transform(
                    image.header.frame_id, 'map', Time.from_msg(image.header.stamp))
                pixels = project_cones(
                    snapshot[1], rigid_transform_matrix(transform.transform), info,
                    self._settings['sample_height_m'], self._settings['max_color_range_m'])
                rgb = decode_image(
                    image.data, image.width, image.height, image.step, image.encoding)
                regions = classify_regions(rgb, self._thresholds, self._min_area)
            except (ValueError, TransformException) as error:
                self._warn(error)
                continue
            colors = associate_regions(pixels, regions, self._settings['pixel_margin'])
            self._evidence.observe(colors, stamp, snapshot[2])
            self._processed_stamp = stamp
            self._images = deque((entry for entry in self._images if entry[0] > stamp), maxlen=60)
            self._publish_map()
            return
        # Camera loss must update the latched fallback even if LiDAR is idle.
        self._publish_classified()

    def _output_snapshot(self, *, classified=False):
        if classified:
            labels = self._evidence.planning_labels(
                self.get_clock().now().nanoseconds, self._settings['camera_max_age_sec'] * 1e9)
            namespaces = {key: resolve_cone_namespace(labels.get(key), source.ns)
                          for key, source in self._current_map.items()}
            published_keys = self._classified_keys
        else:
            labels = self._evidence.labels()
            namespaces = {key: f'{labels[key] or "unknown"}_cones' for key in self._current_map}
            published_keys = self._published_keys
        active = {(namespace, key) for key, namespace in namespaces.items()}
        stamp = (next(iter(self._current_map.values())).header.stamp if self._current_map
                 else self.get_clock().now().to_msg())
        output = MarkerArray()
        for namespace, cone_id in sorted(published_keys - active):
            marker = Marker()
            marker.header.frame_id, marker.header.stamp = 'map', stamp
            marker.ns, marker.id, marker.action = namespace, cone_id, Marker.DELETE
            output.markers.append(marker)
        for cone_id, source in self._current_map.items():
            marker = deepcopy(source)
            marker.ns = namespaces[cone_id]
            r, g, b = MARKER_COLORS[marker.ns]
            marker.color = ColorRGBA(r=r, g=g, b=b, a=1.)
            marker.lifetime = Duration().to_msg()
            output.markers.append(marker)
        return output

    def _publish_map(self):
        output = self._output_snapshot()
        self._publish_snapshot(self._pub, output)
        self._published_keys = {(m.ns, m.id) for m in output.markers if m.action == Marker.ADD}
        self._publish_classified()

    def _publish_classified(self):
        output = self._output_snapshot(classified=True)
        self._publish_snapshot(self._classified_pub, output)
        self._classified_keys = {(m.ns, m.id) for m in output.markers if m.action == Marker.ADD}

    @staticmethod
    def _publish_snapshot(publisher, output):
        # Depth-one subscribers can miss a one-off namespace deletion.
        clear = Marker(action=Marker.DELETEALL)
        clear.header.frame_id = 'map'
        if output.markers:
            clear.header.stamp = output.markers[0].header.stamp
        output.markers.insert(0, clear)
        publisher.publish(output)


def main(args=None):
    """Run fusion until ROS shuts down."""
    rclpy.init(args=args)
    node = None
    try:
        node = CameraLidarFusion()
        rclpy.spin(node)
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
