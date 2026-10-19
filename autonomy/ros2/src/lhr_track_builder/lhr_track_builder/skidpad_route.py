"""Finite skidpad route and local progress tracking in the fitted layout frame."""

import math

from lhr_trackgen.skidpad_geometry import CENTER_SEPARATION, MID_RADIUS
import numpy as np
from scipy.optimize import least_squares


ROUTE_DEFAULTS = {
    'skidpad_speed': 3.0,
    'skidpad_max_cte': 0.65,
    'skidpad_exit_distance': 12.0,
}


class SkidpadRoute:
    """Remember which traversal a position belongs to, including repeated circles."""

    def __init__(self, layout, pose, orange, gates, front, half_width, limits):
        """Build from observed entry boundaries and a validated center-line gate."""
        self.layout = np.array(layout, dtype=float)
        c, s = math.cos(layout[2]), math.sin(layout[2])
        self.rotation = np.array([[c, -s], [s, c]])
        self.front = front
        self.half_width = half_width
        self.limits = limits
        local = (np.asarray(gates).reshape(-1, 2) - self.layout[:2]) @ self.rotation
        # Large orange markers must bracket the shared y=0 timing line.
        for ysign in (-1, 1):
            pair = local[(ysign * local[:, 1] > 0.1) & (ysign * local[:, 1] < 1.0)]
            if (len(pair) < 2 or not any(pair[:, 0] < -1.2)
                    or not any(pair[:, 0] > 1.2)
                    or np.max(np.abs(pair[:, 0])) > 2.0):
                raise ValueError('Center timing gate is not observed on both sides')
        local_pose = (np.asarray(pose[:2]) - self.layout[:2]) @ self.rotation
        lane = self._lane_support(orange, entry=True)
        if lane is None or local_pose[1] >= -1.0:
            raise ValueError('Entry lane is not supported before the crossing')
        slope, offset, _ = lane
        groups = []
        y = np.linspace(local_pose[1], 0.0, max(2, int(-local_pose[1] / 0.2) + 1))
        # Blend the measured lane axis into the nominal tangent at the crossing.
        blend = np.clip(-y / 8.0, 0.0, 1.0)
        groups.append(np.column_stack([(slope * y + offset) * blend, y]))
        for sign in (1, 1, -1, -1):
            angle = np.linspace(math.pi if sign == 1 else 0.0,
                                -math.pi if sign == 1 else 2 * math.pi, 289)
            groups.append(np.column_stack([
                sign * CENTER_SEPARATION / 2 + MID_RADIUS * np.cos(angle),
                MID_RADIUS * np.sin(angle)]))
        y = np.linspace(0.0, limits['skidpad_exit_distance'], 81)
        groups.append(np.column_stack([np.zeros_like(y), y]))
        self.points = np.concatenate([groups[0], *[group[1:] for group in groups[1:]]])
        self.arc = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(self.points, axis=0), axis=1))]
        ends = np.cumsum([len(groups[0]) - 1, *[len(group) - 1 for group in groups[1:]]])
        self.ends = self.arc[ends]
        self.labels = ('ENTRY', 'RIGHT_1', 'RIGHT_2', 'LEFT_1', 'LEFT_2', 'EXIT', 'STOPPED')
        self.segment = 0
        self.progress = 0.0
        self.laps = 0
        self.crossings = []
        self.cte = 0.0
        self.fault = False
        self._last = local_pose
        self._angle = 0.0
        self._sectors = {0}
        self._gate_armed = local_pose[1] < -0.5
        self._stopped_since = None
        self.complete = False

    def _lane_support(self, points, entry=False):
        """Validate orange sides against the fitted tangent, including footprint."""
        local = (np.asarray(points).reshape(-1, 2) - self.layout[:2]) @ self.rotation
        local = local[local[:, 1] < -1.0] if entry else local[local[:, 1] > 1.0]
        sides = []
        for sign in (-1, 1):
            side = local[(sign * local[:, 0] > 0.8) & (sign * local[:, 0] < 2.2)]
            if len(side) < 3 or np.ptp(side[:, 1]) < 4.0:
                return None
            fit = least_squares(lambda ab: side[:, 0] - ab[0] * side[:, 1] - ab[1],
                                [0, sign * 1.5], loss='soft_l1', f_scale=0.1)
            a, b = fit.x
            if (not fit.success or abs(a) > math.tan(math.radians(5))
                    or np.max(np.abs(side[:, 0] - a * side[:, 1] - b)) > 0.2):
                return None
            sides.append((a, b, float(np.max(side[:, 1]))))
        a, b = np.mean(np.array(sides)[:, :2], axis=0)
        end = min(side[2] for side in sides)
        width = [(sides[1][0] - sides[0][0]) * y + sides[1][1] - sides[0][1]
                 for y in (0.0, end)]
        if (min(width) < 2.8 or max(width) > 3.2 or abs(b) > 0.25
                or self.half_width + abs(b) + 0.25 >= min(width) / 2):
            return None
        return a, b, end

    def update(self, pose, speed, dt, now, orange):
        """Project locally, confirm directed full laps, and produce a speed cap."""
        if self.fault or self.complete:
            return 0.0
        local = (np.asarray(pose[:2]) - self.layout[:2]) @ self.rotation
        step = np.linalg.norm(local - self._last)
        if dt <= 0 or dt > 0.5 or step > max(0.5, 2 * speed * dt + 0.25):
            self.fault = True
            return 0.0
        ahead = max(1.0, 2 * speed * dt + 0.25)
        candidates = np.flatnonzero((self.arc[:-1] >= self.progress - 1.0)
                                    & (self.arc[:-1] <= self.progress + ahead)
                                    & (self.arc[:-1] <= self.ends[self.segment] + 0.75))
        if not len(candidates):
            self.fault = True
            return 0.0
        a = self.points[candidates]
        vector = self.points[candidates + 1] - a
        fraction = np.clip(np.sum((local - a) * vector, axis=1)
                           / np.sum(vector * vector, axis=1), 0.0, 1.0)
        projected = a + fraction[:, None] * vector
        distance = np.linalg.norm(projected - local, axis=1)
        heading = pose[2] - self.layout[2]
        alignment = vector @ np.array([math.cos(heading), math.sin(heading)])
        distance[alignment <= 0] = np.inf
        best = int(np.argmin(distance))
        self.cte = float(distance[best])
        if not math.isfinite(self.cte) or self.cte > self.limits['skidpad_max_cte']:
            self.fault = True
            return 0.0
        index = candidates[best]
        self.progress = float(self.arc[index] + fraction[best]
                              * (self.arc[index + 1] - self.arc[index]))
        if 1 <= self.segment <= 4:
            sign = 1 if self.segment <= 2 else -1
            center = np.array([sign * CENTER_SEPARATION / 2, 0.0])
            before, after = self._last - center, local - center
            change = math.atan2(after[1], after[0]) - math.atan2(before[1], before[0])
            self._angle += -sign * math.atan2(math.sin(change), math.cos(change))
            self._sectors.add(int(max(0, self._angle) / (math.pi / 2)) % 4)
        self._gate_armed |= local[1] < -0.5
        crossing = False
        if self._gate_armed and self._last[1] < 0 <= local[1]:
            t = -self._last[1] / (local[1] - self._last[1])
            x = self._last[0] + t * (local[0] - self._last[0])
            crossing = abs(x) < 0.75 and abs(math.atan2(
                math.sin(heading - math.pi / 2), math.cos(heading - math.pi / 2))) < math.pi / 4
        if self.segment < 5 and crossing and self.progress >= self.ends[self.segment] - 0.5:
            if self.segment == 0 or (self._angle >= 2 * math.pi - 0.2 and len(self._sectors) == 4):
                self.crossings.append(self.labels[self.segment])
                self.laps += int(self.segment > 0)
                self.progress = max(self.progress, float(self.ends[self.segment]))
                self.segment += 1
                self._gate_armed = False
                self._angle, self._sectors = 0.0, {0}
        if self.progress > self.ends[self.segment] + 0.5:
            self.fault = True
        self._last = local
        cap = self.limits['skidpad_speed']
        if self.segment == 5:
            lane = self._lane_support(orange)
            if lane is None:
                self._stopped_since = None
                return 0.0
            a, b, supported_end = lane
            target = min(self.limits['skidpad_exit_distance'], supported_end - self.front - 0.5)
            if abs(a * target + b) > 0.3:
                return 0.0
            remaining = target - local[1]
            cap = min(cap, math.sqrt(2 * 0.5 * max(0.0, remaining - 0.1)))
            if local[1] + self.front > 25 or abs(local[0]) + self.half_width > 1.5:
                self.fault = True
            if remaining <= 0.15:
                cap = 0.0
                if speed < 0.05 and local[1] > 0 and not self.fault:
                    if self._stopped_since is None:
                        self._stopped_since = now
                    if now - self._stopped_since >= 1.0:
                        self.complete = True
                        self.segment = 6
                else:
                    self._stopped_since = None
            else:
                self._stopped_since = None
        return 0.0 if self.fault else cap

    def forward_path(self):
        """Expose one local traversal only; repeated coordinates stay out of search."""
        selected = (self.arc >= self.progress - 0.5) & (self.arc <= self.progress + 8.0)
        return self.points[selected] @ self.rotation.T + self.layout[:2]
