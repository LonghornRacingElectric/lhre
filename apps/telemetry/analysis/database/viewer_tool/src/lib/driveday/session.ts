import { Geo, curveOf } from "./geometry";
import { MPH } from "./format";
import { interp, pctl, timeAverage } from "./math";
import { buildSegments, buildZones, type Seg, type Zone } from "./segments";
import { sectorTimes, suggestGates, validGates, type SectorGates } from "./sectors";
import type { Channels, Lap, Pt, RawLap, RunData } from "./types";

const maxOf = (a: number[][], f = 1) => {
  let m = f;
  for (const r of a) for (const v of r) if (v > m) m = v;
  return m;
};

/** GPS curvature is an estimate, so endpoints and fixes without a usable heading stay unavailable. */
export function lateralGs(pts: Pt[], times: number[]): number[] {
  return pts.map((p, i) => {
    if (i === 0 || i === pts.length - 1) return NaN;
    let before = i - 1, after = i + 1;
    while (before > 0 && (times[i] - times[before] < 150 || Math.hypot(p[0] - pts[before][0], p[1] - pts[before][1]) < 1)) before--;
    while (after < pts.length - 1 && (times[after] - times[i] < 150 || Math.hypot(pts[after][0] - p[0], pts[after][1] - p[1]) < 1)) after++;
    const a = pts[before], b = pts[after];
    const ax = p[0] - a[0], ay = p[1] - a[1];
    const bx = b[0] - p[0], by = b[1] - p[1];
    const incoming = Math.hypot(ax, ay), outgoing = Math.hypot(bx, by);
    const chord = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (!(times[i] > times[i - 1] && times[i + 1] > times[i]) ||
        times[i] - times[before] > 1500 || times[after] - times[i] > 1500 ||
        incoming < 1 || outgoing < 1 || chord < 1 || p[2] < 1) return NaN;
    // The circumcircle of three fixes avoids heading wrap at ±pi.
    const curvature = 2 * (ax * by - ay * bx) / (incoming * outgoing * chord);
    const g = p[2] ** 2 * curvature / 9.80665;
    return Number.isFinite(g) ? g : NaN;
  });
}

/** Add the channels that are worked out when a run is opened: speed, acceleration, brake percent, curves. */
export function prepareLap(raw: RawLap, brkMax: number, brkrMax: number): Lap {
  const c = raw.ch;
  const spdMs = timeAverage(c.t, raw.pts.map((p) => p[2]));
  const acc = spdMs.map((_, i) => {
    const a = Math.max(c.t[0], c.t[i] - 250);
    const b = Math.min(c.t[c.t.length - 1], c.t[i] + 250);
    return b > a ? (interp(c.t, spdMs, b) - interp(c.t, spdMs, a)) / ((b - a) / 1000) : 0;
  });
  const smoothAcc = timeAverage(c.t, acc);
  const smoothX = timeAverage(c.t, raw.pts.map((p) => p[0]));
  const smoothY = timeAverage(c.t, raw.pts.map((p) => p[1]));
  const playbackPts: Pt[] = raw.pts.map((p, i) =>
    i === 0 || i === raw.pts.length - 1 ? [...p] : [smoothX[i], smoothY[i], p[2], p[3], p[4]]);
  const cellCols = c.cells.length && c.cells[0] ? c.cells[0].map((_, j) => c.cells.map((r) => r[j])) : [];
  const ch: Channels = {
    ...c,
    spd: spdMs.map((v) => v * MPH),
    x: raw.pts.map((p) => p[0]),
    y: raw.pts.map((p) => p[1]),
    brkp: c.brk.map((v) => (v / brkMax) * 100),
    brkrp: c.brkr.map((v) => (v / brkrMax) * 100),
    acc: smoothAcc,
    gacc: smoothAcc.map((a) => a / 9.80665),
    glat: lateralGs(playbackPts.map((p, i) => [p[0], p[1], spdMs[i], p[3], p[4]]), c.t),
    cellCols,
    cellAvg: c.cells.map((r) => (r.length ? r.reduce((a, b) => a + b, 0) / r.length : 0)),
  };
  return { ...raw, ch, sec: raw.sec, playbackPts, curve: curveOf(playbackPts) };
}

