import { interp, pctl, sd, wrap } from "./math";
import { MPH } from "./format";
import { prepareLap, type Session } from "./session";
import { segAt } from "./segments";
import type { BaseChannels, ChKey, Lap, Pt, RawLap, UserState } from "./types";

/* Everything that depends on what the user has marked on a run: skipped laps, cones, alert limits.
   Rebuilt whenever the user state changes. */

export interface BestSector {
  v: number;
  lap: number;
}

export interface Consistency {
  /** Where each clean lap first touched the brake in each zone. */
  brake: { x: number; y: number; li: number }[][];
  /** Where each clean lap was slowest in each corner. */
  apex: { x: number; y: number; li: number }[][];
  bstd: number[]; // metres, per zone
  astd: number[]; // mph, per segment
}

export interface AlertDef {
  k: string; // key in the setup form
  ch: ChKey;
  label: string;
  dir: 1 | -1; // 1 = above the limit, −1 = below
  temp: boolean;
}
export const ALERT_DEFS: AlertDef[] = [
  { k: "aCellT", ch: "cellT", label: "Hottest cell", dir: 1, temp: true },
  { k: "aInvT", ch: "invT", label: "Inverter hotspot", dir: 1, temp: true },
  { k: "aMotT", ch: "motT", label: "Motor", dir: 1, temp: true },
  { k: "aCoolT", ch: "coolT", label: "Coolant", dir: 1, temp: true },
  { k: "aCellV", ch: "cellV", label: "Lowest cell", dir: -1, temp: false },
  { k: "aPackV", ch: "packv", label: "Pack voltage", dir: -1, temp: false },
];
export interface Alert {
  d: AlertDef;
  t: number; // ms from the session start
  lap: Lap;
  val: number;
  lim: number;
}

const numSetup = (u: UserState, k: string): number | null => {
  const v = u.setup[k];
  return v != null && v !== "" && !isNaN(+v) ? +v : null;
};

export class Analysis {
  best: number[] = [];
  bestLap = 0;
  theo = 0;
  bestRun!: Lap;
  bestSec: BestSector[] = [];
  cons: Consistency = { brake: [], apex: [], bstd: [], astd: [] };
  alerts: Alert[] = [];
  private theoCache: Lap | null | undefined;

  constructor(
    readonly s: Session,
    public u: UserState,
  ) {
    this.recompute();
  }

  /** Call after any change to the user state. */
  recompute(u: UserState = this.u) {
    this.u = u;
    this.theoCache = undefined;
    const { runs } = this.s;
    const vr = runs.filter((l) => this.valid(l));
    const pool = vr.length ? vr : runs;
    this.best = [0, 1, 2].map((i) => Math.min(...pool.map((l) => l.sec[i] as number)));
    this.bestLap = Math.min(...pool.map((l) => l.dur));
    this.theo = this.best.reduce((a, b) => a + b, 0);
    this.bestRun = pool.reduce((a, b) => (b.dur < a.dur ? b : a));
    this.bestSec = [0, 1, 2].map((k) => {
      let bl = pool[0];
      pool.forEach((l) => {
        if ((l.sec[k] as number) < (bl.sec[k] as number)) bl = l;
      });
      return { v: bl.sec[k] as number, lap: runs.indexOf(bl) + 1 };
    });
    this.buildConsistency();
    this.computeAlerts();
  }

  /* ---- skipped laps, cones, penalty ---- */
  valid(l: Lap): boolean {
    if (l.kind !== "lap") return true;
    const f = this.u.flags[this.s.lapNo(l)];
    return !(f && f.x);
  }
  pen(): number {
    const v = numSetup(this.u, "pen");
    return v != null ? v : 2;
  }
  coneEvIn(l: Lap) {
    const { T0 } = this.s;
    return this.u.ce.filter((e) => e.t >= l.t0 - T0 && e.t < l.t1 - T0);
  }
  conesOf(l: Lap): number {
    return l.kind === "lap" ? (+this.u.cones[this.s.lapNo(l)] || 0) + this.coneEvIn(l).length : 0;
  }
  /** Lap time plus the cone penalty, ms. */
  adj(l: Lap): number {
    return l.dur + this.conesOf(l) * this.pen() * 1000;
  }

