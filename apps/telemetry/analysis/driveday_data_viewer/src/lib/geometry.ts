import { interp } from "./math";
import type { Lap, XY } from "./types";

/** The closed reference loop every lap is measured against. */
export class Geo {
  constructor(
    readonly ref: XY[],
    readonly cum: number[],
    readonly RL: number,
  ) {}

  /** Position and heading at arc length s, wrapping around the loop. */
  at(s: number): [number, number, number] {
    const { ref, cum, RL } = this;
    s = ((s % RL) + RL) % RL;
    for (let i = 0; i < cum.length - 1; i++) {
      if (s <= cum[i + 1]) {
        const f = (s - cum[i]) / (cum[i + 1] - cum[i] || 1);
        return [
          ref[i][0] + f * (ref[i + 1][0] - ref[i][0]),
          ref[i][1] + f * (ref[i + 1][1] - ref[i][1]),
          Math.atan2(ref[i + 1][1] - ref[i][1], ref[i + 1][0] - ref[i][0]),
        ];
      }
    }
    return [ref[0][0], ref[0][1], 0];
  }

  /** Points along the loop from arc length a, len metres on. Wraps past the start. */
  arcW(a: number, len: number): XY[] {
    const { ref, cum, RL } = this;
    const p: XY[] = [this.at(a).slice(0, 2) as XY];
    for (let k = 0; k < 2; k++) {
      for (let i = 0; i < cum.length; i++) {
        const c = cum[i] + k * RL;
        if (c > a && c < a + len) p.push(ref[i]);
      }
    }
    p.push(this.at(a + len).slice(0, 2) as XY);
    return p;
  }

  /** Arc length of the point on the loop nearest a world position, and how far away it is. */
  nearest(wx: number, wy: number): { s: number; d: number } {
    const { ref, cum } = this;
    let best = 1e9;
    let bs = 0;
    for (let i = 0; i < ref.length - 1; i++) {
      const a = ref[i];
      const b = ref[i + 1];
      const dx = b[0] - a[0];
      const dy = b[1] - a[1];
      const l2 = dx * dx + dy * dy;
      let t = l2 ? ((wx - a[0]) * dx + (wy - a[1]) * dy) / l2 : 0;
      t = Math.max(0, Math.min(1, t));
      const d = Math.hypot(wx - a[0] - t * dx, wy - a[1] - t * dy);
      if (d < best) {
        best = d;
        bs = cum[i] + t * Math.sqrt(l2);
      }
    }
    return { s: bs, d: best };
  }
}

/* ---- smooth curves through the GPS fixes, so the trace and the marker glide instead of stepping ---- */

/** Catmull–Rom point between b and c, t in [0, 1]. */
export function cr(a: ArrayLike<number>, b: ArrayLike<number>, c: ArrayLike<number>, d: ArrayLike<number>, t: number): XY {
  const t2 = t * t;
  const t3 = t2 * t;
  const f = (p0: number, p1: number, p2: number, p3: number) =>
    0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3);
  return [f(a[0], b[0], c[0], d[0]), f(a[1], b[1], c[1], d[1])];
}

/** Four short steps between each pair of fixes. */
export function curveOf(P: ArrayLike<number>[]): XY[][] {
  const n = P.length;
  const g = (i: number) => P[Math.max(0, Math.min(n - 1, i))];
  const out: XY[][] = [];
  for (let i = 0; i < n - 1; i++) {
    const seg: XY[] = [];
    for (let k = 0; k <= 4; k++) {
      seg.push(k === 0 ? [P[i][0], P[i][1]] : k === 4 ? [P[i + 1][0], P[i + 1][1]] : cr(g(i - 1), g(i), g(i + 1), g(i + 2), k / 4));
    }
    out.push(seg);
  }
  return out;
}

/** Where a lap's car is, rel ms after the lap start, on the smooth curve. */
export function posAt(L: Lap, rel: number): XY {
  const t = L.ch.t;
  const n = t.length;
  if (rel <= t[0]) return [L.pts[0][0], L.pts[0][1]];
  if (rel >= t[n - 1]) return [L.pts[n - 1][0], L.pts[n - 1][1]];
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const m = (lo + hi) >> 1;
    if (t[m] <= rel) lo = m;
    else hi = m;
  }
  const f = (rel - t[lo]) / (t[hi] - t[lo] || 1);
  const g = (i: number) => L.pts[Math.max(0, Math.min(n - 1, i))];
  return cr(g(lo - 1), g(lo), g(hi), g(hi + 1), f);
}

/** A point on the drawn road: x, y, unused, arc length. */
export type RoadPt = [number, number, number, number];

const roadCache = new WeakMap<Lap, RoadPt[]>();

/** The road drawn for the base overlay: one clean, closed loop with the off-track lead-in and tail trimmed. */
export function roadOf(L: Lap): RoadPt[] {
  const hit = roadCache.get(L);
  if (hit) return hit;
  const p = L.pts;
  let a = 0;
  let b = p.length - 1;
  while (a < b && p[a][4] > 2.2) a++;
  while (b > a && p[b][4] > 2.2) b--;
  const r = p.slice(a, b + 1);
  const closed = L.kind !== "partial" && r.length > 3;
  const n = r.length;
  const g = (k: number) => (closed ? r[((k % n) + n) % n] : r[Math.max(0, Math.min(n - 1, k))]);
  const dense: RoadPt[] = [];
  for (let k = 0; k < (closed ? n : n - 1); k++) {
    for (let s = 0; s < 4; s++) {
      const q = cr(g(k - 1), g(k), g(k + 1), g(k + 2), s / 4);
      dense.push([q[0], q[1], 0, r[k][3]]);
    }
  }
  dense.push(closed ? dense[0] : [r[n - 1][0], r[n - 1][1], 0, r[n - 1][3]]);
  roadCache.set(L, dense);
  return dense;
}

/** Time (ms from lap start) at which the lap reaches distance u. */
export const timeAtU = (L: Lap, u: number) => interp(L.ch.u, L.ch.t, u);
/** Speed (mph) at distance u. */
export const speedAtU = (L: Lap, u: number) => interp(L.ch.u, L.ch.spd, u);
