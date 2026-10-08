"use client";

import { useRef } from "react";
import { drawTrace, PL, PR, TRACES, type TraceDef } from "@/lib/draw/traces";
import { interp } from "@/lib/math";
import { usePainter, usePlayer } from "../context";

function Trace({ def }: { def: TraceDef }) {
  const p = usePlayer();
  const ref = usePainter(p, "trace:" + def.key, (c) => drawTrace(p, c, def), true);
  const down = useRef(false);
  const seek = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const d = Math.max(0, Math.min(p.RL, ((e.clientX - r.left - PL) / (r.width - PL - PR)) * p.RL));
    const L = p.laps[p.sel];
    p.setT(L.t0 - p.T0 + interp(L.ch.u, L.ch.t, d));
  };
  return (
    <canvas
      ref={ref}
      style={{ height: def.h }}
      role="img"
      aria-label={def.title + " against distance. Drag to scrub."}
      onPointerDown={(e) => {
        down.current = true;
        e.currentTarget.setPointerCapture(e.pointerId);
        seek(e);
      }}
      onPointerMove={(e) => down.current && seek(e)}
      onPointerUp={() => { down.current = false; }}
    />
  );
}

/** Speed, pedals, steering, torque, power and the gap to the ghost, all against distance round the lap. */
export function TracePanel() {
  return (
    <div className="traces" id="traces">
      {TRACES.map((d) => <Trace key={d.key} def={d} />)}
    </div>
  );
}
