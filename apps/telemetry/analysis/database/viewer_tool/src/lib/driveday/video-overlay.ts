import { posAt } from "./geometry";
import { interp } from "./math";
import type { Lap } from "./types";

export interface LineCalibration { center: number; horizon: number; scale: number; lookahead: number }
export const defaultLine: LineCalibration = { center: 0.5, horizon: 0.4, scale: 1, lookahead: 55 };
export interface RoadPoint { x: number; y: number; brake: number; throttle: number }

/** Chevron wings sit behind the tip, following the projected path toward the horizon. */
export function lineChevron(a: RoadPoint, tip: RoadPoint): string | null {
  const ax = a.x * 1000, ay = a.y * 562.5, tx = tip.x * 1000, ty = tip.y * 562.5;
  const length = Math.hypot(tx - ax, ty - ay);
  if (length < 0.5) return null;
  const ux = (tx - ax) / length, uy = (ty - ay) / length;
  const width = Math.max(3, Math.min(22, 4 + (tip.y - 0.2) * 30));
  const depth = width * 0.85;
  return `M ${tx - ux * depth - uy * width} ${ty - uy * depth + ux * width} L ${tx} ${ty} L ${tx - ux * depth + uy * width} ${ty - uy * depth - ux * width}`;
}

/** Approximate pinhole projection of recorded GPS onto a level road; no image tracking or invented ideal line. */
export function roadLine(current: Lap, target: Lap, elapsed: number, calibration: LineCalibration): RoadPoint[] {
  const origin = posAt(current, elapsed);
  const before = posAt(current, Math.max(0, elapsed - 300));
  const after = posAt(current, Math.min(current.dur, elapsed + 300));
  const dx = after[0] - before[0], dy = after[1] - before[1];
  const headingLength = Math.hypot(dx, dy);
  if (headingLength < 0.3 || interp(current.ch.t, current.ch.spd, elapsed) < 3) return [];
  const hx = dx / headingLength, hy = dy / headingLength;
  const distance = interp(current.ch.t, current.ch.u, elapsed);
  const u = target.ch.u;
  const out: RoadPoint[] = [];
  for (let ahead = 3; ahead <= calibration.lookahead; ahead += 2) {
    const s = distance + ahead;
    if (s < u[0] || s > u[u.length - 1]) continue;
    const x = interp(u, target.ch.x, s) - origin[0];
    const y = interp(u, target.ch.y, s) - origin[1];
    const forward = x * hx + y * hy;
    if (forward <= 2) { if (out.length) break; else continue; }
    const lateral = x * hy - y * hx;
    out.push({ x: calibration.center + lateral * calibration.scale / (forward + 6),
      y: calibration.horizon + (0.96 - calibration.horizon) * 7 / (forward + 7),
      brake: interp(u, target.ch.brkp, s), throttle: interp(u, target.ch.thr, s) });
  }
  return out;
}