/** Everything about a run that does not depend on what the user has marked: laps, track geometry, corners, braking zones. */
export class Session {
  readonly D: RunData;
  readonly geo: Geo;
  readonly RL: number;
  /** Start and finish are in different places, so there is no lap to wrap round. */
  readonly open: boolean;
  readonly laps: Lap[];
  readonly runs: Lap[]; // timed laps, in order
  readonly base: Lap;
  readonly TLAPS: Lap[]; // every lap in time order
  readonly T0: number;
  readonly TD: number;
  readonly brkMax: number;
  readonly brkrMax: number;
  readonly vmaxMph: number;
  readonly accScale: number;
  readonly vlo: number;
  readonly vhi: number;
  readonly segs: Seg[];
  readonly zones: Zone[];
  gates: SectorGates;
  readonly bbox: { x: [number, number]; y: [number, number] };

  constructor(D: RunData) {
    this.D = D;
    this.RL = D.RL;
    this.open = !!D.open;
    this.geo = new Geo(D.ref, D.cum, D.RL, this.open);
    this.brkMax = maxOf(D.laps.map((l) => l.ch.brk));
    this.brkrMax = maxOf(D.laps.map((l) => l.ch.brkr));
    this.laps = D.laps.map((l) => prepareLap(l, this.brkMax, this.brkrMax));
    this.runs = this.laps.filter((l) => l.kind === "lap");
    this.base = this.runs[0];
    this.TLAPS = [...this.laps].sort((a, b) => a.t0 - b.t0);
    this.T0 = this.TLAPS[0].t0;
    this.TD = this.TLAPS[this.TLAPS.length - 1].t1 - this.T0;
    this.vmaxMph = maxOf(this.laps.map((l) => l.ch.spd), 0);
    this.accScale = pctl(this.runs.flatMap((l) => l.ch.acc.map(Math.abs)), 0.95) || 4;
    const spdAll = this.laps.flatMap((l) => l.pts.map((p) => p[2]));
    this.vlo = pctl(spdAll, 0.04);
    this.vhi = Math.max(this.vlo + 1, pctl(spdAll, 0.97));
    this.segs = buildSegments(this.geo);
    this.zones = buildZones(this.runs, this.RL, this.open);
    const representative = this.runs.reduce((a, b) => b.dur < a.dur ? b : a);
    this.gates = suggestGates(representative, this.segs, this.RL);
    this.setGates(this.gates);
    const bounds: { x: [number, number]; y: [number, number] } = { x: [Infinity, -Infinity], y: [Infinity, -Infinity] };
    const include = (point: number[]) => {
      bounds.x[0] = Math.min(bounds.x[0], point[0]); bounds.x[1] = Math.max(bounds.x[1], point[0]);
      bounds.y[0] = Math.min(bounds.y[0], point[1]); bounds.y[1] = Math.max(bounds.y[1], point[1]);
    };
    D.ref.forEach(include); this.runs.forEach(lap => lap.pts.forEach(include));
    this.bbox = bounds;
  }

  setGates(gates: SectorGates): boolean {
    if (!validGates(gates, this.RL)) return false;
    this.gates = [...gates];
    this.laps.forEach((lap) => { lap.sec = sectorTimes(lap, gates); lap.gainKey = undefined; });
    return true;
  }

  sectorAt(distance: number): number {
    return distance < this.gates[0] ? 0 : distance < this.gates[1] ? 1 : 2;
  }

  /** The lap the session clock T (ms from the start of the out lap) falls in. */
  lapIndexAt(T: number): number {
    const a = this.T0 + T;
    for (const l of this.TLAPS) if (a < l.t1) return this.laps.indexOf(l);
    return this.laps.indexOf(this.TLAPS[this.TLAPS.length - 1]);
  }

  lapNo(l: Lap): number {
    return this.laps.indexOf(l) + 1;
  }

  /** Short name for headings: the unfinished lap just reads "Lap N". */
  label(l: Lap): string {
    return l.kind === "out" ? "Out lap" : l.kind === "partial" ? l.name.replace(" (in progress)", "") : l.name;
  }
}
