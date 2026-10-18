"""Acquire the skidpad layout from a bounded view; stop before any event laps."""

import json
import math

from geometry_msgs.msg import Point, PoseStamped
from lhr_trackgen.skidpad_geometry import (
    CENTER_SEPARATION, INNER_RADIUS, MID_RADIUS, OUTER_RADIUS)
from lhr_vehicle import load_vehicle
from nav_msgs.msg import Odometry, Path
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from scipy.optimize import least_squares
from std_msgs.msg import Float32, String
from visualization_msgs.msg import Marker, MarkerArray


ACQUISITION_DEFAULTS = {
    'acquisition_speed': 0.5,
    'acquisition_distance': 5.0,
    'acquisition_timeout': 0.5,
    'fit_rms_limit': 0.10,
    'fit_inlier_distance': 0.25,
    'fit_inlier_fraction': 0.8,
    'fit_min_cones_per_circle': 6,
    'fit_min_arc_deg': 60.0,
    'fit_stable_frames': 5,
    'fit_min_chord_m': 2.0,
    'fit_max_centers': 12,
    'fit_max_hypotheses': 6,
    'fit_heading_tolerance_deg': 45.0,
}


def _visible(points, pose, radius, fov_deg):
    """Filter finite map points by a rear-axle-centered range and forward FOV."""
    points = np.asarray(points, dtype=float).reshape(-1, 2)
    delta = points - pose[:2]
    angle = np.arctan2(delta[:, 1], delta[:, 0]) - pose[2]
    angle = np.arctan2(np.sin(angle), np.cos(angle))
    keep = (np.isfinite(points).all(axis=1)
            & (np.linalg.norm(delta, axis=1) <= radius)
            & (np.abs(angle) <= math.radians(fov_deg) / 2.0))
    return points[keep]