  /* ---- consistency: brake points and apex speeds across the clean laps ---- */
  private buildConsistency() {
    const { s } = this;
    const { RL, zones, segs } = s;
    const vr = s.runs.filter((l) => this.valid(l));
    const c: Consistency = { brake: [], apex: [], bstd: [], astd: [] };
    zones.forEach((z) => {
      const pts: Consistency["brake"][number] = [];
      const ds: number[] = [];
      vr.forEach((l) => {
        for (let i = 0; i < l.pts.length; i++) {
          const d = ((l.pts[i][3] - z.s0 + RL * 1.5) % RL) - RL / 2;
          if (d > -8 && d < z.len + 8 && l.ch.brkp[i] > 8) {
            pts.push({ x: l.pts[i][0], y: l.pts[i][1], li: s.laps.indexOf(l) });
            ds.push(d);
            break;
          }
        }
      });
      c.brake.push(pts);
      c.bstd.push(sd(ds));
    });
    segs.forEach((g) => {
      if (g.type !== "c") {
        c.apex.push([]);
        c.astd.push(0);
        return;
      }
      const pts: Consistency["apex"][number] = [];
      const sp: number[] = [];
      vr.forEach((l) => {
        let bi = -1;
        let bv = 1e9;
        l.pts.forEach((p, i) => {
          const d = wrap(p[3] - g.a, RL);
          if (d <= g.len && l.ch.spd[i] < bv) {
            bv = l.ch.spd[i];
            bi = i;
          }
        });
        if (bi >= 0) {
          pts.push({ x: l.pts[bi][0], y: l.pts[bi][1], li: s.laps.indexOf(l) });
          sp.push(bv);
        }
      });
      c.apex.push(pts);
      c.astd.push(sd(sp));
    });
    this.cons = c;
  }

  /* ---- alerts: moments a limit set in Setup was crossed ---- */
  limit(d: AlertDef): number | null {
    return numSetup(this.u, d.k);
  }
  private computeAlerts() {
    const out: Alert[] = [];
    ALERT_DEFS.forEach((d) => {
      const lim = this.limit(d);
      if (lim == null) return;
      let cur: Alert | null = null;
      this.s.TLAPS.forEach((l) => {
        const c = l.ch[d.ch];
        const t = l.ch.t;
        for (let i = 0; i < c.length; i++) {
          const bad = d.dir > 0 ? c[i] > lim : c[i] < lim && c[i] > 0.5;
          if (bad) {
            if (!cur) cur = { d, t: l.t0 - this.s.T0 + t[i], lap: l, val: c[i], lim };
            else if (d.dir > 0 ? c[i] > cur.val : c[i] < cur.val) cur.val = c[i];
          } else if (cur) {
            out.push(cur);
            cur = null;
          }
        }
      });
      if (cur) out.push(cur);
    });
    out.sort((a, b) => a.t - b.t);
    this.alerts = out;
  }
  /** The limits currently exceeded at a moment on a lap. */
  activeAlerts(L: Lap, rel: number): { d: AlertDef; v: number }[] {
    const out: { d: AlertDef; v: number }[] = [];
    ALERT_DEFS.forEach((d) => {
      const lim = this.limit(d);
      if (lim == null) return;
      const v = interp(L.ch.t, L.ch[d.ch], rel);
      if (d.dir > 0 ? v > lim : v < lim && v > 0.5) out.push({ d, v });
    });
    return out;
  }

