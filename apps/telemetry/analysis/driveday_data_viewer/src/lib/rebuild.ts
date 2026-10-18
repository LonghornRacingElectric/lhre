import { sectorsOf } from "./sectors";
import { CH_KEYS, type BaseChannels, type ChKey, type Pt, type RawLap, type RunData } from "./types";

/* The start line is wherever the car was when each timed lap began, and the finish line wherever it was when the lap ended
   (convert.ts puts them there). Lap times, laps and energy are always the logger's.

   On a loop, every lap also gets an interpolated point exactly on its start and end, taken from the neighbouring lap's fix,
   so the car marker never jumps between laps. */

const round = (v: number, d: number) => {
  const m = Math.pow(10, d);
  return Math.round(v * m) / m;
};

interface Sample {
  t: number;
  x: number;
  y: number;
  spd: number;
  dev: number;
  s: number;
  v: Record<ChKey, number>;
  cells: number[];
  U: number;
}

/** Make each lap of a loop end exactly where the next one begins. An open course has no neighbouring laps, so it is left as it is. */
export function rebuildRun(D0: RunData): RunData {
  if (!D0 || !D0.laps || !D0.laps.length || !D0.laps[0].ch) return D0;
  // Recalculate stored splits too, so old equal-time fallbacks do not survive a reload.
  D0 = { ...D0, laps: D0.laps.map((l) => ({ ...l, sec: sectorsOf(l.ch.u, l.ch.t, D0.RL, l.dur, l.kind !== "partial") })) };
  if (D0.open) return D0;
  const RL = D0.RL;
  const L0 = [...D0.laps].sort((a, b) => a.t0 - b.t0);

  /* one stream of samples over the whole run, so that each lap can borrow the neighbouring fix */
  const ST: Sample[] = [];
  L0.forEach((l) => {
    for (let i = 0; i < l.ch.t.length; i++) {
      const t = l.t0 + l.ch.t[i];
      if (ST.length && t <= ST[ST.length - 1].t) continue;
      const p = l.pts[i];
      const v = {} as Record<ChKey, number>;
      CH_KEYS.forEach((k) => {
        v[k] = l.ch[k][i];
      });
      ST.push({ t, x: p[0], y: p[1], spd: p[2], dev: p[4], s: p[3], v, cells: l.ch.cells ? l.ch.cells[i] : [], U: 0 });
    }
  });
  if (ST.length < 8) return D0;
  ST[0].U = ST[0].s;
  for (let i = 1; i < ST.length; i++) {
    let d = ST[i].s - ST[i - 1].s;
    if (d < -RL / 2) d += RL;
    else if (d > RL / 2) d -= RL;
    ST[i].U = ST[i - 1].U + d;
  }

  const lowerBound = (t: number) => {
    let lo = 0;
    let hi = ST.length;
    while (lo < hi) {
      const m = (lo + hi) >> 1;
      if (ST[m].t < t) lo = m + 1;
      else hi = m;
    }
    return lo;
  };
  const mix = (a: Sample, b: Sample, f: number, t: number): Sample => {
    const v = {} as Record<ChKey, number>;
    CH_KEYS.forEach((k) => {
      v[k] = a.v[k] + (b.v[k] - a.v[k]) * f;
    });
    return {
      t, v,
      x: a.x + (b.x - a.x) * f,
      y: a.y + (b.y - a.y) * f,
      spd: a.spd + (b.spd - a.spd) * f,
      dev: a.dev + (b.dev - a.dev) * f,
      U: a.U + (b.U - a.U) * f,
      s: a.s,
      cells: a.cells && b.cells && a.cells.length === b.cells.length ? a.cells.map((c, j) => c + (b.cells[j] - c) * f) : a.cells,
    };
  };

  const mkLap = (src: RawLap): RawLap | null => {
    const t0 = src.t0;
    const t1 = src.t1;
    const i0 = lowerBound(t0);
    const i1 = lowerBound(t1);
    const sm: Sample[] = [];
    if (i0 > 0 && i0 < ST.length && ST[i0].t > t0) sm.push(mix(ST[i0 - 1], ST[i0], (t0 - ST[i0 - 1].t) / (ST[i0].t - ST[i0 - 1].t), t0));
    for (let i = i0; i < i1; i++) sm.push(ST[i]);
    if (i1 < ST.length && i1 > i0 && sm.length && sm[sm.length - 1].t < t1) {
      sm.push(mix(ST[i1 - 1], ST[i1], (t1 - ST[i1 - 1].t) / (ST[i1].t - ST[i1 - 1].t), t1));
    }
    const m = sm.length;
    if (m < 4) return null;
    const tt = sm.map((s) => s.t - t0);
    const q0 = ((sm[0].U % RL) + RL) % RL;
    const v0 = q0 > RL / 2 ? q0 - RL : q0;
    const u: number[] = [];
    sm.forEach((s, k) => {
      u.push(k === 0 ? v0 : Math.max(u[k - 1], v0 + (s.U - sm[0].U)));
    });
    const ch = { t: tt.map((v) => round(v, 0)), u: u.map((v) => round(v, 1)) } as BaseChannels;
    CH_KEYS.forEach((k) => {
      ch[k] = sm.map((s) => s.v[k]);
    });
    ch.cells = sm.map((s) => s.cells);
    const pts: Pt[] = sm.map((s) => [round(s.x, 2), round(s.y, 2), round(s.spd, 2), round(((s.U % RL) + RL) % RL, 1), round(s.dev, 2)]);
    let dist = 0;
    for (let k = 1; k < m; k++) dist += Math.hypot(sm[k].x - sm[k - 1].x, sm[k].y - sm[k - 1].y);
    return {
      ch, name: src.name, kind: src.kind, t0, t1, dur: src.dur,
      sec: sectorsOf(u, tt, RL, src.dur, src.kind !== "partial"),
      pts, dist,
      vmax: Math.max(...sm.map((s) => s.spd)),
      vavg: src.dur > 0 ? dist / (src.dur / 1000) : 0,
      dev: sm.reduce((a, s) => a + s.dev, 0) / m,
      wh: src.wh,
      whOut: src.whOut,
      whIn: src.whIn,
    };
  };

  const timed: RawLap[] = [];
  const others: RawLap[] = [];
  L0.forEach((src) => {
    const L = mkLap(src);
    if (L) (src.kind === "lap" ? timed : others).push(L);
  });
  if (!timed.length) return D0;
  const rank = { out: 0, partial: 1, lap: 2, theo: 3 } as const;
  others.sort((a, b) => rank[a.kind] - rank[b.kind]);
  return { ...D0, laps: timed.concat(others) };
}
