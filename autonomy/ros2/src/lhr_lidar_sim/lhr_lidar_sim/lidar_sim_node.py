#!/usr/bin/env python3
"""Publish a synthetic Livox Mid-360 cloud on /lhr/lidar/points."""

from collections import deque
import math

from geometry_msgs.msg import TransformStamped
from lhr_lidar_sim.mid360 import Mid360Config
from lhr_lidar_sim.sensor import Mid360Sensor, MountPose
from lhr_trackgen.cone_geometry import CONE_SPECS
from lhr_vehicle import load_vehicle
from nav_msgs.msg import Odometry
import numpy as np
from rcl_interfaces.msg import SetParametersResult
import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import PointCloud2
from sensor_msgs_py import point_cloud2
from std_msgs.msg import Header
from tf2_ros import TransformBroadcaster
from visualization_msgs.msg import Marker, MarkerArray

LIDAR_FRAME = 'lidar'
BASE_FRAME = 'base_link'
SENSOR_TOPIC = '/lhr/lidar/sensor'

# The six numbers the mount study is trying to settle. Grouped because
# every one of them is live: see _on_set_parameters.
MOUNT_PARAMS = (
    'mount_x_m',
    'mount_y_m',
    'mount_z_m',
    'mount_roll_rad',
    'mount_pitch_rad',
    'mount_yaw_rad',
)

# A Mid-360 is a 65 mm puck, 60 mm tall. Drawn only so the sensor is
# visible on the car when a lever moves it.
SENSOR_DIAMETER_M = 0.065
SENSOR_HEIGHT_M = 0.060


def yaw_pitch_roll_to_quat(roll: float, pitch: float, yaw: float):
    """Return (x, y, z, w) for a ROS yaw-pitch-roll rotation."""
    cr, sr = math.cos(roll * 0.5), math.sin(roll * 0.5)
    cp, sp = math.cos(pitch * 0.5), math.sin(pitch * 0.5)
    cy, sy = math.cos(yaw * 0.5), math.sin(yaw * 0.5)
    return (
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def quat_to_yaw(q) -> float:
    """Return yaw from a quaternion, ignoring roll and pitch."""
    siny = 2.0 * (q.w * q.z + q.x * q.y)
    cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny, cosy)