def _layout_residuals(pose, points, assignment):
    """Return radial residuals and their analytic Jacobian for fixed assignments."""
    offsets = np.where(assignment // 2 == 0, -1, 1) * CENTER_SEPARATION / 2
    c, s = math.cos(pose[2]), math.sin(pose[2])
    centers = pose[:2] + offsets[:, None] * np.array([c, s])
    delta = points - centers
    distance = np.linalg.norm(delta, axis=1)
    # A detection at a center is not a usable radial observation.
    safe_distance = np.maximum(distance, 1e-9)
    jacobian = np.column_stack([
        -delta[:, 0] / safe_distance, -delta[:, 1] / safe_distance,
        offsets * (delta[:, 0] * s - delta[:, 1] * c) / safe_distance])
    radii = np.where(assignment % 2 == 0, INNER_RADIUS, OUTER_RADIUS)
    return distance - radii, jacobian


def _fit_layout(points, origin, limits, previous=None):
    """Initialize from cone chords, then refine rigid poses with fixed assignments."""
    points = np.asarray(points, dtype=float).reshape(-1, 2)
    unique = []
    for point in np.unique(points[np.isfinite(points).all(axis=1)], axis=0):
        if all(np.linalg.norm(point - other) >= 0.4 for other in unique):
            unique.append(point)
    points = np.asarray(unique).reshape(-1, 2)
    if len(points) < 2 * limits['fit_min_cones_per_circle']:
        return None
    radii = np.array([INNER_RADIUS, OUTER_RADIUS])
    threshold = limits['fit_inlier_distance']
    heading = float(origin[2])  # No assumed staging translation enters the fit.
    heading_limit = math.radians(limits['fit_heading_tolerance_deg'])

    def centers(pose):
        axis = np.array([math.cos(pose[2]), math.sin(pose[2])])
        return pose[:2] + np.array([-1, 1])[:, None] * axis * CENTER_SEPARATION / 2

    def errors(pose):
        distance = np.linalg.norm(points[:, None, :] - centers(pose), axis=2)
        residual = (distance[:, :, None] - radii).reshape(-1, 4)
        assignment = np.argmin(np.abs(residual), axis=1)
        return residual[np.arange(len(points)), assignment], assignment

    # Bound pair generation using spatially spread observations, rather than
    # IDs or input order. All observations still participate in scoring/fitting.
    selected = [0]
    nearest = np.linalg.norm(points - points[0], axis=1)
    for _ in range(min(48, len(points)) - 1):
        index = int(np.argmax(nearest))
        selected.append(index)
        nearest = np.minimum(nearest, np.linalg.norm(points - points[index], axis=1))
    sample = points[selected]
    i, j = np.triu_indices(len(sample), 1)
    chord = sample[j] - sample[i]
    length = np.linalg.norm(chord, axis=1)
    candidates = []
    for radius in radii:
        keep = (length >= limits['fit_min_chord_m']) & (length < 2 * radius)
        midpoint = (sample[i[keep]] + sample[j[keep]]) / 2
        normal = chord[keep][:, ::-1] * [-1, 1] / length[keep, None]
        height = np.sqrt(radius ** 2 - (length[keep] / 2) ** 2)
        for sign in (-1, 1):
            candidates.extend(midpoint + sign * height[:, None] * normal)
    candidates = np.asarray(candidates).reshape(-1, 2)
    if not len(candidates):
        return None

    scored_centers = []
    for start in range(0, len(candidates), 256):
        chunk = candidates[start:start + 256]
        distance = np.linalg.norm(points[None, :, :] - chunk[:, None, :], axis=2)
        residual = np.abs(distance[:, :, None] - radii)
        ring = np.argmin(residual, axis=2)
        error = np.min(residual, axis=2)
        inliers = error <= threshold
        for k, center in enumerate(chunk):
            count = int(np.count_nonzero(inliers[k]))
            if (count < limits['fit_min_cones_per_circle']
                    or any(np.count_nonzero(inliers[k] & (ring[k] == r)) < 2
                           for r in range(2))):
                continue
            rms = float(np.sqrt(np.mean(error[k, inliers[k]] ** 2)))
            scored_centers.append((-count, rms, center))
    scored_centers.sort(key=lambda item: item[:2])
    distinct = []
    for _, _, center in scored_centers:
        if all(np.linalg.norm(center - other) > 2 * threshold for other in distinct):
            distinct.append(center)
        if len(distinct) >= limits['fit_max_centers']:
            break

    guesses = []
    if previous is not None and np.isfinite(previous).all():
        guesses.append(np.array(previous, dtype=float))
    for left in distinct:
        for right in distinct:
            delta = right - left
            if abs(np.linalg.norm(delta) - CENTER_SEPARATION) > 4 * threshold:
                continue
            yaw = math.atan2(delta[1], delta[0])
            yaw = heading + math.atan2(math.sin(yaw - heading), math.cos(yaw - heading))
            if abs(yaw - heading) <= heading_limit:
                guesses.append(np.r_[(left + right) / 2, yaw])
    ranked = []
    for guess in guesses:
        guess[2] = heading + math.atan2(
            math.sin(guess[2] - heading), math.cos(guess[2] - heading))
        if abs(guess[2] - heading) >= heading_limit:
            continue
        residual, _ = errors(guess)
        inliers = np.abs(residual) <= threshold
        if np.count_nonzero(inliers) < 2 * limits['fit_min_cones_per_circle']:
            continue
        ranked.append((-int(np.count_nonzero(inliers)),
                       float(np.mean(np.minimum(residual ** 2, threshold ** 2))), guess))
    ranked.sort(key=lambda item: item[:2])
    starts = []
    for _, _, guess in ranked:
        if all(np.linalg.norm(guess[:2] - other[:2]) > threshold
               or abs(guess[2] - other[2]) > math.radians(3) for other in starts):
            starts.append(guess)
        if len(starts) >= limits['fit_max_hypotheses']:
            break

    fits = []
    for initial in starts:
        pose = initial
        # Keep associations fixed during each differentiable optimization.
        for _ in range(5):
            _, assignment = errors(pose)
            result = least_squares(
                lambda value: _layout_residuals(value, points, assignment)[0], pose,
                jac=lambda value: _layout_residuals(value, points, assignment)[1],
                bounds=([-np.inf, -np.inf, heading - heading_limit],
                        [np.inf, np.inf, heading + heading_limit]),
                loss='soft_l1', f_scale=0.1, max_nfev=80)
            pose = result.x
            residual, updated = errors(pose)
            if np.array_equal(updated, assignment):
                break
        else:
            continue
        inliers = np.abs(residual) <= threshold
        if (not result.success or np.any(result.active_mask)
                or np.mean(inliers) < limits['fit_inlier_fraction']):
            continue
        rms = float(np.sqrt(np.mean(residual[inliers] ** 2)))
        if rms > limits['fit_rms_limit']:
            continue
        # Small residuals do not guarantee all three pose variables are observable.
        jacobian = _layout_residuals(pose, points[inliers], updated[inliers])[1]
        if np.linalg.matrix_rank(jacobian) < 3:
            continue
        fitted_centers = centers(pose)
        support = []
        for circle in range(2):
            selected = inliers & (updated // 2 == circle)
            count = int(np.count_nonzero(selected))
            if count < limits['fit_min_cones_per_circle']:
                break
            if any(np.count_nonzero(inliers & (updated == 2 * circle + ring)) < 2
                   for ring in range(2)):
                break
            delta = points[selected] - fitted_centers[circle]
            angles = np.sort(np.mod(np.arctan2(delta[:, 1], delta[:, 0]), 2 * math.pi))
            arc = 2 * math.pi - np.max(np.diff(np.r_[angles, angles[0] + 2 * math.pi]))
            if math.degrees(arc) < limits['fit_min_arc_deg']:
                break
            support.append(count)
        if len(support) == 2:
            fits.append({'pose': pose, 'rms': rms, 'support': support,
                         'hypotheses': len(starts), 'center_candidates': len(distinct)})
    if not fits:
        return None
    fits.sort(key=lambda fit: fit['rms'])
    best = fits[0]
    for other in fits[1:]:
        if (other['rms'] <= best['rms'] + 0.02
                and (np.linalg.norm(other['pose'][:2] - best['pose'][:2]) > 0.25
                     or abs(other['pose'][2] - best['pose'][2]) > math.radians(3))):
            return None
    return best


def _entry_corridor(points, pose, front_extent, half_width):
    """Fit observed orange entry sides; return a finite local forward segment."""
    delta = np.asarray(points, dtype=float).reshape(-1, 2) - pose[:2]
    c, s = math.cos(pose[2]), math.sin(pose[2])
    local = delta @ np.array([[c, -s], [s, c]])
    sides = []
    for sign in (-1, 1):
        side = local[(local[:, 0] >= -2) & (sign * local[:, 1] >= 0.8)
                     & (sign * local[:, 1] <= 2.2)]
        if len(side) < 3 or np.ptp(side[:, 0]) < 4.0:
            return None
        fit = least_squares(
            lambda ab: side[:, 1] - ab[0] * side[:, 0] - ab[1],
            [0, sign * 1.5], loss='soft_l1', f_scale=0.1)
        slope, intercept = fit.x
        if np.max(np.abs(side[:, 1] - slope * side[:, 0] - intercept)) > 0.2:
            return None
        sides.append((slope, intercept, float(np.max(side[:, 0]))))
    a = (sides[0][0] + sides[1][0]) / 2
    b = (sides[0][1] + sides[1][1]) / 2
    end = min(side[2] for side in sides) - front_extent - 1.0
    widths = [(sides[1][0] - sides[0][0]) * x + sides[1][1] - sides[0][1]
              for x in (0.0, max(0.0, end))]
    if (end <= 0 or abs(a) > math.tan(math.radians(15))
            or abs(sides[0][0] - sides[1][0]) > math.tan(math.radians(5))
            or min(widths) < 2.8 or max(widths) > 3.2
            or abs(b) + half_width + 0.5 > min(widths) / 2):
        return None
    x = np.linspace(0, end, max(2, int(end / 0.25) + 1))
    path = np.column_stack([x, a * x + b])
    return path @ np.array([[c, s], [-s, c]]) + pose[:2], end


class SkidpadAcquisition(Node):
    """Acquire, optionally creep on an observed lane, then hold at ACQUIRED."""

    def __init__(self):
        super().__init__('skidpad_acquisition')
        self.declare_parameter('fov_deg', 240.0)
        self.declare_parameter('max_range_m', 15.0)
        for name, value in ACQUISITION_DEFAULTS.items():
            self.declare_parameter(name, value)
        self._limits = {name: self.get_parameter(name).value
                        for name in ACQUISITION_DEFAULTS}
        self._range = float(self.get_parameter('max_range_m').value)
        self._fov = float(self.get_parameter('fov_deg').value)
        if (not 0 < self._range <= 15 or not 0 < self._fov <= 240
                or any(not math.isfinite(value) or value <= 0
                       for value in self._limits.values())
                or self._limits['fit_inlier_fraction'] > 1
                or self._limits['fit_min_arc_deg'] > 360
                or self._limits['fit_heading_tolerance_deg'] >= 90
                or self._limits['fit_min_chord_m'] >= 2 * INNER_RADIUS
                or self._limits['fit_max_centers'] < 2
                or self._limits['fit_min_cones_per_circle'] < 4
                or self._limits['acquisition_speed'] > 0.5
                or self._limits['acquisition_distance'] > 5):
            raise ValueError('Invalid acquisition limits (view <=15m/240deg; creep <=0.5m/s/5m)')
        vehicle = load_vehicle()
        self._front = max(vehicle.chassis_center_x_m + vehicle.body_length_m / 2,
                          vehicle.wheelbase_m + vehicle.wheel_radius_m)
        self._half_width = max(vehicle.body_width_m / 2,
                               (vehicle.track_m + vehicle.wheel_width_m) / 2)
        self._pose = None
        self._origin = None
        self._speed = 0.0
        self._distance = 0.0
        self._odom_time = None
        self._cone_time = None
        self._mission = ''
        self._acquired = False
        self._phase = 'ACQUIRE'
        self._reported_phase = None
        self._circular = []
        self._orange = []
        self._fit = None
        self._stable = 0
        self._frame = 0
        self._last_frame = -1
        self._stopped_since = None
        self._fault = False
        self.create_subscription(Odometry, '/lhr/vehicle/odom', self._odom_cb, 10)
        self.create_subscription(String, '/lhr/mission/status', self._status_cb, 10)
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(MarkerArray, '/lhr/sensor/cones_detected', self._cones_cb, qos)
        self._path_pub = self.create_publisher(Path, '/lhr/track/centerline', 10)
        self._limit_pub = self.create_publisher(Float32, '/lhr/planning/speed_limit', 10)
        self._state_pub = self.create_publisher(String, '/lhr/skidpad/acquisition', 10)
        self._view_pub = self.create_publisher(MarkerArray, '/lhr/skidpad/visible_cones', 10)
        self._layout_pub = self.create_publisher(MarkerArray, '/lhr/skidpad/layout', 10)
        self.create_timer(0.2, self._tick)

    def _status_cb(self, msg):
        self._mission = msg.data

    def _odom_cb(self, msg):
        now = self.get_clock().now().nanoseconds / 1e9
        q = msg.pose.pose.orientation
        yaw = math.atan2(2 * (q.w * q.z + q.x * q.y),
                         1 - 2 * (q.y * q.y + q.z * q.z))
        pose = np.array([msg.pose.pose.position.x, msg.pose.pose.position.y, yaw])
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec / 1e9
        velocity = math.hypot(msg.twist.twist.linear.x, msg.twist.twist.linear.y)
        if (msg.header.frame_id != 'map' or not np.isfinite(pose).all()
                or not math.isfinite(velocity)):
            self._fault = True
            return
        if not 0 <= now - stamp <= self._limits['acquisition_timeout']:
            # Do not refresh receipt time for a repeatedly published frozen pose.
            return
        self._speed = velocity
        if self._pose is not None:
            step = np.linalg.norm(pose[:2] - self._pose[:2])
            elapsed = now - self._odom_time
            if elapsed < 0 or step > max(0.5, 2 * elapsed * max(0.5, self._speed)):
                self._fault = True
            self._distance += step
        self._pose, self._odom_time = pose, now
        if self._origin is None:
            heading = pose[2]
            self._origin = np.r_[pose[:2], heading - math.pi / 2]

    def _cones_cb(self, msg):
        self._circular, self._orange = [], []
        visible = MarkerArray()
        visible.markers.append(Marker(action=Marker.DELETEALL))
        if self._pose is None:
            return
        for marker in msg.markers:
            if marker.action not in (Marker.ADD, Marker.MODIFY) or marker.header.frame_id != 'map':
                continue
            point = (marker.pose.position.x, marker.pose.position.y)
            if not len(_visible([point], self._pose, self._range, self._fov)):
                continue
            if marker.ns in ('left_cones', 'right_cones', 'cones'):
                self._circular.append(point)
            elif marker.ns == 'orange_small':
                self._orange.append(point)
            else:
                continue
            visible.markers.append(marker)
        self._view_pub.publish(visible)
        self._cone_time = self.get_clock().now().nanoseconds / 1e9
        self._frame += 1

    def _tick(self):
        now = self.get_clock().now().nanoseconds / 1e9
        path = Path()
        path.header.frame_id = 'map'
        path.header.stamp = self.get_clock().now().to_msg()
        speed = 0.0
        fresh = (self._pose is not None and self._cone_time is not None
                 and 0 <= now - self._cone_time <= self._limits['acquisition_timeout']
                 and 0 <= now - self._odom_time <= self._limits['acquisition_timeout'])
        if fresh and not self._fault:
            # Refresh the view after motion even if the map message hasn't changed.
            circular = _visible(self._circular, self._pose, self._range, self._fov)
            orange = _visible(self._orange, self._pose, self._range, self._fov)
            if self._frame != self._last_frame and self._phase != 'ACQUIRED':
                self._last_frame = self._frame
                fit = _fit_layout(
                    circular, self._origin, self._limits,
                    previous=self._fit['pose'] if self._fit is not None else None)
                consistent = (fit is not None and self._fit is not None
                              and np.linalg.norm(fit['pose'][:2] - self._fit['pose'][:2]) <= 0.1
                              and abs(fit['pose'][2] - self._fit['pose'][2]) <= math.radians(1))
                self._stable = self._stable + 1 if consistent else int(fit is not None)
                self._fit = fit
            ready = self._stable >= self._limits['fit_stable_frames']
            corridor = _entry_corridor(orange, self._pose, self._front, self._half_width)
            if ready or self._acquired:
                self._phase = 'ACQUIRED' if self._acquired else 'HOLD'
                if self._speed < 0.05:
                    if self._stopped_since is None:
                        self._stopped_since = now
                    if now - self._stopped_since >= 1.0:
                        self._phase = 'ACQUIRED'
                        self._acquired = True
                else:
                    self._stopped_since = None
            elif corridor is not None and self._distance < self._limits['acquisition_distance']:
                self._phase = 'APPROACH'
                points, available = corridor
                remaining = min(available, self._limits['acquisition_distance'] - self._distance)
                # Conservative simulation envelope: 0.5m/s² braking, 0.5s
                # latency, 0.5m margin, independent of the steering controller.
                speed = min(self._limits['acquisition_speed'],
                            max(0.0, math.sqrt(0.25 ** 2 + max(0, remaining - 0.5)) - 0.25))
                for x, y in points:
                    point = PoseStamped(header=path.header)
                    point.pose.position.x = float(x)
                    point.pose.position.y = float(y)
                    point.pose.orientation.w = 1.0
                    path.poses.append(point)
            else:
                self._phase = 'HOLD'
        else:
            self._phase = 'HOLD'
            self._stable = 0
            self._fit = None
            self._stopped_since = None
        if fresh and self._phase in ('HOLD', 'ACQUIRED') and self._fit is not None:
            point = PoseStamped(header=path.header)
            point.pose.position.x, point.pose.position.y = map(float, self._pose[:2])
            point.pose.orientation.w = 1.0
            path.poses.append(point)
        if self._mission != 'DRIVING':
            speed = 0.0
        self._path_pub.publish(path)
        self._limit_pub.publish(Float32(data=float(speed)))
        report = {'phase': self._phase, 'distance_m': float(self._distance),
                  'speed_limit': speed, 'stable_frames': self._stable,
                  'visible_cones': len(self._circular), 'fault': self._fault}
        if self._fit is not None:
            report.update(rms_m=self._fit['rms'], support=self._fit['support'],
                          layout_pose=self._fit['pose'].tolist(),
                          hypotheses=self._fit['hypotheses'],
                          center_candidates=self._fit['center_candidates'])
        self._state_pub.publish(String(data=json.dumps(report)))
        if self._phase != self._reported_phase:
            self.get_logger().info(json.dumps(report))
            self._reported_phase = self._phase
        self._publish_layout(path.header)

    def _publish_layout(self, header):
        markers = MarkerArray()
        markers.markers.append(Marker(action=Marker.DELETEALL))
        if self._fit is not None:
            x, y, yaw = self._fit['pose']
            for index, sign in enumerate((-1, 1)):
                cx = x + sign * CENTER_SEPARATION / 2 * math.cos(yaw)
                cy = y + sign * CENTER_SEPARATION / 2 * math.sin(yaw)
                marker = Marker(header=header, ns='fitted_centerline', id=index,
                                type=Marker.LINE_STRIP, action=Marker.ADD)
                marker.pose.orientation.w = 1.0
                marker.scale.x = 0.12
                marker.color.g, marker.color.a = 1.0, 1.0
                for angle in np.linspace(0, 2 * math.pi, 181):
                    marker.points.append(Point(x=cx + MID_RADIUS * math.cos(angle),
                                               y=cy + MID_RADIUS * math.sin(angle), z=0.1))
                markers.markers.append(marker)
        self._layout_pub.publish(markers)


def main():
    """Run skidpad acquisition without authorizing any event laps."""
    rclpy.init()
    node = SkidpadAcquisition()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()
