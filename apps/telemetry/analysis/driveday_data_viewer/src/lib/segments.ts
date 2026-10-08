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

/** Corners are where the loop turns tighter than about 18 m radius for at least 6 m. */
export function buildSegments(geo: Geo): Seg[] {
  const { RL } = geo;
  const NSEG = Math.round(RL / 2);
  const STEP = RL / NSEG;
  const P: [number, number][] = [];
  for (let i = 0; i < NSEG; i++) {
    const a = geo.at(i * STEP);
    P.push([a[0], a[1]]);
  }
  const Q = P.map((p, i) => {
    const a = P[(i + NSEG - 1) % NSEG];
    const b = P[(i + 1) % NSEG];
    return [(a[0] + p[0] + b[0]) / 3, (a[1] + p[1] + b[1]) / 3];
  });
  const Hd = Q.map((p, i) => {
    const b = Q[(i + 2) % NSEG];
    return Math.atan2(b[1] - p[1], b[0] - p[0]);
  });
  const dh = (a: number, b: number) => {
    let x = b - a;
    while (x > Math.PI) x -= 2 * Math.PI;
    while (x < -Math.PI) x += 2 * Math.PI;
    return x;
  };
  const rate = Hd.map((_, i) => (dh(Hd[(i + NSEG - 1) % NSEG], Hd[(i + 1) % NSEG]) * 180) / Math.PI / (2 * STEP));
  const corner = rate.map((r) => Math.abs(r) > 3.2);

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
  const cs = runs.filter((r) => r.e - r.s + 1 >= 3);

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

export const segAt = (segs: Seg[], RL: number, s: number): number => {
  s = wrap(s, RL);
  for (let i = 0; i < segs.length; i++) if (wrap(s - segs[i].a, RL) < segs[i].len) return i;
  return -1;
};

/** Brake application above 8% of peak that repeats on at least a quarter of the timed laps. */
export function buildZones(runs: Lap[], RL: number): Zone[] {
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
    let c = cl.find((k) => circDist(k.c, z.s0, RL) < 14);
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
      const w = wrap(a, RL);
      return { s0: w, len: b - a, s1: w + (b - a), n: new Set(c.m.map((m) => m.li)).size, peak: c.m.reduce((x, m) => x + m.peak, 0) / c.m.length, name: "" };
    });
  zs.sort((a, b) => a.s0 - b.s0);
  zs.forEach((z, i) => {
    z.name = "B" + (i + 1);
  });
  return zs;
}

export const zoneAt = (zones: Zone[], RL: number, s: number): number => {
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
