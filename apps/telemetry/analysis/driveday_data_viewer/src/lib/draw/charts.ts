import { nice } from "../math";
import type { EnergyModel } from "../energy";
import { SETTLE } from "../energy";
import type { Player } from "../player";
import type { Lap } from "../types";
import { MONO, palette, setupCanvas, type Palette } from "./palette";

/* Small charts used by the side panels: bars per lap, lines over the session, and the energy projection. */

export interface ChartGeo {
  pl: number;
  pw: number;
  slot?: number;
  n?: number;
  span?: number;
  t0?: number;
}
/** Where the plot area of each chart canvas sits, so pointer handlers can turn a click into a lap or a time. */
export const chartGeo = new WeakMap<HTMLCanvasElement, ChartGeo>();

export type Col = keyof Palette;

export function axisY(g: CanvasRenderingContext2D, pal: Palette, w: number, h: number, pl: number, pr: number, pt: number, pb: number, lo: number, hi: number, fmt: (v: number) => string) {
  const py = (v: number) => pt + (1 - (v - lo) / (hi - lo)) * (h - pt - pb);
  g.font = MONO(pal);
  g.textAlign = "right";
  g.textBaseline = "middle";
  g.lineWidth = 1;
  nice(lo, hi, 4).t.forEach((v) => {
    if (v < lo - 1e-9 || v > hi + 1e-9) return;
    g.strokeStyle = pal.line;
    g.beginPath();
    g.moveTo(pl, py(v));
    g.lineTo(w - pr, py(v));
    g.stroke();
    g.fillStyle = pal.mute;
    g.fillText(fmt(v), pl - 6, py(v));
  });
  return py;
}

export interface BarOpts {
  n: number;
  series: { col: Col; vals: number[] }[];
  hline?: { v: number; label: string };
  fmt?: (v: number) => string;
  /** Index of the bar to outline. */
  selected: number;
}

export function barChart(canvas: HTMLCanvasElement, o: BarOpts) {
  const c = setupCanvas(canvas);
  if (!c) return;
  const { g, w, h } = c;
  const pal = palette();
  const pl = 42, pr = 8, pt = 10, pb = 22;
  const pw = w - pl - pr;
  const up = Array(o.n).fill(0) as number[];
  const dn = Array(o.n).fill(0) as number[];
  o.series.forEach((s) => s.vals.forEach((v, i) => { if (v >= 0) up[i] += v; else dn[i] += v; }));
  let hi = Math.max(...up, o.hline ? o.hline.v : 0);
  let lo = Math.min(...dn, 0);
  const nt = nice(lo, hi * 1.04, 4);
  lo = nt.lo;
  hi = nt.hi;
  const py = axisY(g, pal, w, h, pl, pr, pt, pb, lo, hi, o.fmt || ((v) => String(v)));
  const slot = pw / o.n;
  const bw = Math.max(3, slot * 0.68);
  for (let i = 0; i < o.n; i++) {
    const x = pl + i * slot + (slot - bw) / 2;
    let u = 0;
    let d = 0;
    o.series.forEach((s) => {
      const v = s.vals[i];
      let y0: number, y1: number;
      if (v >= 0) { y0 = py(u); y1 = py(u + v); u += v; } else { y0 = py(d); y1 = py(d + v); d += v; }
      g.fillStyle = pal[s.col] as string;
      g.fillRect(x, Math.min(y0, y1), bw, Math.max(0.5, Math.abs(y1 - y0)));
    });
    if (i === o.selected) {
      g.strokeStyle = pal.ink;
      g.lineWidth = 2;
      g.strokeRect(x - 1.5, py(up[i]) - 1.5, bw + 3, py(dn[i]) - py(up[i]) + 3);
    }
  }
  if (lo < 0) {
    g.strokeStyle = pal.mute;
    g.lineWidth = 1;
    g.beginPath();
    g.moveTo(pl, py(0));
    g.lineTo(w - pr, py(0));
    g.stroke();
  }
  if (o.hline) {
    g.setLineDash([4, 3]);
    g.strokeStyle = pal.ink;
    g.globalAlpha = 0.7;
    g.lineWidth = 1;
    g.beginPath();
    g.moveTo(pl, py(o.hline.v));
    g.lineTo(w - pr, py(o.hline.v));
    g.stroke();
    g.setLineDash([]);
    g.globalAlpha = 1;
    g.fillStyle = pal.ink;
    g.font = "600 10px " + pal.fBody;
    g.textAlign = "right";
    g.textBaseline = "bottom";
    g.fillText(o.hline.label, w - pr - 2, py(o.hline.v) - 2);
  }
  g.fillStyle = pal.mute;
  g.font = MONO(pal);
  g.textAlign = "center";
  g.textBaseline = "alphabetic";
  const every = slot < 20 ? 2 : 1;
  for (let i = 0; i < o.n; i++) if (i % every === 0) g.fillText(String(i + 1), pl + i * slot + slot / 2, h - 6);
  chartGeo.set(canvas, { pl, pw, slot, n: o.n });
}

