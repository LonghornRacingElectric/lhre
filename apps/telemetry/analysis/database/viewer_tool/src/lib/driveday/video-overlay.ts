import { posAt } from "./geometry";
import { interp } from "./math";
import type { Lap } from "./types";

export interface LineCalibration { center: number; horizon: number; scale: number; lookahead: number; cameraHeight?: number }
export const defaultLine: LineCalibration = { center: 0.5, horizon: 0.4, scale: 1, lookahead: 55, cameraHeight: 1.1 };
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

export interface GhostFace { points: string; depth: number; tire: boolean }
export interface GhostProjection { faces: GhostFace[]; status: string; forward?: number }

/** Project a simple metre-scale car at equal elapsed lap time onto a flat road. */
export function ghostCar(current: Lap, target: Lap | null, elapsed: number, calibration: LineCalibration, cameraHeight = 1.1): GhostProjection {
  const hidden = (status: string): GhostProjection => ({ faces: [], status });
  if (!target || target.kind !== "lap") return hidden("Choose a recorded ghost lap");
  if (target === current) return hidden("Choose a different ghost lap");
  if (elapsed > target.dur) return hidden("Ghost finished");
  const pose = (lap: Lap) => {
    const { t, x, y } = lap.ch;
    if (t.length < 2 || elapsed < t[0] || elapsed > t[t.length - 1]) return null;
    const next = t.findIndex(time => time >= elapsed);
    if (next > 0 && t[next] - t[next - 1] > 1500) return null;
    const before = Math.max(t[0], elapsed - 200), after = Math.min(t[t.length - 1], elapsed + 200);
    const px = interp(t, x, elapsed), py = interp(t, y, elapsed);
    const dx = interp(t, x, after) - interp(t, x, before), dy = interp(t, y, after) - interp(t, y, before);
    const length = Math.hypot(dx, dy);
    return [px, py, dx, dy, length].every(Number.isFinite) && length > 0.1 ? { x: px, y: py, hx: dx / length, hy: dy / length } : null;
  };
  const origin = pose(current), ghost = pose(target);
  if (!origin || !ghost) return hidden("GPS position or heading unavailable");
  const dx = ghost.x - origin.x, dy = ghost.y - origin.y;
  const forward = dx * origin.hx + dy * origin.hy;
  if (forward < 2) return { faces: [], forward, status: forward < -1 ? "Ghost behind" : "Ghost alongside / overlapping" };
  type Vertex = { right: number; forward: number; z: number };
  const faces: GhostFace[] = [];
  const box = (front: number, rear: number, left: number, right: number, bottom: number, top: number, tire = false) => {
    const vertices = [[rear,left,bottom],[front,left,bottom],[front,right,bottom],[rear,right,bottom],[rear,left,top],[front,left,top],[front,right,top],[rear,right,top]].map(([f,r,z]): Vertex => {
      const wx = dx + f * ghost.hx + r * ghost.hy, wy = dy + f * ghost.hy - r * ghost.hx;
      return { right: wx * origin.hy - wy * origin.hx, forward: wx * origin.hx + wy * origin.hy, z };
    });
    for (const indices of [[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7],[4,5,6,7]]) {
      let polygon = indices.map(i => vertices[i]);
      const clipped: Vertex[] = [];
      for (let i = 0; i < polygon.length; i++) {
        const a = polygon[i], b = polygon[(i + 1) % polygon.length], inside = a.forward >= 1.5, nextInside = b.forward >= 1.5;
        if (inside) clipped.push(a);
        if (inside !== nextInside) { const f = (1.5 - a.forward) / (b.forward - a.forward); clipped.push({ right: a.right + f * (b.right - a.right), forward: 1.5, z: a.z + f * (b.z - a.z) }); }
      }
      polygon = clipped;
      if (polygon.length < 3) continue;
      const points = polygon.map(v => `${(calibration.center + v.right * calibration.scale / v.forward) * 1000},${(calibration.horizon + (cameraHeight - v.z) * calibration.scale * 16 / 9 / v.forward) * 562.5}`).join(" ");
      faces.push({ points, depth: polygon.reduce((sum,v) => sum + v.forward, 0) / polygon.length, tire });
    }
  };
  box(0.7,-1.1,-0.34,0.34,0.12,0.6);
  box(1.5,0.5,-0.16,0.16,0.12,0.3);
  box(1.55,1.35,-0.73,0.73,0.09,0.17);
  box(-1,-1.2,-0.65,0.65,0.55,0.72);
  for (const axle of [-0.85,0.95]) for (const side of [-1,1]) box(axle+0.24,axle-0.24,side*0.65-0.13,side*0.65+0.13,0,0.48,true);
  return { faces: faces.sort((a,b) => b.depth - a.depth), forward, status: `${forward.toFixed(1)} m ahead` };
}
