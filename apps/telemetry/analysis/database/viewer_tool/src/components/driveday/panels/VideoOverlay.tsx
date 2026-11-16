"use client";

import { useRef } from "react";
import { posAt } from "@/lib/driveday/geometry";
import { interp } from "@/lib/driveday/math";
import { fmt } from "@/lib/driveday/format";
import { cvS, sU } from "@/lib/driveday/units";
import { ghostCar, lineChevron, roadLine, type LineCalibration } from "@/lib/driveday/video-overlay";
import { useFrame, usePlayer, useUi } from "../context";

export interface OverlaySettings extends LineCalibration {
  ghostCar?: boolean; cameraHeight?: number; hud: boolean; map: boolean; line: boolean; ghost: boolean; position: "left" | "right";
}

export function VideoOverlay({ settings, onCenter }: { settings: OverlaySettings; onCenter: (center: number) => void }) {
  const p = usePlayer();
  useUi(p); useFrame(p);
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<{ x: number; center: number } | null>(null);
  const { L, rel } = p.curState();
  const target = settings.ghost ? p.refLap() ?? L : L;
  const ghost = settings.ghostCar ? ghostCar(L, p.refLap(), rel, settings, settings.cameraHeight ?? 1.1) : null;
  const points = settings.line ? roadLine(L, target, rel, settings) : [];
  const V = (key: "spd" | "thr" | "brkp" | "glat") => interp(L.ch.t, L.ch[key], rel);
  const lat = V("glat");
  const ref = p.session.D.ref;
  const { bbox } = p.session;
  const width = Math.max(1, bbox.x[1] - bbox.x[0]), height = Math.max(1, bbox.y[1] - bbox.y[0]);
  const scale = 80 / Math.max(width, height);
  const X = (x: number) => 50 + (x - (bbox.x[0] + bbox.x[1]) / 2) * scale;
  const Y = (y: number) => 50 - (y - (bbox.y[0] + bbox.y[1]) / 2) * scale;
  const car = posAt(L, rel);
  return <div className="video-overlays">
    {settings.line && <>
      <svg ref={svg} className="video-road-line" viewBox="0 0 1000 562.5" preserveAspectRatio="none" aria-label="Approximate recorded GPS racing line"
        onPointerDown={e => { drag.current = { x: e.clientX, center: settings.center }; e.currentTarget.setPointerCapture(e.pointerId); }}
        onPointerMove={e => { if (drag.current && svg.current) onCenter(Math.max(0.1, Math.min(0.9, drag.current.center + (e.clientX - drag.current.x) / svg.current.getBoundingClientRect().width))); }}
        onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}>
        {points.slice(1).map((point, i) => {
          if (i % 2 !== 0) return null;
          const a = points[i], color = point.brake > 15 ? "#ff5964" : point.throttle > 35 ? "#42e59b" : "#ffd45b";
          const d = lineChevron(a, point);
          return d ? <g key={i}><path d={d} stroke="#071016" strokeWidth="12" opacity=".8" /><path d={d} stroke={color} strokeWidth="6" /></g> : null;
        })}
      </svg>
      <span className="video-line-caption">GPS line preview · {target === L ? "watched lap" : "ghost"}</span>
    </>}
    {ghost && <>
      <svg className="video-ghost-car" viewBox="0 0 1000 562.5" preserveAspectRatio="none" aria-label="Experimental 3D ghost car at equal elapsed lap time">
        {ghost.faces.map((face,i) => <polygon key={i} points={face.points} fill={face.tire ? "#081923" : "#50e4ff"} fillOpacity={face.tire ? 0.55 : 0.25} stroke="#9af0ff" strokeWidth="1.3" strokeOpacity=".8" />)}
      </svg>
      <span className="video-ghost-caption">Ghost · {p.refLap()?.name ?? "not selected"} · {ghost.status}<small>Experimental · equal lap time · flat-road GPS estimate</small></span>
    </>}
    {settings.hud && <div className={"video-telemetry video-telemetry-" + settings.position}>
      <div className="video-speed"><strong>{cvS(p.units, V("spd")).toFixed(0)}</strong><span>{sU(p.units)}</span></div>
      <div className="video-pedal"><span>THR</span><i><b style={{ width: Math.max(0, Math.min(100, V("thr"))) + "%", background: "#42e59b" }} /></i><em>{V("thr").toFixed(0)}%</em></div>
      <div className="video-pedal"><span>BRK</span><i><b style={{ width: Math.max(0, Math.min(100, V("brkp"))) + "%", background: "#ff5964" }} /></i><em>{V("brkp").toFixed(0)}%</em></div>
      <small>{L.name} · {fmt(rel)}<br />LAT {Number.isFinite(lat) ? Math.abs(lat).toFixed(2) + " g " + (lat >= 0 ? "L" : "R") : "—"} · GPS</small>
    </div>}
    {settings.map && <svg className={"video-minimap video-minimap-" + settings.position} viewBox="0 0 100 100" aria-label="Track map and car position">
      <polyline points={ref.map(([x, y]) => `${X(x)},${Y(y)}`).join(" ")} fill="none" stroke="#f3f7fa" strokeWidth="2.5" opacity=".75" />
      <polyline points={target.pts.map(point => `${X(point[0])},${Y(point[1])}`).join(" ")} fill="none" stroke="#42e59b" strokeWidth="1.2" />
      <circle cx={X(car[0])} cy={Y(car[1])} r="4" fill="#fff" stroke="#081015" strokeWidth="1.5" />
    </svg>}
  </div>;
}
