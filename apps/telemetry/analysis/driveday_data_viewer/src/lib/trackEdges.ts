import type { XY } from "./types";

export const TRACK_WIDTH_M = 11.5 * 0.3048;
export const GPS_RIGHT_M = 0.3048;

/** Offset in the direction of travel; repeated fixes must not create undefined normals. */
export function trackEdges(gps: XY[], open = false) {
  const points = gps.filter((p, i) => !i || Math.hypot(p[0] - gps[i - 1][0], p[1] - gps[i - 1][1]) > 1e-6);
  if (!open && points.length > 1 && Math.hypot(points[0][0] - points.at(-1)![0], points[0][1] - points.at(-1)![1]) < 1e-6) points.pop();
  if (points.length < 2) throw new Error("Layout needs at least two distinct GPS positions.");
  const center: XY[] = [], left: XY[] = [], right: XY[] = [];
  points.forEach((p, i) => {
    const a = points[open ? Math.max(0, i - 1) : (i + points.length - 1) % points.length];
    const b = points[open ? Math.min(points.length - 1, i + 1) : (i + 1) % points.length];
    let dx = b[0] - a[0], dy = b[1] - a[1];
    if (Math.hypot(dx, dy) < 1e-6) { dx = b[0] - p[0]; dy = b[1] - p[1]; }
    const length = Math.hypot(dx, dy) || 1;
    const nx = dy / length, ny = -dx / length;
    const offset = (d: number): XY => [p[0] + nx * d, p[1] + ny * d];
    center.push(offset(GPS_RIGHT_M));
    left.push(offset(GPS_RIGHT_M - TRACK_WIDTH_M / 2));
    right.push(offset(GPS_RIGHT_M + TRACK_WIDTH_M / 2));
  });
  if (!open) { center.push(center[0]); left.push(left[0]); right.push(right[0]); }
  return { center, left, right, widthM: TRACK_WIDTH_M, gpsRightM: GPS_RIGHT_M };
}
