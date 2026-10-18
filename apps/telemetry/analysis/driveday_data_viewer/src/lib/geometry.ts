import { interp } from "./math";
import type { Lap, XY } from "./types";

/** The reference line every lap is measured against: a closed loop, or a path from the start line to the finish line when `open`. */
export class Geo {
  constructor(
    readonly ref: XY[],
    readonly cum: number[],
    readonly RL: number,
    readonly open = false,
  ) {}

  /** Position and heading at arc length s, wrapping around a loop or stopping at the ends of an open course. */
  at(s: number): [number, number, number] {
    const { ref, cum, RL } = this;
    s = this.open ? Math.max(0, Math.min(RL, s)) : ((s % RL) + RL) % RL;
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
    const end = this.open ? ref.length - 1 : 0;
    return [ref[end][0], ref[end][1], 0];
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

/* ---- the road: track edges worked out from a GPS centre line ---- */

const FT = 0.3048;
/** Width of the track surface, edge to edge. */
export const TRACK_WIDTH_M = 11.5 * FT;
/** The GPS antenna is on the left of the car, so the real centre line sits this far to the right of the GPS line
 *  (right of the direction of travel). A first estimate; tune it once the edges are checked against the real track. */
export const GPS_RIGHT_OFFSET_M = 1 * FT;

export interface TrackEdges {
  /** The GPS line moved right by `offset`: the estimated real centre line. */
  centre: XY[];
  /** Left and right edges of the road, half the width either side of `centre`, seen in the direction of travel. */
  left: XY[];
  right: XY[];
  width: number;
  offset: number;
}

/** Track edges from a GPS line driven down the middle of the track. The line runs in the direction of travel; a closed loop
 *  repeats its first point at the end, and so do the returned lines. Offsets toward the inside of a bend are capped just short
 *  of the bend radius, so a tight hairpin pinches the inner edge instead of folding it back on itself. */
export function trackEdges(ref: XY[], open = false, width = TRACK_WIDTH_M, offset = GPS_RIGHT_OFFSET_M): TrackEdges {
  if (ref.length < 3) return { centre: [], left: [], right: [], width, offset };
  const closed = !open && Math.hypot(ref[0][0] - ref[ref.length - 1][0], ref[0][1] - ref[ref.length - 1][1]) < 1e-6;
  const P = closed ? ref.slice(0, -1) : ref.slice();
  const n = P.length;
  const idx = (i: number) => (closed ? ((i % n) + n) % n : Math.max(0, Math.min(n - 1, i)));
  /* unit tangents from the neighbours, then smoothed over ±2 points so GPS jitter does not wobble the edges */
  const raw = P.map((_, i) => {
    const a = P[idx(i - 1)];
    const b = P[idx(i + 1)];
    const l = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
    return [(b[0] - a[0]) / l, (b[1] - a[1]) / l];
  });
  const tan = P.map((_, i) => {
    let tx = 0;
    let ty = 0;
    for (let k = -2; k <= 2; k++) {
      tx += raw[idx(i + k)][0];
      ty += raw[idx(i + k)][1];
    }
    const l = Math.hypot(tx, ty) || 1;
    return [tx / l, ty / l];
  });
  /* signed curvature (left turn positive) from the change in heading across the neighbouring points */
  const kap = P.map((_, i) => {
    if (!closed && (i === 0 || i === n - 1)) return 0;
    const ip = idx(i - 1);
    const iq = idx(i + 1);
    let dh = Math.atan2(tan[iq][1], tan[iq][0]) - Math.atan2(tan[ip][1], tan[ip][0]);
    while (dh > Math.PI) dh -= 2 * Math.PI;
    while (dh < -Math.PI) dh += 2 * Math.PI;
    return dh / (Math.hypot(P[iq][0] - P[ip][0], P[iq][1] - P[ip][1]) || 1);
  });
  /** P[i] moved d metres to the right of travel (negative d moves it left). */
  const shift = (i: number, d: number): XY => {
    const [tx, ty] = tan[i];
    const k = kap[i];
    /* the inside of a left turn is on the left (d < 0), of a right turn on the right (d > 0) */
    if (Math.abs(k) > 1e-6 && (k > 0 ? d < 0 : d > 0)) {
      const cap = 0.9 / Math.abs(k);
      if (Math.abs(d) > cap) d = Math.sign(d) * cap;
    }
    return [P[i][0] + ty * d, P[i][1] - tx * d];
  };
  const close = (a: XY[]) => (closed ? a.concat([a[0]]) : a);
  const half = width / 2;
  return {
    centre: close(P.map((_, i) => shift(i, offset))),
    left: close(P.map((_, i) => shift(i, offset - half))),
    right: close(P.map((_, i) => shift(i, offset + half))),
    width,
    offset,
  };
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
  const pts = L.playbackPts ?? L.pts;
  const t = L.ch.t;
  const n = t.length;
  if (rel <= t[0]) return [pts[0][0], pts[0][1]];
  if (rel >= t[n - 1]) return [pts[n - 1][0], pts[n - 1][1]];
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const m = (lo + hi) >> 1;
    if (t[m] <= rel) lo = m;
    else hi = m;
  }
  const f = (rel - t[lo]) / (t[hi] - t[lo] || 1);
  const g = (i: number) => pts[Math.max(0, Math.min(n - 1, i))];
  return cr(g(lo - 1), g(lo), g(hi), g(hi + 1), f);
}

/** A point on the drawn road: x, y, unused, arc length. */
export type RoadPt = [number, number, number, number];

const roadCache = new WeakMap<Lap, RoadPt[]>();

/** Measured overlay path. Manual cuts and open courses must not be closed by an invented chord. */
export function roadOf(L: Lap): RoadPt[] {
  const hit = roadCache.get(L);
  if (hit) return hit;
  const p = L.pts;
  let a = 0;
  let b = p.length - 1;
  while (a < b && p[a][4] > 2.2) a++;
  while (b > a && p[b][4] > 2.2) b--;
  const r = p.slice(a, b + 1);
  const n = r.length;
  const g = (k: number) => r[Math.max(0, Math.min(n - 1, k))];
  const dense: RoadPt[] = [];
  for (let k = 0; k < n - 1; k++) {
    for (let s = 0; s < 4; s++) {
      const q = cr(g(k - 1), g(k), g(k + 1), g(k + 2), s / 4);
      dense.push([q[0], q[1], 0, r[k][3]]);
    }
  }
  dense.push([r[n - 1][0], r[n - 1][1], 0, r[n - 1][3]]);
  roadCache.set(L, dense);
  return dense;
}

const joinCache = new WeakMap<Geo, WeakMap<Lap, { points: XY[]; distance: number } | null>>();

/** A visibly untimed join along the known layout, only for a small gap in a completed loop lap. */
export function lapCutJoin(L: Lap, geo: Geo): { points: XY[]; distance: number } | null {
  const cache = joinCache.get(geo) ?? new WeakMap<Lap, { points: XY[]; distance: number } | null>();
  joinCache.set(geo, cache);
  if (cache.has(L)) return cache.get(L)!;
  if (geo.open || L.kind !== "lap" || L.pts.length < 2) return null;
  const first = L.pts[0], last = L.pts[L.pts.length - 1];
  if (Math.hypot(first[0] - last[0], first[1] - last[1]) < 1) return null;
  const a = geo.nearest(last[0], last[1]).s;
  const b = geo.nearest(first[0], first[1]).s;
  const distance = ((b - a) % geo.RL + geo.RL) % geo.RL;
  if (distance < 1 || distance > geo.RL * 0.15) return null;
  // Only continue the known layout. Joining an off-track recorded endpoint
  // directly to it would draw a fictitious diagonal across the infield.
  const join = { points: geo.arcW(a, distance), distance };
  cache.set(L, join);
  return join;
}

/** Time (ms from lap start) at which the lap reaches distance u. */
export const timeAtU = (L: Lap, u: number) => interp(L.ch.u, L.ch.t, u);
/** Speed (mph) at distance u. */
export const speedAtU = (L: Lap, u: number) => interp(L.ch.u, L.ch.spd, u);

/** Older saved roads may lack width metadata. Recover it from corresponding boundaries. */
export function roadWidth(edges: { width?: number; left: XY[]; right: XY[] }): number {
  if (Number.isFinite(edges.width) && edges.width! > 0) return edges.width!;
  const widths = edges.left.map((p, i) => edges.right[i] ? Math.hypot(p[0] - edges.right[i][0], p[1] - edges.right[i][1]) : NaN)
    .filter(w => Number.isFinite(w) && w > 0).sort((a, b) => a - b);
  return widths.length ? widths[Math.floor(widths.length / 2)] : TRACK_WIDTH_M;
}
