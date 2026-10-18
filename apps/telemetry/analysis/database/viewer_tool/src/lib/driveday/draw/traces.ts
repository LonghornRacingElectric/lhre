import { KMH_PER_MPH } from "../format";
import { interp, nice } from "../math";
import type { Player } from "../player";
import type { Lap } from "../types";
import { sU } from "../units";
import { MONO, palette, setupCanvas, type Palette } from "./palette";

/* Telemetry against distance for the lap being watched, with the ghost lap behind it. */

export const PL = 48;
export const PR = 10;

export interface TraceDef {
  key: string; // channel on Lap.ch, or "dlt" for the time gap
  title: string;
  unit: string;
  col: keyof Palette;
  h: number;
  fill?: boolean;
  lw?: number;
  y?: [number, number];
  sym?: boolean;
  delta?: boolean;
  last?: boolean;
  f: (v: number) => string;
}

const fix0 = (v: number) => v.toFixed(0);
export const TRACES: TraceDef[] = [
  { key: "spd", title: "Speed", unit: "mph", col: "blue", h: 176, fill: true, lw: 2.8, f: fix0 },
  { key: "thr", title: "Throttle", unit: "%", col: "green", h: 76, y: [0, 100], f: fix0 },
  { key: "brkp", title: "Front brake pressure", unit: "% of peak", col: "red", h: 76, y: [0, 100], f: fix0 },
  { key: "steer", title: "Steering column angle", unit: "°", col: "blue", h: 92, sym: true, f: fix0 },
  { key: "trq", title: "Motor torque", unit: "Nm", col: "s2", h: 92, f: fix0 },
  { key: "kw", title: "DC bus power", unit: "kW", col: "purple", h: 92, f: fix0 },
  { key: "dlt", title: "Time vs ghost lap", unit: "s", col: "ink", h: 112, delta: true, last: true, f: (v) => (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(2) },
];

/** Time gap in seconds between a lap and the ghost, at 100 points along the loop. */
function deltaSeries(L: Lap, R: Lap | null, RL: number) {
  const n = 100;
  const u: number[] = [];
  const v: number[] = [];
  const a = interp(L.ch.u, L.ch.t, 0);
  const b = R ? interp(R.ch.u, R.ch.t, 0) : 0;
  for (let i = 0; i < n; i++) {
    const d = (i * RL) / (n - 1);
    u.push(d);
    v.push(R ? (interp(L.ch.u, L.ch.t, d) - a - (interp(R.ch.u, R.ch.t, d) - b)) / 1000 : 0);
  }
  return { u, v };
}

export function drawTrace(p: Player, canvas: HTMLCanvasElement, c: TraceDef) {
  const cv = setupCanvas(canvas);
  if (!cv) return;
  const { g, w, h } = cv;
  const pal = palette();
  const { L, rel } = p.curState();
  const R = p.refLap();
  const RL = p.RL;
  const posU = interp(L.ch.t, L.ch.u, rel);
  const pt = 20;
  const pb = c.last ? 22 : 6;
  const pw = w - PL - PR;
  const ink = pal.ink;
  const mute = pal.mute;
  const col = pal[c.col] as string;
  const px = (d: number) => PL + (d / RL) * pw;
  const kph = c.key === "spd" && p.units.spd === "kph";
  const scale = (a: number[]) => (kph ? a.map((v) => v * KMH_PER_MPH) : a);
  const ch = (l: Lap) => (l.ch as unknown as Record<string, number[]>)[c.key];
  const Sr = c.delta ? deltaSeries(L, R, RL) : { u: L.ch.u, v: scale(ch(L)) };
  const Rs = !c.delta && R && R !== L ? { u: R.ch.u, v: scale(ch(R)) } : null;

  let lo: number, hi: number;
  if (c.y) {
    lo = c.y[0];
    hi = c.y[1];
  } else {
    const all = Sr.v.concat(Rs ? Rs.v : []);
    lo = Math.min(...all);
    hi = Math.max(...all);
    if (c.delta || c.sym) {
      const m = Math.max(Math.abs(lo), Math.abs(hi), c.delta ? 0.3 : 20);
      lo = -m;
      hi = m;
    } else {
      lo = Math.min(lo, 0);
      hi = Math.max(hi, 1);
    }
    const nt = nice(lo, hi, 3);
    lo = nt.lo;
    hi = nt.hi;
  }
  const py = (v: number) => pt + (1 - (v - lo) / (hi - lo)) * (h - pt - pb);
  for (let k = 0; k < 3; k++) {
    g.globalAlpha = 0.08;
    g.fillStyle = pal.sector[k];
    g.fillRect(px((k * RL) / 3), pt, pw / 3, h - pt - pb);
  }
  g.globalAlpha = 1;
  g.font = MONO(pal);
  g.textAlign = "right";
  g.textBaseline = "middle";
  g.lineWidth = 1;
  const ticks = c.fill ? nice(lo, hi, 4).t.filter((v) => v >= lo - 1e-9 && v <= hi + 1e-9) : [lo, hi];
  if (!c.fill && lo < 0 && hi > 0) ticks.push(0);
  ticks.forEach((v) => {
    g.strokeStyle = pal.line;
    g.beginPath();
    g.moveTo(PL, py(v));
    g.lineTo(w - PR, py(v));
    g.stroke();
    g.fillStyle = mute;
    g.fillText(String(+v.toFixed(2)), PL - 6, Math.min(h - pb - 4, Math.max(pt + 4, py(v))));
  });
  g.setLineDash([3, 3]);
  g.strokeStyle = mute;
  g.globalAlpha = 0.5;
  [RL / 3, (2 * RL) / 3].forEach((d) => {
    g.beginPath();
    g.moveTo(px(d), pt);
    g.lineTo(px(d), h - pb);
    g.stroke();
  });
  g.setLineDash([]);
  g.globalAlpha = 1;
  const line = (s: { u: number[]; v: number[] }, color: string, lw: number, al: number) => {
    g.globalAlpha = al;
    g.strokeStyle = color;
    g.lineWidth = lw;
    g.lineJoin = "round";
    g.beginPath();
    let st = false;
    for (let i = 0; i < s.u.length; i++) {
      if (s.u[i] < 0 || s.u[i] > RL) continue;
      const x = px(s.u[i]);
      const y = py(s.v[i]);
      if (st) g.lineTo(x, y);
      else g.moveTo(x, y);
      st = true;
    }
    g.stroke();
    g.globalAlpha = 1;
  };
  if (c.delta) {
    if (!R) {
      g.fillStyle = mute;
      g.textAlign = "center";
      g.font = "12px " + pal.fBody;
      g.fillText("Turn the ghost on to see the time gap", PL + pw / 2, pt + (h - pt - pb) / 2);
    } else {
      ([[pal.red, pt, py(0) - pt], [pal.green, py(0), h - pb - py(0)]] as [string, number, number][]).forEach((a) => {
        g.save();
        g.beginPath();
        g.rect(PL, a[1], pw, a[2]);
        g.clip();
        g.globalAlpha = 0.32;
        g.fillStyle = a[0];
        g.beginPath();
        g.moveTo(px(Sr.u[0]), py(0));
        Sr.u.forEach((u, i) => g.lineTo(px(u), py(Sr.v[i])));
        g.lineTo(px(Sr.u[Sr.u.length - 1]), py(0));
        g.fill();
        g.restore();
      });
      line(Sr, ink, 1.8, 1);
    }
    g.font = "600 11px " + pal.fDisp;
    g.textAlign = "center";
    g.textBaseline = "middle";
    for (let k = 0; k < 3; k++) {
      g.fillStyle = pal.sector[k];
      g.fillText("S" + (k + 1), px(((k + 0.5) * RL) / 3), pt + 9);
    }
    g.fillStyle = mute;
    g.font = MONO(pal);
    g.textBaseline = "alphabetic";
    for (let d = 0; d <= Math.floor(RL / 50) * 50; d += 50) {
      g.textAlign = d === 0 ? "left" : "center";
      g.fillText(d + " m", px(d), h - 5);
    }
  } else {
    if (c.fill) {
      g.save();
      g.globalAlpha = 0.18;
      g.fillStyle = col;
      g.beginPath();
      let f0 = false;
      Sr.u.forEach((u, i) => {
        if (u < 0 || u > RL) return;
        const x = px(u);
        const y = py(Sr.v[i]);
        if (!f0) {
          g.moveTo(x, py(lo));
          f0 = true;
        }
        g.lineTo(x, y);
      });
      g.lineTo(px(Math.min(RL, Sr.u[Sr.u.length - 1])), py(lo));
      g.closePath();
      g.fill();
      g.restore();
    }
    if (Rs) line(Rs, mute, c.fill ? 1.9 : 1.4, 0.95);
    line(Sr, col, c.lw || 2, 1);
    g.fillStyle = col;
    Sr.u.forEach((u, i) => {
      if (u < 0 || u > RL) return;
      g.beginPath();
      g.arc(px(u), py(Sr.v[i]), 1.7, 0, 7);
      g.fill();
    });
  }
  const x = px(posU);
  const v = interp(Sr.u, Sr.v, posU);
  g.strokeStyle = ink;
  g.globalAlpha = 0.6;
  g.lineWidth = 1;
  g.beginPath();
  g.moveTo(x, pt);
  g.lineTo(x, h - pb);
  g.stroke();
  g.globalAlpha = 1;
  g.fillStyle = pal.panel;
  g.strokeStyle = ink;
  g.lineWidth = 2;
  g.beginPath();
  g.arc(x, py(v), 3.5, 0, 7);
  g.fill();
  g.stroke();
  g.textBaseline = "middle";
  g.textAlign = "left";
  g.font = "600 10.5px " + pal.fBody;
  g.fillStyle = mute;
  g.fillText(c.title.toUpperCase(), PL + 2, 9);
  g.textAlign = "right";
  let tx2 = w - PR;
  if (Rs) {
    const rv = interp(Rs.u, Rs.v, posU);
    g.font = MONO(pal);
    g.fillStyle = mute;
    const s = R!.name + ": " + c.f(rv);
    g.fillText(s, tx2, 9);
    tx2 -= g.measureText(s).width + 10;
  }
  g.font = "500 12px " + pal.fMono;
  g.fillStyle = ink;
  g.fillText(c.f(v) + " " + (c.key === "spd" ? sU(p.units) : c.unit), tx2, 9);
}
