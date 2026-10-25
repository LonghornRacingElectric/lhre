import type { Geo } from "./geometry";
import { circDist, med, wrap } from "./math";
import { speedAtU, timeAtU } from "./geometry";
import type { Lap } from "./types";

/** A corner or straight, cut from the curvature of the reference loop. */
export interface Seg {
  a: number; // arc length where it starts
  len: number;
  type: "c" | "s";
  dir: number; // +1 left, −1 right, 0 straight
  name: string;
}

/** A braking zone that repeats across the timed laps. */
export interface Zone {
  s0: number;
  len: number;
  s1: number;
  n: number; // laps it was seen on
  peak: number;
  name: string;
}

/** Smooth over metres rather than sample counts so low-rate GPS noise does not create tiny corners. */
function trackCurvature(geo: Geo): { rate: number[]; count: number; step: number } {
  const count = Math.max(8, Math.round(geo.RL / 2));
  const step = geo.RL / count;
  const points = Array.from({ length: count }, (_, i) => geo.at(i * step));
  const at = (i: number) => geo.open ? Math.max(0, Math.min(count - 1, i)) : (i + count) % count;
  const smooth = points.map((_, i) => {
    const weights = [1, 2, 3, 2, 1];
    return [0, 1].map((axis) => weights.reduce((sum, w, k) => sum + w * points[at(i + k - 2)][axis], 0) / 9);
  });
  const heading = smooth.map((_, i) => Math.atan2(smooth[at(i + 2)][1] - smooth[at(i - 2)][1], smooth[at(i + 2)][0] - smooth[at(i - 2)][0]));
  const rate = heading.map((_, i) => {
    let delta = heading[at(i + 2)] - heading[at(i - 2)];
    while (delta > Math.PI) delta -= 2 * Math.PI;
    while (delta < -Math.PI) delta += 2 * Math.PI;
    return delta * 180 / Math.PI / (4 * step);
  });
  return { rate, count, step };
}

/** Sustained curvature tighter than about 26 m radius for at least 8 m. */
export function buildSegments(geo: Geo): Seg[] {
  if (geo.open) return buildOpenSegments(geo);
  const { RL } = geo;
  const { rate, count: NSEG, step: STEP } = trackCurvature(geo);
  const corner = rate.map((r) => Math.abs(r) > 2.2);

  /* start the scan in the middle of the longest straight so no corner straddles the start of the scan */
  let i0 = 0;
  {
    let bestRun = -1;
    let rs = 0;
    let rl = 0;
    for (let k = 0; k < 2 * NSEG; k++) {
      const i = k % NSEG;
      if (!corner[i]) {
        if (rl === 0) rs = k;
        rl++;
        if (rl > bestRun && rl <= NSEG) {
          bestRun = rl;
          i0 = (rs + Math.floor(rl / 2)) % NSEG;
        }
      } else rl = 0;
    }
  }

  interface Run { s: number; e: number; sign: number }
  const runs: Run[] = [];
  let cur: Run | null = null;
  for (let k = 0; k < NSEG; k++) {
    const i = (i0 + k) % NSEG;
    if (corner[i]) {
      const sg = rate[i] > 0 ? 1 : -1;
      if (cur && cur.sign === sg && k - cur.e <= 4) cur.e = k;
      else {
        if (cur) runs.push(cur);
        cur = { s: k, e: k, sign: sg };
      }
    }
  }
  if (cur) runs.push(cur);
  const cs = runs.filter((r) => r.e - r.s + 1 >= 4);
  if (!cs.length) return [{ a: 0, len: RL, type: "s", dir: 0, name: "Str 1" }];

  interface Raw { k0: number; k1: number; type: "c" | "s"; dir: number }
  const out: Raw[] = [];
  cs.forEach((r, j) => {
    const next = cs[(j + 1) % cs.length];
    const gap = (j + 1 < cs.length ? next.s : next.s + NSEG) - (r.e + 1);
    out.push({ k0: r.s, k1: r.e + 1, type: "c", dir: r.sign });
    if (gap >= 3) out.push({ k0: r.e + 1, k1: r.e + 1 + gap, type: "s", dir: 0 });
    else if (gap > 0) out[out.length - 1].k1 += Math.floor(gap / 2);
  });
  /* a corner that was pulled back by a short gap starts where the previous one ends */
  for (let j = 0; j < out.length; j++) {
    const p = out[(j + out.length - 1) % out.length];
    if (out[j].type === "c" && p.type === "c" && p.k1 < out[j].k0) out[j].k0 = p.k1;
  }
  const segs = out.map((o) => ({
    a: ((i0 + o.k0) % NSEG) * STEP,
    len: (o.k1 - o.k0) * STEP,
    type: o.type,
    dir: o.dir,
    name: "",
  }));
  const total = segs.reduce((a, s) => a + s.len, 0);
  if (Math.abs(total - RL) > 1 && segs.length) segs[segs.length - 1].len += RL - total;
  /* number from the segment that holds the start/finish line */
  let first = segs.findIndex((s) => wrap(0 - s.a, RL) < s.len);
  if (first < 0) first = 0;
  const rot = segs.slice(first).concat(segs.slice(0, first));
  let nc = 0;
  let ns = 0;
  rot.forEach((s) => {
    s.name = s.type === "c" ? "T" + ++nc : "Str " + ++ns;
  });
  return rot;
}

