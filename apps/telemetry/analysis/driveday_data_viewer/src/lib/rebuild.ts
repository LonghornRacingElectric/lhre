import { med } from "./math";
import { CH_KEYS, type BaseChannels, type ChKey, type Pt, type RawLap, type RunData, type XY } from "./types";

/* Start/finish line: move it, and re-cut every lap, sector and energy figure there.
   Also gives every lap an interpolated point exactly on its start and end, so the marker
   never jumps between laps. */

const round = (v: number, d: number) => {
  const m = Math.pow(10, d);
  return Math.round(v * m) / m;
};

/** Rotate the closed reference loop so that arc length `shift` becomes the new origin. */
export function rotRef(ref: XY[], cum: number[], shift: number): { ref: XY[]; cum: number[] } {
  if (!shift) return { ref, cum };
  const P = ref.slice(0, ref.length - 1);
  let seg = 0;
  while (seg < cum.length - 2 && cum[seg + 1] < shift) seg++;
  const f = (shift - cum[seg]) / (cum[seg + 1] - cum[seg] || 1);
  const a = ref[seg];
  const b = ref[seg + 1];
  const Q: XY = [a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])];
  const rot: XY[] = [Q].concat(P.slice(seg + 1), P.slice(0, seg + 1));
  rot.push(Q);
  const c = [0];
  for (let i = 1; i < rot.length; i++) c.push(c[i - 1] + Math.hypot(rot[i][0] - rot[i - 1][0], rot[i][1] - rot[i - 1][1]));
  return { ref: rot, cum: c };
}

interface Sample {
  t: number;
  x: number;
  y: number;
  spd: number;
  dev: number;
  s: number;
  v: Record<ChKey, number>;
  cells: number[];
  kw: number;
  sc: number;
  U: number;
  E: number;
}

interface Spec {
  name: string;
  kind: "lap" | "out" | "partial";
  t0: number;
  t1: number;
  complete: boolean;
}

/**
 * Rebuild a run's laps from its own samples. With `shift` = 0 the laps keep the logger's lap marks.
 * With a shift, laps are cut where the car crosses the new line (shift is metres along the stored loop).
 */