export interface LineSeries {
  col: Col;
  lw?: number;
  al?: number;
  arr: (l: Lap) => number[];
}

/** Lines over the timed laps, laid out on session time, with the selected lap shaded and the playhead. */
export function lineTime(p: Player, canvas: HTMLCanvasElement, o: { series: LineSeries[]; fmt?: (v: number) => string }) {
  const c = setupCanvas(canvas);
  if (!c) return;
  const { g, w, h } = c;
  const pal = palette();
  const { runs, T0, laps } = p.session;
  const pl = 42, pr = 8, pt = 10, pb = 22;
  const pw = w - pl - pr;
  const Tl0 = runs[0].t0;
  const Tl1 = runs[runs.length - 1].t1;
  const span = (Tl1 - Tl0) / 1000;
  const px = (s: number) => pl + (s / span) * pw;
  let lo = Infinity;
  let hi = -Infinity;
  o.series.forEach((s) => runs.forEach((l) => s.arr(l).forEach((v) => { if (v < lo) lo = v; if (v > hi) hi = v; })));
  const nt = nice(lo, hi, 4);
  lo = nt.lo;
  hi = nt.hi;
  const py = axisY(g, pal, w, h, pl, pr, pt, pb, lo, hi, o.fmt || ((v) => String(v)));
  const L = laps[p.sel];
  if (L.kind === "lap") {
    g.fillStyle = pal.ink;
    g.globalAlpha = 0.09;
    const a = px((L.t0 - Tl0) / 1000);
    const b = px((L.t1 - Tl0) / 1000);
    g.fillRect(a, pt, b - a, h - pt - pb);
    g.globalAlpha = 1;
  }
  g.fillStyle = pal.mute;
  g.font = MONO(pal);
  g.textAlign = "center";
  g.textBaseline = "alphabetic";
  runs.forEach((l, i) => {
    if (i % 5 === 0) {
      const x = px((l.t0 - Tl0) / 1000);
      g.fillText("L" + (i + 1), x, h - 6);
      g.strokeStyle = pal.line;
      g.beginPath();
      g.moveTo(x, pt);
      g.lineTo(x, h - pb);
      g.stroke();
    }
  });
  o.series.forEach((s) => {
    g.strokeStyle = pal[s.col] as string;
    g.lineWidth = s.lw || 1.8;
    g.globalAlpha = s.al || 1;
    g.lineJoin = "round";
    g.beginPath();
    let st = false;
    runs.forEach((l) => {
      s.arr(l).forEach((v, i) => {
        const x = px((l.t0 - Tl0 + l.ch.t[i]) / 1000);
        const y = py(v);
        if (st) g.lineTo(x, y);
        else g.moveTo(x, y);
        st = true;
      });
    });
    g.stroke();
    g.globalAlpha = 1;
  });
  const ph = (T0 + p.T - Tl0) / 1000;
  if (ph >= 0 && ph <= span) {
    g.strokeStyle = pal.ink;
    g.lineWidth = 2;
    g.beginPath();
    g.moveTo(px(ph), pt);
    g.lineTo(px(ph), h - pb);
    g.stroke();
  }
  chartGeo.set(canvas, { pl, pw, span, t0: Tl0 });
}

