"use client";

import { useRef } from "react";
import { clock } from "@/lib/format";
import { interp } from "@/lib/math";
import { drawTimeline, TPAD, timeX } from "@/lib/draw/timeline";
import { fmtLimit } from "./format";
import { cvS, sU } from "@/lib/units";
import { usePainter, usePlayer, useUi } from "./context";

/** The session timeline: click or drag to scrub, hover to preview. */
export function Timeline() {
  const p = usePlayer();
  useUi(p);
  const ref = usePainter(p, "timeline", (c) => drawTimeline(p, c), true);
  const tip = useRef<HTMLDivElement>(null);
  const down = useRef(false);

  const posOf = (e: React.PointerEvent) => {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    return Math.max(0, Math.min(p.TD, ((e.clientX - r.left - TPAD) / (r.width - 2 * TPAD)) * p.TD));
  };
  const onMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const t = posOf(e);
    p.hoverT = t;
    if (down.current) p.setT(t);
    const s = p.session;
    const l = s.laps[s.lapIndexAt(t)];
    const rel = t + s.T0 - l.t0;
    const nm = l.kind === "out" ? "Out lap" : l.kind === "partial" ? l.name.replace(" (in progress)", "") : l.name;
    let sec = "";
    if (l.sec[0] != null) sec = " · S" + (rel < (l.sec[0] as number) ? 1 : rel < (l.sec[0] as number) + (l.sec[1] as number) ? 2 : 3);
    let text = `${clock(t)} · ${nm}${sec} · ${cvS(p.units, interp(l.ch.t, l.ch.spd, Math.max(0, rel))).toFixed(0)} ${sU(p.units)}`;
    const r = e.currentTarget.getBoundingClientRect();
    const cx0 = e.clientX - r.left;
    const mk = p.user.marks.find((m) => Math.abs(timeX(p, m.t, r.width) - cx0) < 7);
    const al = p.analysis.alerts.find((a) => Math.abs(timeX(p, a.t, r.width) - cx0) < 6);
    const ce = p.user.ce.find((c) => Math.abs(timeX(p, c.t, r.width) - cx0) < 6);
    if (mk) text = "Note · " + clock(mk.t) + " · " + mk.text;
    else if (ce) text = "Cone · " + p.lapLabelAt(ce.t) + " · " + clock(ce.t);
    else if (al) text = "Alert · " + al.d.label + " " + fmtLimit(p.units, al.d, al.val) + " (limit " + fmtLimit(p.units, al.d, al.lim) + ") · " + clock(al.t);
    if (tip.current) {
      tip.current.hidden = false;
      tip.current.textContent = text;
      tip.current.style.left = Math.max(60, Math.min(r.width - 60, cx0)) + "px";
    }
    p.schedule();
  };
  return (
    <div className="tlwrap">
      <canvas
        ref={ref}
        id="tl"
        role="slider"
        aria-label="Session timeline"
        aria-valuemin={0}
        aria-valuemax={Math.round(p.TD / 1000)}
        aria-valuenow={Math.round(p.T / 1000)}
        tabIndex={0}
        onPointerDown={(e) => {
          down.current = true;
          e.currentTarget.setPointerCapture(e.pointerId);
          p.setT(posOf(e));
        }}
        onPointerUp={() => { down.current = false; }}
        onPointerMove={onMove}
        onPointerLeave={() => {
          p.hoverT = null;
          if (tip.current) tip.current.hidden = true;
          p.schedule();
        }}
      />
      <div className="tltip" id="tltip" ref={tip} hidden />
    </div>
  );
}
