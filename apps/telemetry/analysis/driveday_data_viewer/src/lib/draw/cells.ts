import type { Player } from "../player";
import type { Lap } from "../types";
import { cvT, cvTd, tU } from "../units";
import { heat, MONO, palette, setupCanvas } from "./palette";

/* The battery's temperature sensors as a grid, coloured at the playhead. */

export const NSLOT = 92;
export const COLS = 23;

export interface CellStats {
  cMin: number;
  cMax: number;
  rMax: number; // biggest rise since lap 1 of any sensor
  base: number[]; // readings at the start of lap 1
  hotJ: number; // sensor that got hottest in the session
  coolJ: number; // sensor that ended coolest
  slotJ: number[]; // grid slot -> live sensor index, or −1
}

export function cellStats(p: Player): CellStats {
  const { runs, D } = p.session;
  const idx = D.meta.cellIdx;
  const base = runs[0].ch.cells[0] ?? [];
  let cMin = 1e9, cMax = -1e9, rMax = 0, hotJ = 0, hotV = -1;
  runs.forEach((l) => l.ch.cells.forEach((r) => r.forEach((v, j) => {
    cMin = Math.min(cMin, v);
    cMax = Math.max(cMax, v);
    rMax = Math.max(rMax, v - base[j]);
    if (v > hotV) { hotV = v; hotJ = j; }
  })));
  const lastRow = runs[runs.length - 1].ch.cells;
  const last = lastRow[lastRow.length - 1] ?? [];
  let coolJ = 0;
  last.forEach((v, j) => { if (v < last[coolJ]) coolJ = j; });
  const slotJ = new Array<number>(NSLOT).fill(-1);
  idx.forEach((s, j) => { slotJ[s] = j; });
  return { cMin, cMax, rMax, base, hotJ, coolJ, slotJ };
}

/** Readings at a moment, interpolated between fixes. */
export function cellsAt(L: Lap, rel: number): number[] {
  const t = L.ch.t;
  const C = L.ch.cells;
  if (rel <= t[0]) return C[0];
  if (rel >= t[t.length - 1]) return C[t.length - 1];
  let lo = 0;
  let hi = t.length - 1;
  while (hi - lo > 1) {
    const m = (lo + hi) >> 1;
    if (t[m] <= rel) lo = m;
    else hi = m;
  }
  const f = (rel - t[lo]) / (t[hi] - t[lo] || 1);
  return C[lo].map((v, j) => v + (C[hi][j] - v) * f);
}

export interface CellGeo { padL: number; padT: number; cell: number }
export const cellGeo = new WeakMap<HTMLCanvasElement, CellGeo>();

export function drawCells(p: Player, canvas: HTMLCanvasElement, st: CellStats, mode: "abs" | "rise", hover: number) {
  const c = setupCanvas(canvas);
  if (!c) return;
  const { g, w } = c;
  const pal = palette();
  const { L, rel } = p.curState();
  const cs = cellsAt(L, rel);
  const padL = 34;
  const padT = 6;
  const cell = Math.floor((w - padL - 8) / COLS);
  const sz = cell - 3;
  const abs = mode === "abs";
  cellGeo.set(canvas, { padL, padT, cell });
  g.font = MONO(pal);
  g.textAlign = "right";
  g.textBaseline = "middle";
  g.fillStyle = pal.mute;
  for (let row = 0; row < 4; row++) g.fillText(String(row * COLS), padL - 6, padT + row * cell + sz / 2);
  let hi = 0;
  cs.forEach((v, j) => { if (v > cs[hi]) hi = j; });
  for (let s = 0; s < NSLOT; s++) {
    const row = Math.floor(s / COLS);
    const col = s % COLS;
    const x = padL + col * cell;
    const y = padT + row * cell;
    const j = st.slotJ[s];
    if (j < 0) {
      g.strokeStyle = pal.line;
      g.setLineDash([2, 2]);
      g.lineWidth = 1;
      g.strokeRect(x + 0.5, y + 0.5, sz - 1, sz - 1);
      g.setLineDash([]);
      continue;
    }
    g.fillStyle = abs ? heat(cs[j], st.cMin, st.cMax) : heat(cs[j] - st.base[j], 0, st.rMax);
    g.fillRect(x, y, sz, sz);
    if (j === hi || j === hover) {
      g.strokeStyle = pal.ink;
      g.lineWidth = 2;
      g.strokeRect(x - 1, y - 1, sz + 2, sz + 2);
    }
  }
  const ly = padT + 4 * cell + 6;
  for (let i = 0; i < 40; i++) {
    g.fillStyle = heat(i / 39, 0, 1);
    g.fillRect(padL + i * 3, ly, 3, 8);
  }
  g.fillStyle = pal.mute;
  g.textAlign = "left";
  g.textBaseline = "middle";
  g.fillText(abs ? cvT(p.units, st.cMin).toFixed(0) + tU(p.units) : "+0°", padL + 130, ly + 4);
  g.textAlign = "right";
  g.fillText(abs ? cvT(p.units, st.cMax).toFixed(0) + tU(p.units) : "+" + cvTd(p.units, st.rMax).toFixed(0) + "°", padL + 180, ly + 4);
  g.textAlign = "left";
  g.fillText("ringed: hottest now", padL + 192, ly + 4);
}
