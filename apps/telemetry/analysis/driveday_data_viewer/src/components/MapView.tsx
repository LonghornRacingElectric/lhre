"use client";

import { useEffect, useRef } from "react";
import { clock, MPH } from "@/lib/format";
import { drawMap } from "@/lib/draw/map";
import { gradientCss, rampSpd } from "@/lib/draw/palette";
import { cvS, sU } from "@/lib/units";
import { Hud } from "./Hud";
import { usePainter, usePlayer, useReload, useUi } from "./context";

/* The map stage: canvas, overlay, legend and zoom controls. */

function Legend() {
  const p = usePlayer();
  useUi(p);
  const { mode, units } = p;
  const s = p.session;
  const sw = (c: string, n: string) => (
    <span key={n}><span className="sw" style={{ background: `var(${c})` }} />{n}</span>
  );
  let body;
  if (mode === "speed") body = <span>Speed<span className="ramp" style={{ background: gradientCss(rampSpd) }} />{cvS(units, s.vlo * MPH).toFixed(0)} to {cvS(units, s.vhi * MPH).toFixed(0)} {sU(units)}</span>;
  else if (mode === "pedal") body = <>{sw("--green", "Throttle")}{sw("--red", "Brake")}{sw("--mute", "Coast")}<span>Brighter is harder</span></>;
  else if (mode === "gain") body = <><span>Gaining<span className="ramp" style={{ background: "linear-gradient(90deg,var(--green),var(--line),var(--red))" }} />Losing</span><span>against the ghost{p.refLap() ? "" : " (turn the ghost on)"}</span></>;
  else if (mode === "acc") body = <><span>Braking<span className="ramp" style={{ background: "linear-gradient(90deg,var(--red),var(--line),var(--green))" }} />Accelerating</span><span>±{s.accScale.toFixed(1)} m/s²</span></>;
  else body = <>{["--s1", "--s2", "--s3"].map((c, i) => sw(c, "S" + (i + 1)))}</>;
  return <div className="maplegend" id="legend">{body}</div>;
}