/** The same corner test along a path from the start line to the finish line: nothing wraps, and the first and last pieces are just straights or corners. */
function buildOpenSegments(geo: Geo): Seg[] {
  const { rate, count: N, step: STEP } = trackCurvature(geo);
  interface Run { s: number; e: number; sign: number }
  const runs: Run[] = [];
  let cur: Run | null = null;
  for (let i = 0; i < N; i++) {
    if (Math.abs(rate[i]) > 2.2) {
      const sg = rate[i] > 0 ? 1 : -1;
      if (cur && cur.sign === sg && i - cur.e <= 4) cur.e = i;
      else {
        if (cur) runs.push(cur);
        cur = { s: i, e: i, sign: sg };
      }
    }
  }
  if (cur) runs.push(cur);
  const corners = runs.filter((r) => r.e - r.s + 1 >= 4);
  interface Raw { k0: number; k1: number; type: "c" | "s"; dir: number }
  const out: Raw[] = [];
  let pos = 0;
  corners.forEach((r) => {
    const gap = r.s - pos;
    if (gap >= 3) out.push({ k0: pos, k1: r.s, type: "s", dir: 0 });
    /* a gap too short to be a straight belongs to the corner, so every sample has exactly one owner */
    out.push({ k0: gap >= 3 ? r.s : pos, k1: r.e + 1, type: "c", dir: r.sign });
    pos = r.e + 1;
  });
  if (N - pos >= 3 || !out.length) out.push({ k0: pos, k1: N, type: "s", dir: 0 });
  else out[out.length - 1].k1 = N;
  let nc = 0;
  let ns = 0;
  return out.map((o) => ({
    a: o.k0 * STEP,
    len: (o.k1 - o.k0) * STEP,
    type: o.type,
    dir: o.dir,
    name: o.type === "c" ? "T" + ++nc : "Str " + ++ns,
  }));
}

export const segAt = (segs: Seg[], RL: number, s: number, open = false): number => {
  if (open) {
    s = Math.max(0, Math.min(RL - 1e-7, s));
    return segs.findIndex((g) => s >= g.a && s < g.a + g.len);
  }
  s = wrap(s, RL);
  for (let i = 0; i < segs.length; i++) if (wrap(s - segs[i].a, RL) < segs[i].len) return i;
  return -1;
};