class LidarSimNode(Node):
    """Casts the Mid-360 model against the ground-truth cone set."""

    def __init__(self):
        super().__init__('lidar_sim')

        # Mount *position* comes from vehicle.yaml, like every other
        # vehicle number. Mount *orientation* does not: it is what the
        # mount study has to determine, so it stays a parameter until
        # there is a measured answer to write down.
        vehicle = load_vehicle()
        # lhr_vehicle's Vec3 is a plain (x, y, z) tuple, not an object.
        mount_x, mount_y, mount_z = vehicle.lidar_position_m

        # Position defaults come from vehicle.yaml so that file stays
        # the source of truth, but all six are parameters and all six
        # are settable while the node runs. The study is someone
        # dragging a slider and watching the cloud change, and a mount
        # pose that needs a rebuild per guess is why it has not happened.
        # The measured answer gets written back to vehicle.yaml, not
        # left in a launch argument.
        self.declare_parameter('mount_x_m', mount_x)
        self.declare_parameter('mount_y_m', mount_y)
        self.declare_parameter('mount_z_m', mount_z)
        self.declare_parameter('mount_roll_rad', 0.0)
        self.declare_parameter('mount_pitch_rad', 0.0)
        self.declare_parameter('mount_yaw_rad', 0.0)
        self.declare_parameter('seed', 1)
        self.declare_parameter('frame_rate_hz', 10.0)
        self.declare_parameter('point_rate_hz', 200_000.0)
        self.declare_parameter('max_range_m', 40.0)
        self.declare_parameter('range_noise_std_m', 0.02)
        self.declare_parameter('dropout_rate', 0.0)
        self.declare_parameter('elevation_profile', 'livox')
        self.declare_parameter('return_profile', 'baseline')
        self.declare_parameter('motion_distortion', False)
        self.declare_parameter('clutter_profile', 'none')

        def param(name):
            return self.get_parameter(name).value

        frame_rate = float(param('frame_rate_hz'))
        config = Mid360Config(
            point_rate_hz=float(param('point_rate_hz')),
            frame_rate_hz=frame_rate,
            max_range_m=float(param('max_range_m')),
            range_noise_std_m=float(param('range_noise_std_m')),
            dropout_rate=float(param('dropout_rate')),
            elevation_profile=str(param('elevation_profile')),
            return_profile=str(param('return_profile')),
        )
        mount = self._mount_from_params()
        self._sensor = Mid360Sensor(
            config=config, mount=mount, seed=int(param('seed')))

        self._history = deque(maxlen=100)
        self._boxes = np.empty((0, 6))
        self._cones = np.empty((0, 2))
        self._veh = (0.0, 0.0, 0.0)
        self._have_odom = False
        self._odom_stamp = None

        latch_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self.create_subscription(
            MarkerArray, '/lhr/track/cones', self._cones_cb, latch_qos)
        self.create_subscription(
            Odometry, '/lhr/vehicle/odom', self._odom_cb, 10)
        self._cloud_pub = self.create_publisher(
            PointCloud2, '/lhr/lidar/points', 10)
        self._clutter_pub = self.create_publisher(
            MarkerArray, '/lhr/scene/clutter', latch_qos)
        self._sensor_pub = self.create_publisher(
            MarkerArray, SENSOR_TOPIC, latch_qos)

        # Without this the cloud is unviewable. It is stamped in the
        # 'lidar' frame, and the kinematic stack's whole tf tree is one
        # transform, map -> base_link, so nothing could place it: not
        # Foxglove, not RViz, not any tf2 consumer. Published here
        # rather than from a URDF because this node already owns the
        # mount pose, and a second copy of it would drift.
        #
        # Deliberately *not* a StaticTransformBroadcaster, even though
        # the mount holds still on any one run. That class appends a
        # child frame the first time it sees one and silently ignores
        # every send afterwards, republishing the original, so a mount
        # the levers can move would log that it moved and never move.
        # A transform that can change is not static.
        self._tf = TransformBroadcaster(self)
        self._sensor_pub.publish(self._sensor_marker())

        # Registered last: the callback touches the sensor, so it must
        # not be reachable before it exists.
        self.add_on_set_parameters_callback(self._on_set_parameters)

        self.create_timer(1.0 / frame_rate, self._tick)

        self.get_logger().info(
            f'Mid-360 sim ready: {config.points_per_frame()} beams at '
            f'{frame_rate:.0f} Hz, pitch '
            f'{math.degrees(mount.pitch_rad):.1f} deg, '
            f'profile {config.elevation_profile}')

    def _mount_from_params(self, overrides=None) -> MountPose:
        """Build the mount pose from the parameters, plus any overrides."""
        values = {name: float(self.get_parameter(name).value)
                  for name in MOUNT_PARAMS}
        values.update(overrides or {})
        return MountPose(
            x_m=values['mount_x_m'],
            y_m=values['mount_y_m'],
            z_m=values['mount_z_m'],
            roll_rad=values['mount_roll_rad'],
            pitch_rad=values['mount_pitch_rad'],
            yaw_rad=values['mount_yaw_rad'],
        )

    def _on_set_parameters(self, params) -> SetParametersResult:
        """
        Move the sensor on the car without restarting anything.

        This is the mount study's control surface: Foxglove's parameter
        panel writes here, and the next frame is cast from the new pose.
        Rejecting a bad value matters more than usual, because the
        callback runs *before* the parameters are stored, so refusing
        one leaves the node on the pose it already had rather than
        halfway into a new one.
        """
        overrides = {}
        for param in params:
            if param.name not in MOUNT_PARAMS:
                continue
            if param.type_ not in (Parameter.Type.DOUBLE,
                                   Parameter.Type.INTEGER):
                return SetParametersResult(
                    successful=False,
                    reason=f'{param.name} must be a number')
            value = float(param.value)
            if not math.isfinite(value):
                return SetParametersResult(
                    successful=False,
                    reason=f'{param.name} must be finite')
            overrides[param.name] = value

        if not overrides:
            return SetParametersResult(successful=True)

        mount = self._mount_from_params(overrides)
        self._sensor.mount = mount
        # The next tick broadcasts the new pose, so the frame and the
        # cloud it carries move together and never disagree.
        self.get_logger().info(
            f'mount moved to ({mount.x_m:.2f}, {mount.y_m:.2f}, '
            f'{mount.z_m:.2f}) m, pitch '
            f'{math.degrees(mount.pitch_rad):.1f} deg')
        return SetParametersResult(successful=True)

    def _sensor_marker(self) -> MarkerArray:
        """Draw the sensor puck at the origin of its own frame."""
        m = Marker()
        # In the lidar frame, so a lever that moves the mount moves this
        # with it and no second copy of the pose has to be kept.
        m.header.frame_id = LIDAR_FRAME
        m.ns = 'lidar'
        m.id = 0
        m.type = Marker.CYLINDER
        m.action = Marker.ADD
        m.pose.orientation.w = 1.0
        m.frame_locked = True
        m.scale.x = SENSOR_DIAMETER_M
        m.scale.y = SENSOR_DIAMETER_M
        m.scale.z = SENSOR_HEIGHT_M
        m.color.r, m.color.g, m.color.b, m.color.a = (0.9, 0.9, 0.95, 1.0)
        out = MarkerArray()
        out.markers.append(m)
        return out

    def _mount_transform(self, stamp) -> TransformStamped:
        """Build the base_link to lidar transform at the current mount."""
        mount = self._sensor.mount
        tf = TransformStamped()
        # Shares the cloud's stamp, so a consumer looking the transform
        # up at the time of a scan gets the pose that scan was cast
        # from rather than an interpolation around it.
        tf.header.stamp = stamp
        tf.header.frame_id = BASE_FRAME
        tf.child_frame_id = LIDAR_FRAME
        tf.transform.translation.x = mount.x_m
        tf.transform.translation.y = mount.y_m
        tf.transform.translation.z = mount.z_m
        qx, qy, qz, qw = yaw_pitch_roll_to_quat(
            mount.roll_rad, mount.pitch_rad, mount.yaw_rad)
        tf.transform.rotation.x = qx
        tf.transform.rotation.y = qy
        tf.transform.rotation.z = qz
        tf.transform.rotation.w = qw
        return tf

    def _cones_cb(self, msg: MarkerArray):
        self._cones = np.array(
            [[m.pose.position.x, m.pose.position.y,
              CONE_SPECS.get(m.text, CONE_SPECS['blue']).height_m,
              CONE_SPECS.get(m.text, CONE_SPECS['blue']).base_width_m]
             for m in msg.markers],
            dtype=np.float64).reshape(-1, 4)
        profile = self.get_parameter('clutter_profile').value
        if profile not in ('none', 'trackside'):
            raise ValueError('clutter_profile must be none or trackside')
        left = sorted((m for m in msg.markers if m.ns == 'left_cones'), key=lambda m: m.id)
        right = sorted((m for m in msg.markers if m.ns == 'right_cones'), key=lambda m: m.id)
        boxes, markers = [], MarkerArray()
        if profile == 'trackside':
            for index, (a, b) in enumerate(zip(left, right)):
                if index % 8:
                    continue
                for cone, other in ((a, b), (b, a)):
                    position = np.array([cone.pose.position.x, cone.pose.position.y])
                    direction = position - [other.pose.position.x, other.pose.position.y]
                    position += direction / np.linalg.norm(direction) * .8
                    x, y = position
                    boxes.append([x - .3, y - .3, 0., x + .3, y + .3, .45])
                    marker = Marker()
                    marker.header.frame_id = 'map'
                    marker.ns, marker.id = 'clutter', len(boxes)
                    marker.type = Marker.CUBE
                    marker.pose.position.x, marker.pose.position.y = float(x), float(y)
                    marker.pose.position.z = .225
                    marker.pose.orientation.w = 1.
                    marker.scale.x, marker.scale.y, marker.scale.z = .6, .6, .45
                    marker.color.r, marker.color.g, marker.color.b, marker.color.a = .5, .5, .5, 1.
                    markers.markers.append(marker)
        self._boxes = np.asarray(boxes).reshape(-1, 6)
        self._clutter_pub.publish(markers)

    def _odom_cb(self, msg: Odometry):
        self._veh = (msg.pose.pose.position.x,
                     msg.pose.pose.position.y,
                     quat_to_yaw(msg.pose.pose.orientation))
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        if self._history and stamp <= self._history[-1][0]:
            if stamp < self._history[-1][0]:
                self._history.clear()
            else:
                self._history.pop()
        self._history.append((stamp, self._veh))
        self._odom_stamp = msg.header.stamp
        self._have_odom = True

    def _tick(self):
        now = self.get_clock().now()
        stamp = self._odom_stamp if self._have_odom else now.to_msg()
        # Broadcast before the odom check, so the frame exists from the
        # first tick and a viewer opened early is not left with a cloud
        # it cannot place.
        self._tf.sendTransform(self._mount_transform(stamp))

        if not self._have_odom:
            return

        # Scan time comes from the ROS clock so the pattern advances
        # with sim time, which is what keeps the non-repetition
        # reproducible under a seeded run.
        t = stamp.sec + stamp.nanosec * 1e-9

        x, y, yaw = self._veh
        poses = None
        if self.get_parameter('motion_distortion').value:
            period = 1. / self._sensor.config.frame_rate_hz
            if len(self._history) < 2 or self._history[0][0] > t - period:
                return
            times = np.array([sample[0] for sample in self._history])
            history = np.array([sample[1] for sample in self._history])
            history[:, 2] = np.unwrap(history[:, 2])
            acquisition = t - period + (np.arange(10) + .5) * period / 10
            poses = np.column_stack([np.interp(acquisition, times, history[:, axis])
                                     for axis in range(3)])
            t -= period
        points, _ = self._sensor.frame_labeled(t, x, y, yaw, self._cones,
                                               boxes=self._boxes, poses=poses)

        header = Header()
        header.stamp = stamp
        header.frame_id = LIDAR_FRAME
        self._cloud_pub.publish(
            point_cloud2.create_cloud_xyz32(header, points))


def main():
    """Entry point."""
    rclpy.init()
    node = LidarSimNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.try_shutdown()


if __name__ == '__main__':
    main()