export function MapView() {
  const p = usePlayer();
  useUi(p);
  const reload = useReload();
  const ref = usePainter(p, "map", (c) => drawMap(p, c), true);
  const tip = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    let drag: { x: number; y: number; cx: number; cy: number; moved: boolean } | null = null;
    const hideTip = () => { if (tip.current) tip.current.hidden = true; };
    const down = (e: PointerEvent) => {
      cv.setPointerCapture(e.pointerId);
      drag = { x: e.clientX, y: e.clientY, cx: p.view.cx, cy: p.view.cy, moved: false };
      hideTip();
    };
    const move = (e: PointerEvent) => {
      if (drag) {
        const dx = e.clientX - drag.x;
        const dy = e.clientY - drag.y;
        if (!drag.moved && Math.hypot(dx, dy) > 5) {
          drag.moved = true;
          p.takeCamera();
        }
        if (drag.moved) {
          p.view.cx = drag.cx - dx / p.sc;
          p.view.cy = drag.cy + dy / p.sc;
          p.schedule();
        }
        return;
      }
      const r = cv.getBoundingClientRect();
      const mx = e.clientX - r.left;
      const my = e.clientY - r.top;
      if (p.pickSF) {
        const nr = p.session.geo.nearest((mx - p.ox) / p.sc, (p.oy - my) / p.sc);
        p.pickPrev = nr.d < 30 ? nr.s : null;
        hideTip();
        p.schedule();
        return;
      }
      if (p.layoutOnly || !tip.current) return hideTip();
      const L = p.laps[p.sel];
      let bi = -1;
      let bd = 18;
      L.pts.forEach((q, i) => {
        const d = Math.hypot(p.X(q[0]) - mx, p.Y(q[1]) - my);
        if (d < bd) { bd = d; bi = i; }
      });
      if (bi < 0) return hideTip();
      const c = L.ch;
      const g = c.gacc[bi];
      tip.current.hidden = false;
      tip.current.textContent = `${cvS(p.units, c.spd[bi]).toFixed(0)} ${sU(p.units)} · THR ${c.thr[bi].toFixed(0)} · BRK ${c.brkp[bi].toFixed(0)} · ${g >= 0 ? "+" : "−"}${Math.abs(g).toFixed(2)} g · ${clock(c.t[bi])}`;
      tip.current.style.left = Math.max(4, Math.min(r.width - 250, mx + 14)) + "px";
      tip.current.style.top = Math.max(4, my - 32) + "px";
    };
    const leave = () => {
      hideTip();
      if (p.pickSF) { p.pickPrev = null; p.schedule(); }
    };
    const up = (e: PointerEvent) => {
      const was = drag;
      drag = null;
      if (!was || was.moved) return;
      const r = cv.getBoundingClientRect();
      const mx = e.clientX - r.left;
      const my = e.clientY - r.top;
      if (p.pickSF) {
        const nr = p.session.geo.nearest((mx - p.ox) / p.sc, (p.oy - my) / p.sc);
        if (nr.d > 30) { p.toast("That is too far from the track. Click on the line.", "err"); return; }
        p.commitSF(nr.s, reload);
        return;
      }
      for (const h of p.hits) {
        if (Math.hypot(h.x - mx, h.y - my) <= h.r) { h.fn(); return; }
      }
      const L = p.laps[p.sel];
      let bi = -1;
      let bd = 30;
      L.pts.forEach((q, i) => {
        const d = Math.hypot(p.X(q[0]) - mx, p.Y(q[1]) - my);
        if (d < bd) { bd = d; bi = i; }
      });
      if (bi >= 0) p.setT(L.t0 - p.T0 + L.ch.t[bi]);
    };
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      p.takeCamera();
      const r = cv.getBoundingClientRect();
      const mx = e.clientX - r.left;
      const my = e.clientY - r.top;
      const wx = (mx - p.ox) / p.sc;
      const wy = (p.oy - my) / p.sc;
      const z2 = Math.max(1, Math.min(14, p.view.z * Math.exp(-e.deltaY * 0.0016)));
      const sc2 = p.scFit * z2;
      p.view.z = z2;
      p.view.cx = (p.W / 2 - (mx - sc2 * wx)) / sc2;
      p.view.cy = (my + sc2 * wy - p.cyScreen) / sc2;
      p.schedule();
    };
    const dbl = () => p.flyFit();
    cv.addEventListener("pointerdown", down);
    cv.addEventListener("pointermove", move);
    cv.addEventListener("pointerup", up);
    cv.addEventListener("pointerleave", leave);
    cv.addEventListener("wheel", wheel, { passive: false });
    cv.addEventListener("dblclick", dbl);
    return () => {
      cv.removeEventListener("pointerdown", down);
      cv.removeEventListener("pointermove", move);
      cv.removeEventListener("pointerup", up);
      cv.removeEventListener("pointerleave", leave);
      cv.removeEventListener("wheel", wheel);
      cv.removeEventListener("dblclick", dbl);
    };
  }, [p, reload, ref]);

  return (
    <div className={"viewer panel" + (p.layoutOnly ? " layoutonly" : "")} id="viewer">
      <canvas ref={ref} id="cv" role="img" aria-label="GPS map of the current lap over the track layout. Click the track to jump to that point." style={p.pickSF ? { cursor: "copy" } : undefined} />
      <Hud />
      <Legend />
      <div className="maptip" id="maptip" ref={tip} hidden />
      <div className="zoomctl">
        <button className="btn sq" type="button" aria-label="Zoom in" title="Zoom in (+)" onClick={() => p.zoomBy(1.6)}>+</button>
        <button className="btn sq" type="button" aria-label="Zoom out" title="Zoom out (-)" onClick={() => p.zoomBy(1 / 1.6)}>−</button>
        <button className="btn sq" type="button" title="Fit the whole track (0)" onClick={() => p.flyFit()}>Fit</button>
        <button className="btn sq" type="button" aria-pressed={p.follow} title="Follow the car (F)" onClick={() => p.setFollow(!p.follow)}>Follow</button>
      </div>
    </div>
  );
}