/** State of charge by lap, the projection to the reserve, and the plan. */
export function drawEnergy(p: Player, canvas: HTMLCanvasElement, m: EnergyModel) {
  const c = setupCanvas(canvas);
  if (!c) return;
  const { g, w, h } = c;
  const pal = palette();
  const { runs } = p.session;
  const pl = 42, pr = 10, pt = 14, pb = 24;
  const pw = w - pl - pr;
  const per = m.perLap;
  const endLap = per ? Math.min(70, runs.length + Math.ceil((m.socNow - m.reserve) / per)) : runs.length;
  const xmax = Math.max(m.planLaps, endLap, runs.length) + 2;
  const lo0 = Math.max(0, Math.min(m.reserve, Math.min(...m.socLap.slice(SETTLE))) - 6);
  const hi0 = Math.max(...m.socLap.slice(SETTLE)) + 4;
  const nt = nice(lo0, hi0, 4);
  const py = axisY(g, pal, w, h, pl, pr, pt, pb, nt.lo, nt.hi, (v) => v + "%");
  const px = (n: number) => pl + ((n - 1) / (xmax - 1)) * pw;
  g.font = MONO(pal);
  g.textAlign = "center";
  g.textBaseline = "alphabetic";
  g.fillStyle = pal.mute;
  for (let n = 5; n <= xmax; n += 5) g.fillText(String(n), px(n), h - 7);
  g.fillText("lap", px(1), h - 7);
  g.setLineDash([5, 4]);
  g.lineWidth = 1.2;
  g.strokeStyle = pal.red;
  g.beginPath();
  g.moveTo(pl, py(m.reserve));
  g.lineTo(w - pr, py(m.reserve));
  g.stroke();
  g.fillStyle = pal.red;
  g.textAlign = "right";
  g.fillText("reserve " + m.reserve + "%", w - pr, py(m.reserve) - 4);
  g.strokeStyle = pal.mute;
  g.beginPath();
  g.moveTo(px(m.planLaps), pt);
  g.lineTo(px(m.planLaps), h - pb);
  g.stroke();
  g.fillStyle = pal.mute;
  g.textAlign = "right";
  g.fillText("plan " + m.planLaps + " laps", px(m.planLaps) - 4, pt + 8);
  g.setLineDash([]);
  if (per) {
    g.setLineDash([2, 4]);
    g.lineWidth = 2;
    g.strokeStyle = pal.blue;
    g.beginPath();
    g.moveTo(px(runs.length), py(m.socNow));
    const n2 = runs.length + (m.socNow - m.reserve) / per;
    g.lineTo(px(n2), py(m.reserve));
    g.stroke();
    g.setLineDash([]);
    g.fillStyle = pal.blue;
    g.beginPath();
    g.arc(px(n2), py(m.reserve), 4, 0, 7);
    g.fill();
  }
  g.strokeStyle = pal.blue;
  g.lineWidth = 2;
  g.beginPath();
  m.socLap.forEach((v, i) => {
    if (i < SETTLE) return;
    if (i === SETTLE) g.moveTo(px(i + 1), py(v));
    else g.lineTo(px(i + 1), py(v));
  });
  g.stroke();
  m.socLap.forEach((v, i) => {
    g.fillStyle = i < SETTLE ? pal.mute : pal.blue;
    g.globalAlpha = i < SETTLE ? 0.5 : 1;
    g.beginPath();
    g.arc(px(i + 1), py(v), i < SETTLE ? 3 : 2.4, 0, 7);
    g.fill();
  });
  g.globalAlpha = 1;
  if (p.laps[p.sel].kind === "lap") {
    g.strokeStyle = pal.ink;
    g.lineWidth = 1.5;
    g.globalAlpha = 0.6;
    g.beginPath();
    g.moveTo(px(p.sel + 1), pt);
    g.lineTo(px(p.sel + 1), h - pb);
    g.stroke();
    g.globalAlpha = 1;
  }
}