/** Brake application above 8% of peak that repeats on at least a quarter of the timed laps. */
export function buildZones(runs: Lap[], RL: number, open = false): Zone[] {
  interface Raw { li: number; s0: number; s1: number; peak: number }
  const raw: Raw[] = [];
  runs.forEach((l, li) => {
    const p = l.pts;
    const c = l.ch;
    let cur: { i0: number; i1: number; peak: number } | null = null;
    const close = () => {
      if (cur) {
        raw.push({ li, s0: p[cur.i0][3], s1: p[cur.i1][3], peak: cur.peak });
        cur = null;
      }
    };
    for (let i = 0; i < p.length; i++) {
      const b = c.brkp[i];
      if (b > 8) {
        if (!cur) cur = { i0: i, i1: i, peak: b };
        else {
          cur.i1 = i;
          cur.peak = Math.max(cur.peak, b);
        }
      } else close();
    }
    close();
  });
  raw.sort((a, b) => a.s0 - b.s0);
  const cl: { c: number; m: Raw[] }[] = [];
  raw.forEach((z) => {
    let c = cl.find((k) => (open ? Math.abs(k.c - z.s0) : circDist(k.c, z.s0, RL)) < 14);
    if (!c) {
      c = { c: z.s0, m: [] };
      cl.push(c);
    }
    c.m.push(z);
    c.c = c.m.reduce((a, m) => a + m.s0, 0) / c.m.length;
  });
  const need = Math.max(3, Math.ceil(runs.length * 0.25));
  const zs: Zone[] = cl
    .filter((c) => new Set(c.m.map((m) => m.li)).size >= need)
    .map((c) => {
      const s0 = med(c.m.map((m) => m.s0));
      const e = med(c.m.map((m) => (m.s1 >= m.s0 ? m.s1 : m.s0)));
      let a = s0;
      let b = Math.max(e, s0 + 5);
      if (b - a < 6) {
        a = s0 - 3;
        b = s0 + 3;
      }
      const w = open ? Math.max(0, a) : wrap(a, RL);
      return { s0: w, len: b - a, s1: w + (b - a), n: new Set(c.m.map((m) => m.li)).size, peak: c.m.reduce((x, m) => x + m.peak, 0) / c.m.length, name: "" };
    });
  zs.sort((a, b) => a.s0 - b.s0);
  zs.forEach((z, i) => {
    z.name = "B" + (i + 1);
  });
  return zs;
}

export const zoneAt = (zones: Zone[], RL: number, s: number, open = false): number => {
  if (open) return zones.findIndex((z) => s >= z.s0 - 3 && s < z.s0 + z.len + 4);
  s = wrap(s, RL);
  for (let i = 0; i < zones.length; i++) {
    const d = wrap(s - zones[i].s0, RL);
    if (d < zones[i].len + 4 || d > RL - 3) return i;
  }
  return -1;
};

/* ---- per-lap numbers through a segment or zone ---- */

export const pieces = (a: number, len: number, RL: number): [number, number][] => {
  const e = a + len;
  return e <= RL ? [[a, e]] : [[a, RL], [0, e - RL]];
};

export function segStats(L: Lap, s: Seg, RL: number) {
  const ps = pieces(s.a, s.len, RL);
  let t = 0;
  let mn = 1e9;
  ps.forEach((p) => {
    t += timeAtU(L, p[1]) - timeAtU(L, p[0]);
    mn = Math.min(mn, speedAtU(L, p[0]), speedAtU(L, p[1]));
    L.ch.u.forEach((u, i) => {
      if (u >= p[0] && u <= p[1]) mn = Math.min(mn, L.ch.spd[i]);
    });
  });
  return { t, entry: speedAtU(L, ps[0][0]), exit: speedAtU(L, ps[ps.length - 1][1]), min: mn };
}

export function zoneStats(L: Lap, z: Zone) {
  const c = L.ch;
  let peak = 0;
  let mn = 1e9;
  let dec = 0;
  L.pts.forEach((q, i) => {
    const s = q[3];
    if (s >= z.s0 - 3 && s <= z.s0 + z.len + 3) {
      peak = Math.max(peak, c.brkp[i]);
      dec = Math.min(dec, c.gacc[i]);
    }
    if (s >= z.s0 && s <= z.s0 + z.len + 12) mn = Math.min(mn, c.spd[i]);
  });
  return { peak, entry: speedAtU(L, z.s0), min: mn > 1e8 ? speedAtU(L, z.s0 + z.len) : mn, dec };
}
