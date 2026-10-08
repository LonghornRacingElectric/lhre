"use client";

import { useMemo, useRef, useState } from "react";
import { cellGeo, cellStats, cellsAt, COLS, drawCells, NSLOT } from "@/lib/draw/cells";
import { interp } from "@/lib/math";
import { cvT, cvTd, tU } from "@/lib/units";
import { useFrame, usePainter, usePlayer, useUi } from "../context";
import { Tile } from "./Tile";

/** The battery's temperature sensors as a grid, coloured at the playhead. */
export function CellsPanel() {
  const p = usePlayer();
  useUi(p);
  useFrame(p);
  const st = useMemo(() => cellStats(p), [p]);
  const [mode, setMode] = useState<"abs" | "rise">("abs");
  const [hover, setHover] = useState(-1);
  const hoverRef = useRef(-1);
  hoverRef.current = hover;
  const ref = usePainter(p, "cells", (c) => drawCells(p, c, st, mode, hoverRef.current), true);
  const { L, rel } = p.curState();
  const cs = cellsAt(L, rel);
  const V = (k: "cellV" | "vmaxC" | "vavgC") => interp(L.ch.t, L.ch[k], rel);
  const u = p.units;
  let hi = 0;
  let lo = 0;
  let sum = 0;
  cs.forEach((v, j) => {
    if (v > cs[hi]) hi = j;
    if (v < cs[lo]) lo = j;
    sum += v;
  });
  const idx = p.session.D.meta.cellIdx;
  const rise = hover >= 0 ? cs[hover] - st.base[hover] : 0;
  const vmin = V("cellV");
  const vmax = V("vmaxC");
  const vavg = V("vavgC");
  return (
    <>
      <div className="enwrap" style={{ gap: 10 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <div className="seg" role="group" aria-label="Cell colour">
            <button aria-pressed={mode === "abs"} onClick={() => setMode("abs")}>Temperature</button>
            <button aria-pressed={mode === "rise"} onClick={() => setMode("rise")}>Rise since Lap 1</button>
          </div>
          <span className="mono">
            {hover >= 0 ? `Sensor ${idx[hover]} · ${cvT(u, cs[hover]).toFixed(1)} ${tU(u)} · ${rise >= 0 ? "+" : "−"}${Math.abs(cvTd(u, rise)).toFixed(1)} since Lap 1 start` : "Hover a cell for its reading"}
          </span>
        </div>
        <canvas
          ref={ref}
          style={{ height: 132 }}
          role="img"
          aria-label="Battery temperature sensors as a grid, coloured by temperature at the playhead"
          onPointerMove={(e) => {
            const c = e.currentTarget;
            const g = cellGeo.get(c);
            if (!g) return;
            const r = c.getBoundingClientRect();
            const col = Math.floor((e.clientX - r.left - g.padL) / g.cell);
            const row = Math.floor((e.clientY - r.top - g.padT) / g.cell);
            setHover(col >= 0 && col < COLS && row >= 0 && row < 4 ? st.slotJ[row * COLS + col] : -1);
          }}
          onPointerLeave={() => setHover(-1)}
        />
        <div className="tiles">
          <Tile label="Hottest sensor" value={"#" + idx[hi] + " · " + cvT(u, cs[hi]).toFixed(1)} unit={tU(u)} />
          <Tile label="Coolest sensor" value={"#" + idx[lo] + " · " + cvT(u, cs[lo]).toFixed(1)} unit={tU(u)} />
          <Tile label="Temp spread" value={cvTd(u, cs[hi] - cs[lo]).toFixed(1)} unit={tU(u)} />
          <Tile label="Average" value={cvT(u, sum / cs.length).toFixed(1)} unit={tU(u)} />
          <Tile label="Highest cell" value={vmax.toFixed(3)} unit="V" />
          <Tile label="Lowest cell" value={vmin.toFixed(3)} unit="V" />
          <Tile label="Average cell" value={vavg.toFixed(3)} unit="V" />
          <Tile label="Voltage spread" value={((vmax - vmin) * 1000).toFixed(0)} unit="mV" />
        </div>
      </div>
      <div className="note2">
        The log has {idx.length} live temperature sensors out of {NSLOT} channels. Dashed squares are channels that never reported. Sensors appear in the order the battery management system numbers them, because the physical layout is not in the log. Per-cell voltage is not logged either, only the lowest, average and highest cell.
      </div>
    </>
  );
}
