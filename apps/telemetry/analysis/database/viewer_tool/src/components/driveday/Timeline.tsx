"use client";

import { useEffect, useRef } from "react";
import { clock } from "@/lib/driveday/format";
import { interp } from "@/lib/driveday/math";
import { drawTimeline, TPAD, timeX } from "@/lib/driveday/draw/timeline";
import { fmtLimit } from "./format";
import { cvS, sU } from "@/lib/driveday/units";
import { usePainter, usePlayer, useUi } from "./context";

/** The session timeline: click or drag to scrub, hover to preview. */
export function Timeline() {
  const p = usePlayer();
  useUi(p);
  const ref = usePainter(p, "timeline", (c) => drawTimeline(p, c), true);
  const tip = useRef<HTMLDivElement>(null);
  const down = useRef(false);
  const range = p.timelineRange;

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      const current = p.timelineRange;
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? canvas.clientHeight : 1;
      if (event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
        const delta = (event.deltaX || event.deltaY) * unit / canvas.clientWidth * current.span;
        p.setTimelineWindow(current.start + delta, current.end + delta);
      } else if (event.deltaY) {
        const bounds = canvas.getBoundingClientRect();
        const fraction = Math.max(0, Math.min(1, (event.clientX - bounds.left - TPAD) / (bounds.width - 2 * TPAD)));
        p.zoomTimeline(Math.exp(-Math.max(-120, Math.min(120, event.deltaY * unit)) * 0.004), current.start + fraction * current.span);
      }
      if (tip.current) tip.current.hidden = true;
    };
    canvas.addEventListener("wheel", wheel, { passive: false });
    return () => canvas.removeEventListener("wheel", wheel);
  }, [p, ref]);

  const posOf = (e: { clientX: number; currentTarget: EventTarget }) => {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    return Math.max(range.start, Math.min(range.end, range.start + ((e.clientX - r.left - TPAD) / (r.width - 2 * TPAD)) * range.span));
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
        title="Scroll to zoom · Shift+scroll to pan · Double-click to show the whole session"
        aria-valuemin={0}
        aria-valuemax={Math.round(p.TD / 1000)}
        aria-valuenow={Math.round(p.T / 1000)}
        tabIndex={0}
        onDoubleClick={() => p.setTimelineWindow(0, p.TD)}
        onKeyDown={e => { if (e.key === "+" || e.key === "=") p.zoomTimeline(2); else if (e.key === "-") p.zoomTimeline(0.5); else if (e.key === "ArrowLeft" || e.key === "ArrowRight") { const delta = range.span * (e.key === "ArrowLeft" ? -0.2 : 0.2); p.setTimelineWindow(range.start + delta, range.end + delta); } else return; e.preventDefault(); e.stopPropagation(); }}
        onPointerDown={(e) => {
          down.current = true;
          e.currentTarget.setPointerCapture(e.pointerId);
          p.setT(posOf(e));
        }}
        onPointerUp={() => { down.current = false; }}
        onPointerCancel={() => { down.current = false; }}
        onPointerMove={onMove}
        onPointerLeave={() => {
          p.hoverT = null;
          if (tip.current) tip.current.hidden = true;
          p.schedule();
        }}
      />
      {range.span < p.TD && <input className="timeline-pan" type="range" aria-label="Timeline window start" min={0} max={p.TD - range.span} step={20} value={range.start} onInput={e => p.setTimelineWindow(+e.currentTarget.value, +e.currentTarget.value + range.span)} />}
      <div className="tltip" id="tltip" ref={tip} hidden />
    </div>
  );
}
