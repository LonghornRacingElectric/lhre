import { Geo, curveOf } from "./geometry";
import { MPH } from "./format";
import { pctl } from "./math";
import { buildSegments, buildZones, type Seg, type Zone } from "./segments";
import type { Channels, Lap, RawLap, RunData } from "./types";

const maxOf = (a: number[][], f = 1) => {
  let m = f;
  for (const r of a) for (const v of r) if (v > m) m = v;
  return m;
};

/** Add the channels that are worked out when a run is opened: speed, acceleration, brake percent, curves. */
export function prepareLap(raw: RawLap, brkMax: number, brkrMax: number): Lap {
  const c = raw.ch;
  const spdMs = raw.pts.map((p) => p[2]);
  const acc = spdMs.map((_, i) => {
    const a = Math.max(0, i - 1);
    const b = Math.min(spdMs.length - 1, i + 1);
    const dt = (c.t[b] - c.t[a]) / 1000;
    return dt > 0 ? (spdMs[b] - spdMs[a]) / dt : 0;
  });
  const cellCols = c.cells.length && c.cells[0] ? c.cells[0].map((_, j) => c.cells.map((r) => r[j])) : [];
  const ch: Channels = {
    ...c,
    spd: spdMs.map((v) => v * MPH),
    x: raw.pts.map((p) => p[0]),
    y: raw.pts.map((p) => p[1]),
    brkp: c.brk.map((v) => (v / brkMax) * 100),
    brkrp: c.brkr.map((v) => (v / brkrMax) * 100),
    acc,
    gacc: acc.map((a) => a / 9.80665),
    cellCols,
    cellAvg: c.cells.map((r) => (r.length ? r.reduce((a, b) => a + b, 0) / r.length : 0)),
  };
  return { ...raw, ch, sec: raw.sec, curve: curveOf(raw.pts) };
}

/** Everything about a run that does not depend on what the user has marked: laps, track geometry, corners, braking zones. */
export class Session {
  readonly D: RunData;
  readonly geo: Geo;
  readonly RL: number;
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
  readonly bbox: { x: [number, number]; y: [number, number] };

  constructor(D: RunData) {
    this.D = D;
    this.RL = D.RL;
    this.geo = new Geo(D.ref, D.cum, D.RL);
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
    this.zones = buildZones(this.runs, this.RL);
    const pts = [...D.ref, ...this.runs.flatMap((l) => l.pts.map((p) => [p[0], p[1]] as [number, number]))];
    this.bbox = {
      x: [Math.min(...pts.map((p) => p[0])), Math.max(...pts.map((p) => p[0]))],
      y: [Math.min(...pts.map((p) => p[1])), Math.max(...pts.map((p) => p[1]))],
    };
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