  /* ---- a lap stitched from the fastest S1, S2 and S3 ---- */
  theoLap(): Lap | null {
    if (this.theoCache !== undefined) return this.theoCache;
    const { s } = this;
    const { RL } = s;
    const vr = s.runs.filter((l) => this.valid(l));
    if (vr.length < 2) return (this.theoCache = null);
    const g = [0, RL / 3, (2 * RL) / 3, RL];
    const bl = [0, 1, 2].map((k) => vr.reduce((a, l) => ((l.sec[k] as number) < (a.sec[k] as number) ? l : a)));
    const bk = ["thr", "brk", "brkr", "steer", "rpm", "trq", "dcv", "dca", "kw", "packv", "soc", "motT", "invT", "gateT", "coolT", "cellT", "cellV", "modA", "modB", "modC", "vmaxC", "vavgC"] as const;
    const T: number[] = [];
    const U: number[] = [];
    const P: Pt[] = [];
    const CH = {} as Record<ChKey, number[]>;
    bk.forEach((k) => {
      CH[k] = [];
    });
    let off = 0;
    for (let k = 0; k < 3; k++) {
      const l = bl[k];
      const c = l.ch;
      /* sector 1 starts at the lap start, as the logged sector time does, even if the first fix is a metre before the line */
      const tA = k === 0 ? 0 : interp(c.u, c.t, g[k]);
      const tB = k === 2 ? l.dur : interp(c.u, c.t, g[k + 1]);
      const times = [tA];
      c.t.forEach((t) => {
        if (t > tA + 1 && t < tB - 1) times.push(t);
      });
      times.push(tB);
      times.forEach((tt, j) => {
        T.push(off + (tt - tA) + (k > 0 && j === 0 ? 1 : 0));
        const uu = Math.max(g[k], Math.min(g[k + 1], interp(c.t, c.u, tt)));
        U.push(uu);
        P.push([interp(c.t, c.x, tt), interp(c.t, c.y, tt), interp(c.t, c.spd, tt) / MPH, uu % RL, 0]);
        bk.forEach((key) => CH[key].push(interp(c.t, c[key], tt)));
      });
      off += tB - tA;
    }
    const dur = T[T.length - 1];
    const ch = { t: T, u: U, cells: T.map(() => [] as number[]), ...CH } as BaseChannels;
    const raw: RawLap = {
      ch, name: "Best sectors", kind: "theo", t0: 0, t1: dur, dur,
      sec: [bl[0].sec[0], bl[1].sec[1], bl[2].sec[2]],
      pts: P, dist: RL, vmax: Math.max(...P.map((p) => p[2])), vavg: RL / (dur / 1000), dev: 0, wh: null, whOut: null, whIn: null,
    };
    const lap = prepareLap(raw, s.brkMax, s.brkrMax);
    lap.theo = true;
    return (this.theoCache = lap);
  }

  /** Per-fix gain (negative) or loss (positive) in ms against a ghost lap, cached on the lap. */
  gainFor(L: Lap, R: Lap | null, refKey: string): number[] | null {
    if (!R || R === L) return null;
    const key = this.s.laps.indexOf(L) + "|" + refKey;
    if (L.gainKey === key && L.gain) return L.gain;
    const c = L.ch;
    const tL0 = interp(c.u, c.t, 0);
    const tR0 = interp(R.ch.u, R.ch.t, 0);
    const dl = c.u.map((u, i) => c.t[i] - tL0 - (interp(R.ch.u, R.ch.t, u) - tR0));
    const g: number[] = [];
    for (let i = 0; i < dl.length - 1; i++) g.push(dl[i + 1] - dl[i]);
    L.gainKey = key;
    L.gain = g;
    L.gainScale = Math.max(20, pctl(g.map(Math.abs), 0.9));
    return g;
  }

  segIndexAt(L: Lap, rel: number): number {
    const u = interp(L.ch.t, L.ch.u, rel);
    return segAt(this.s.segs, this.s.RL, u);
  }
}

/** Fix rate and gaps longer than 1.5 s, across every lap. */
export function gpsStats(s: Session): { hz: number; gaps: number } {
  const dts: number[] = [];
  let gaps = 0;
  s.TLAPS.forEach((l) => {
    const t = l.ch.t;
    for (let i = 1; i < t.length; i++) {
      const d = t[i] - t[i - 1];
      if (d > 1) {
        dts.push(d);
        if (d > 1500) gaps++;
      }
    }
  });
  const sorted = [...dts].sort((a, b) => a - b);
  const m = sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0;
  return { hz: m ? 1000 / m : 0, gaps };
}

/** Timing colour for one sector of a lap: p = session best, g = faster than Lap 1, y = slower, n = not timed. */
export function sectorClass(a: Analysis, l: Lap, i: number): "p" | "g" | "y" | "n" {
  const v = l.sec[i];
  if (v == null) return "n";
  if (l.kind === "lap" && Math.abs(v - a.best[i]) < 1) return "p";
  return v <= (a.s.base.sec[i] as number) ? "g" : "y";
}