export function rebuildRun(D0: RunData, shift: number): RunData {
  if (!D0 || !D0.laps || !D0.laps.length || !D0.laps[0].ch) return D0;
  const rr = rotRef(D0.ref, D0.cum, shift);
  const ref = rr.ref;
  const cum = rr.cum;
  const RL = cum[cum.length - 1];
  const L0 = [...D0.laps].sort((a, b) => a.t0 - b.t0);

  /* Energy per lap: the logger's own per-lap figures, spread over the samples in proportion to power,
     so they can be re-added up between any two moments. */
  const scaleOf = new Map<RawLap, number>();
  const scl: number[] = [];
  L0.forEach((l) => {
    if (l.wh == null || !l.ch.kw) return;
    let I = 0;
    const k = l.ch.kw;
    const t = l.ch.t;
    for (let i = 1; i < t.length; i++) I += (0.5 * (k[i] + k[i - 1]) * (t[i] - t[i - 1])) / 3600000;
    if (Math.abs(I) > 0.001) {
      const s = l.wh / I;
      scaleOf.set(l, s);
      scl.push(s);
    }
  });
  const scMed = scl.length ? med(scl) : 1;

  const ST: Sample[] = [];
  L0.forEach((l) => {
    const sc = scaleOf.get(l) ?? scMed;
    const n = l.ch.t.length;
    for (let i = 0; i < n; i++) {
      const t = l.t0 + l.ch.t[i];
      if (ST.length && t <= ST[ST.length - 1].t) continue;
      const p = l.pts[i];
      const v = {} as Record<ChKey, number>;
      CH_KEYS.forEach((k) => {
        v[k] = l.ch[k][i];
      });
      ST.push({
        t, x: p[0], y: p[1], spd: p[2], dev: p[4],
        s: (((p[3] - shift) % RL) + RL) % RL,
        v, cells: l.ch.cells ? l.ch.cells[i] : [], kw: l.ch.kw ? l.ch.kw[i] : 0, sc, U: 0, E: 0,
      });
    }
  });
  if (ST.length < 8) return D0;
  ST[0].U = ST[0].s;
  for (let i = 1; i < ST.length; i++) {
    let d = ST[i].s - ST[i - 1].s;
    if (d < -RL / 2) d += RL;
    else if (d > RL / 2) d -= RL;
    ST[i].U = ST[i - 1].U + d;
    ST[i].E = ST[i - 1].E + (0.5 * (ST[i].kw + ST[i - 1].kw) * (ST[i].t - ST[i - 1].t)) / 3600000 * ST[i].sc;
  }

  const timedOld = L0.filter((l) => l.kind === "lap");
  const outOld = L0.find((l) => l.kind === "out");
  const partOld = L0.find((l) => l.kind === "partial");
  if (!timedOld.length) return D0;

  const specs: Spec[] = [];
  if (!shift) {
    timedOld.forEach((l) => specs.push({ name: l.name, kind: "lap", t0: l.t0, t1: l.t1, complete: true }));
    if (outOld) specs.push({ name: outOld.name, kind: "out", t0: outOld.t0, t1: outOld.t1, complete: true });
    if (partOld) specs.push({ name: partOld.name, kind: "partial", t0: partOld.t0, t1: partOld.t1, complete: false });
  } else {
    const ct: number[] = [];
    let k = Math.floor(ST[0].U / RL) + 1;
    for (let i = 1; i < ST.length; i++) {
      if (ST[i].U >= k * RL && ST[i - 1].U < k * RL) {
        ct.push(ST[i - 1].t + ((ST[i].t - ST[i - 1].t) * (k * RL - ST[i - 1].U)) / (ST[i].U - ST[i - 1].U));
        k++;
      }
    }
    /* Lap 1 is the first crossing after the logger's first lap mark. */
    const cc = ct.filter((c) => c >= timedOld[0].t0 - 1);
    if (cc.length < 2) return D0;
    for (let i = 0; i < cc.length - 1; i++) specs.push({ name: "Lap " + (i + 1), kind: "lap", t0: cc[i], t1: cc[i + 1], complete: true });
    const st0 = outOld ? outOld.t0 : ST[0].t;
    if (cc[0] - st0 > 3000) specs.push({ name: "Out lap", kind: "out", t0: st0, t1: cc[0], complete: true });
    const endT = ST[ST.length - 1].t;
    if (endT - cc[cc.length - 1] > 3000) {
      specs.push({ name: "Lap " + cc.length + " (in progress)", kind: "partial", t0: cc[cc.length - 1], t1: endT, complete: false });
    }
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
      E: a.E + (b.E - a.E) * f,
      s: a.s, kw: a.kw + (b.kw - a.kw) * f, sc: a.sc,
      cells: a.cells && b.cells && a.cells.length === b.cells.length ? a.cells.map((c, j) => c + (b.cells[j] - c) * f) : a.cells,
    };
  };

  const mkLap = (sp: Spec): RawLap | null => {
    const i0 = lowerBound(sp.t0);
    const i1 = lowerBound(sp.t1);
    const sm: Sample[] = [];
    if (i0 > 0 && i0 < ST.length && ST[i0].t > sp.t0) sm.push(mix(ST[i0 - 1], ST[i0], (sp.t0 - ST[i0 - 1].t) / (ST[i0].t - ST[i0 - 1].t), sp.t0));
    for (let i = i0; i < i1; i++) sm.push(ST[i]);
    if (i1 < ST.length && i1 > i0 && sm.length && sm[sm.length - 1].t < sp.t1) {
      sm.push(mix(ST[i1 - 1], ST[i1], (sp.t1 - ST[i1 - 1].t) / (ST[i1].t - ST[i1 - 1].t), sp.t1));
    }
    const m = sm.length;
    if (m < 4) return null;
    const dur = sp.t1 - sp.t0;
    const tt = sm.map((s) => s.t - sp.t0);
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
    const cross = (th: number): number | null => {
      for (let k = 1; k < m; k++) {
        if (u[k - 1] < th && u[k] >= th) return tt[k - 1] + ((tt[k] - tt[k - 1]) * (th - u[k - 1])) / (u[k] - u[k - 1]);
      }
      return null;
    };
    const c1 = cross(RL / 3);
    const c2 = cross((2 * RL) / 3);
    let sec: number[] = [];
    if (c1 != null) sec.push(c1);
    if (c1 != null && c2 != null) {
      sec.push(c2 - c1);
      if (sp.complete) sec.push(dur - c2);
    } else if (sp.complete) {
      sec = [dur / 3, dur / 3, dur / 3];
    }
    let dist = 0;
    for (let k = 1; k < m; k++) dist += Math.hypot(sm[k].x - sm[k - 1].x, sm[k].y - sm[k - 1].y);
    return {
      ch, name: sp.name, kind: sp.kind, t0: sp.t0, t1: sp.t1, dur, sec, pts, dist,
      vmax: Math.max(...sm.map((s) => s.spd)),
      vavg: dur > 0 ? dist / (dur / 1000) : 0,
      dev: sm.reduce((a, s) => a + s.dev, 0) / m,
      wh: sp.kind === "lap" ? sm[m - 1].E - sm[0].E : null,
      whOut: null,
      whIn: null,
    };
  };

  const timed: RawLap[] = [];
  const others: RawLap[] = [];
  specs.forEach((sp) => {
    const L = mkLap(sp);
    if (!L) return;
    (sp.kind === "lap" ? timed : others).push(L);
  });
  const rank = { out: 0, partial: 1, lap: 2, theo: 3 } as const;
  others.sort((a, b) => rank[a.kind] - rank[b.kind]);
  if (!timed.length) return D0;
  const warnings = (D0.warnings || []).slice();
  if (shift) warnings.push("Start/finish moved " + shift.toFixed(0) + " m from the logged lap marks. Laps, sectors and energy were re-cut there.");
  return {
    ref: ref.map((p) => [round(p[0], 2), round(p[1], 2)] as XY),
    cum: cum.map((v) => round(v, 2)),
    RL,
    laps: timed.concat(others),
    meta: Object.assign({}, D0.meta, { sf: shift }),
    warnings,
  };
}
