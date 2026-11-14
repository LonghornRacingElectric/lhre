"""Simulation-only noisy, delayed NavSatFix measurements from plant truth."""

from collections import deque
import math

from lhr_state_estimation.gnss import LocalENU
from lhr_vehicle import load_vehicle
from nav_msgs.msg import Odometry
import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import NavSatFix, NavSatStatus


class GnssSim(Node):
    """Sample each distinct plant pose at most once; never publish truth odometry."""

    def __init__(self):
        super().__init__('gnss_sim')
        config = load_vehicle().gnss
        defaults = {
            'truth_topic': '/lhr/vehicle/odom_truth', 'gnss_topic': '/lhr/gnss/fix',
            'gnss_frame_id': 'gnss_link', 'gnss_rate_hz': config.update_rate_hz,
            'gnss_horizontal_stddev_m': config.horizontal_stddev_m,
            'gnss_vertical_stddev_m': config.vertical_stddev_m,
            'gnss_latency_s': config.latency_s, 'seed': 0,
            'gnss_origin_latitude_deg': 0., 'gnss_origin_longitude_deg': 0.,
            'gnss_origin_altitude_m': 0.,
        }
        for name, value in defaults.items():
            self.declare_parameter(name, value)

        def get(name):
            return self.get_parameter(name).value
        rate = get('gnss_rate_hz')
        self._sigma = np.array([get('gnss_horizontal_stddev_m')]*2+[get('gnss_vertical_stddev_m')])
        self._delay = get('gnss_latency_s')
        if (not math.isfinite(rate) or rate <= 0 or not np.isfinite(self._sigma).all()
                or np.min(self._sigma) <= 0 or not math.isfinite(self._delay) or self._delay < 0):
            raise ValueError('GNSS rate/sigmas must be positive and latency non-negative')
        self._period_ns = round(1e9/rate)
        self._last_sample_ns = None
        self._queue = deque()
        self._rng = np.random.default_rng(get('seed'))
        self._mount = np.asarray(config.position_m)
        self._frame = get('gnss_frame_id')
        self._origin = LocalENU(get('gnss_origin_latitude_deg'), get('gnss_origin_longitude_deg'),
                                get('gnss_origin_altitude_m'))
        self._pub = self.create_publisher(NavSatFix, get('gnss_topic'), 10)
        self.create_subscription(Odometry, get('truth_topic'), self._truth_cb, 50)
        self.create_timer(1/min(2*rate, 200), self._flush)

    def _truth_cb(self, msg):
        stamp_ns = msg.header.stamp.sec*1_000_000_000+msg.header.stamp.nanosec
        if self._last_sample_ns is not None:
            if stamp_ns < self._last_sample_ns:
                self._queue.clear()
                self._last_sample_ns = None
            elif stamp_ns-self._last_sample_ns < self._period_ns:
                return
        self._last_sample_ns = stamp_ns
        p, q = msg.pose.pose.position, msg.pose.pose.orientation
        # Full attitude for antenna placement; no attitude is included in NavSatFix.
        x, y, z, w = q.x, q.y, q.z, q.w
        rotation = np.array([
            [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
            [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
            [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)],
        ])
        point = np.array([p.x, p.y, p.z])+rotation @ self._mount
        point += self._rng.normal(0, self._sigma)
        fix = NavSatFix()
        fix.header.stamp = msg.header.stamp
        fix.header.frame_id = self._frame
        fix.status.status, fix.status.service = NavSatStatus.STATUS_FIX, NavSatStatus.SERVICE_GPS
        fix.latitude, fix.longitude, fix.altitude = self._origin.to_geodetic(*point)
        fix.position_covariance = np.diag(self._sigma**2).ravel().tolist()
        fix.position_covariance_type = NavSatFix.COVARIANCE_TYPE_DIAGONAL_KNOWN
        self._queue.append((stamp_ns+round(self._delay*1e9), fix))

    def _flush(self):
        now = self.get_clock().now().nanoseconds
        while self._queue and self._queue[0][0] <= now:
            self._pub.publish(self._queue.popleft()[1])


def main():
    rclpy.init()
    node = GnssSim()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.try_shutdown()
