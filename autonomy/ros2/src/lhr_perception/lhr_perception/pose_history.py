"""Timestamped planar poses for sensor-to-map synchronization."""

from collections import deque
from dataclasses import dataclass
import math


def wrap_angle(angle: float) -> float:
    """Wrap an angle into [-pi, pi)."""
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


@dataclass(frozen=True)
class Pose2D:
    """A timestamped planar vehicle pose."""

    stamp_ns: int
    x: float
    y: float
    yaw: float


class PoseHistory:
    """Keep recent poses and interpolate them at sensor timestamps."""

    def __init__(self, duration_sec: float = 2.0) -> None:
        self._duration_ns = int(duration_sec * 1e9)
        self._poses: deque[Pose2D] = deque()

    def __bool__(self) -> bool:
        """Return whether the history contains any poses."""
        return bool(self._poses)

    @property
    def oldest_stamp_ns(self) -> int | None:
        """Return the oldest retained timestamp."""
        return self._poses[0].stamp_ns if self._poses else None

    @property
    def newest_stamp_ns(self) -> int | None:
        """Return the newest retained timestamp."""
        return self._poses[-1].stamp_ns if self._poses else None

    def add(self, pose: Pose2D) -> None:
        """Append a pose, resetting cleanly if simulation time moves backward."""
        if self._poses and pose.stamp_ns < self._poses[-1].stamp_ns:
            self._poses.clear()
        elif self._poses and pose.stamp_ns == self._poses[-1].stamp_ns:
            self._poses[-1] = pose
            return

        self._poses.append(pose)
        cutoff = pose.stamp_ns - self._duration_ns
        while len(self._poses) > 1 and self._poses[1].stamp_ns < cutoff:
            self._poses.popleft()

    def lookup(self, stamp_ns: int) -> Pose2D | None:
        """Interpolate a pose, returning None unless the timestamp is bracketed."""
        if not self._poses:
            return None
        if stamp_ns < self._poses[0].stamp_ns:
            return None
        if stamp_ns > self._poses[-1].stamp_ns:
            return None

        older = self._poses[0]
        if stamp_ns == older.stamp_ns:
            return older

        for newer in list(self._poses)[1:]:
            if stamp_ns > newer.stamp_ns:
                older = newer
                continue
            if stamp_ns == newer.stamp_ns:
                return newer

            span = newer.stamp_ns - older.stamp_ns
            fraction = (stamp_ns - older.stamp_ns) / span
            yaw_delta = wrap_angle(newer.yaw - older.yaw)
            return Pose2D(
                stamp_ns=stamp_ns,
                x=older.x + fraction * (newer.x - older.x),
                y=older.y + fraction * (newer.y - older.y),
                yaw=wrap_angle(older.yaw + fraction * yaw_delta),
            )
        return None


def sensor_point_to_map(
    sensor_x: float,
    sensor_y: float,
    mount_x: float,
    mount_y: float,
    pose: Pose2D,
) -> tuple[float, float]:
    """Transform one sensor-frame point through base_link into the map."""
    vehicle_x = sensor_x + mount_x
    vehicle_y = sensor_y + mount_y
    cos_yaw = math.cos(pose.yaw)
    sin_yaw = math.sin(pose.yaw)
    map_x = pose.x + vehicle_x * cos_yaw - vehicle_y * sin_yaw
    map_y = pose.y + vehicle_x * sin_yaw + vehicle_y * cos_yaw
    return map_x, map_y
