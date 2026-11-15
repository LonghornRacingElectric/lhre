"use client";

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { activeDriverSector, driverPedalsAt, pedalPath, PEDAL_HEIGHT, PEDAL_WIDTH } from "@/lib/driveday/driver-view";
import { clock, fmt } from "@/lib/driveday/format";
import { interp } from "@/lib/driveday/math";
import type { Lap } from "@/lib/driveday/types";
import { useFrame, usePlayer, useUi } from "../context";

function PedalGraph({ best, compare }: { best: Lap | null; compare: boolean }) {
  const p = usePlayer(); useFrame(p);
  const { L, rel } = p.curState();
  const distance = Math.max(0, Math.min(p.RL, interp(L.ch.t, L.ch.u, rel)));
  const reference = compare && best !== L ? best : null;
  const paths = useMemo(() => ({
    throttle: pedalPath(L, "thr", p.RL), brake: pedalPath(L, "brkp", p.RL),
    bestThrottle: reference ? pedalPath(reference, "thr", p.RL) : "",
    bestBrake: reference ? pedalPath(reference, "brkp", p.RL) : "",
  }), [L, reference, p.RL]);
  const down = useRef(false);
  const seek = (event: React.PointerEvent<SVGSVGElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const d = Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)) * p.RL;
    p.setT(L.t0 - p.T0 + interp(L.ch.u, L.ch.t, d));
  };
  const throttle = interp(L.ch.t, L.ch.thr, rel), brake = interp(L.ch.t, L.ch.brkp, rel);
  const x = distance / p.RL * PEDAL_WIDTH;
  const matched = reference && distance >= reference.ch.u[0] && distance <= reference.ch.u.at(-1)! ? driverPedalsAt(reference, distance) : null;
  return <>
    <div className="driver-live">
      <span><strong>{L.name}</strong><small>{clock(rel)} · {Math.round(distance)} m</small></span>
      <span className="driver-pedal-value throttle"><small>Throttle</small><b>{throttle.toFixed(0)}<small>%</small></b>{matched && <em>Best {matched.throttle.toFixed(0)}%</em>}</span>
      <span className="driver-pedal-value brake"><small title="Front brake pressure as a percentage of the session peak">Brake</small><b>{brake.toFixed(0)}<small>%</small></b>{matched && <em>Best {matched.brake.toFixed(0)}%</em>}</span>
    </div>
    <div className="driver-plot">
      <div className="driver-y-axis" aria-hidden="true"><span>100%</span><span>50</span><span>0</span></div>
      <svg className="driver-pedal-svg" viewBox={`0 0 ${PEDAL_WIDTH} ${PEDAL_HEIGHT}`} preserveAspectRatio="none"
        role="slider" tabIndex={0} aria-label="Throttle and brake by lap distance; drag to scrub video and telemetry"
        aria-valuemin={0} aria-valuemax={Math.round(p.RL)} aria-valuenow={Math.round(distance)} aria-valuetext={`${Math.round(distance)} metres, ${clock(rel)}`}
        onPointerDown={event => { down.current = true; event.currentTarget.setPointerCapture(event.pointerId); seek(event); }}
        onPointerMove={event => { if (down.current) seek(event); }} onPointerUp={() => { down.current = false; }} onPointerCancel={() => { down.current = false; }}
        onKeyDown={event => {
          if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); event.stopPropagation(); p.setT(p.T + (event.key === "ArrowRight" ? 1 : -1) * (event.shiftKey ? 1000 : 100)); }
          if (event.key === "Home" || event.key === "End") { event.preventDefault(); p.setT(L.t0 - p.T0 + (event.key === "End" ? L.dur : 0)); }
        }}>
        {[0, 50, 100].map(percent => <line className="driver-grid" key={percent} x1={0} x2={PEDAL_WIDTH} y1={PEDAL_HEIGHT * percent / 100} y2={PEDAL_HEIGHT * percent / 100} />)}
        {p.session.gates.map((gate, index) => <line className="driver-gate" key={index} x1={gate / p.RL * PEDAL_WIDTH} x2={gate / p.RL * PEDAL_WIDTH} y1={0} y2={PEDAL_HEIGHT} />)}
        <path className="driver-trace throttle best" d={paths.bestThrottle} /><path className="driver-trace brake best" d={paths.bestBrake} />
        <path className="driver-trace throttle" d={paths.throttle} /><path className="driver-trace brake" d={paths.brake} />
        <line className="driver-cursor" x1={x} x2={x} y1={0} y2={PEDAL_HEIGHT} />
      </svg>
      <div className="driver-x-axis" aria-hidden="true"><span>0 m</span><span>{Math.round(p.RL / 2)} m</span><span>{Math.round(p.RL)} m</span></div>
    </div>
  </>;
}

function DriverLapTable({ best }: { best: Lap | null }) {
  const p = usePlayer(); useUi(p);
  // The table changes at sector/lap boundaries; the graph alone updates each frame.
  const state = useSyncExternalStore(p.frame.subscribe, () => {
    const { L, rel } = p.curState(); return p.sel * 4 + (activeDriverSector(L, rel) ?? 3);
  }, () => 3);
  const current = Math.floor(state / 4), sector = state % 4;
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const box = container.current, row = box?.querySelector<HTMLElement>('[aria-current="true"]');
    if (!box || !row) return;
    const bounds = box.getBoundingClientRect(), active = row.getBoundingClientRect();
    const header = box.querySelector("thead")?.getBoundingClientRect().height ?? 0;
    if (active.bottom > bounds.bottom) box.scrollTop += active.bottom - bounds.bottom;
    else if (active.top < bounds.top + header) box.scrollTop -= bounds.top + header - active.top;
  }, [current]);
  return <div className="driver-laps scroll" ref={container}><table aria-label="Laps and sector times">
    <thead><tr><th>Lap</th><th>S1</th><th>S2</th><th>S3</th><th>Total</th></tr></thead>
    <tbody>{p.laps.map((lap, index) => ({ lap, index })).filter(({ lap }) => lap.kind === "lap" || lap.kind === "partial").map(({ lap, index }) => (
      <tr key={lap.t0} aria-current={index === current ? "true" : undefined} className={index === current ? "driver-current" : ""}>
        <th scope="row"><button type="button" onClick={() => p.seekLap(index)} title={`Play ${lap.name}`}><strong>{lap.name.replace(" (in progress)", "")}</strong><span>{index === current ? "Current" : lap === best ? "Session best" : ""}{index === current && lap === best ? " · Best" : ""}</span></button></th>
        {[0, 1, 2].map(k => <td key={k} className={index === current && k === sector ? `driver-active-sector sector-${k}` : ""}>
          <button type="button" aria-current={index === current && k === sector ? "step" : undefined} title={`Jump to ${lap.name}, sector ${k + 1}`}
            disabled={lap.sec.slice(0, k).some(t => t == null)} onClick={() => p.setT(lap.t0 - p.T0 + lap.sec.slice(0, k).reduce<number>((sum, value) => sum + (value ?? 0), 0))}>{fmt(lap.sec[k])}</button>
        </td>)}
        <td className={lap === best ? "driver-best-time" : ""}>{lap.kind === "partial" ? "—" : fmt(lap.dur)}</td>
      </tr>
    ))}</tbody>
  </table></div>;
}

export function DriverPanel() {
  const p = usePlayer(); useUi(p);
  const [compare, setCompare] = useState(true);
  const best = Number.isFinite(p.analysis.bestLap) ? p.analysis.bestRun : null;
  return <div className="driver-panel">
    <div className="driver-pedals-section">
    <div className="driver-graph-heading"><h4>Throttle / brake</h4><label className="chk" title="Dashed traces: fastest eligible lap, matched by track distance"><input type="checkbox" checked={compare} disabled={!best} onChange={event => setCompare(event.target.checked)} /> Session best</label></div>
    <PedalGraph best={best} compare={compare} />
    <p className="driver-reference">{best ? `Best: ${best.name} · ${fmt(best.dur)}` : "No eligible session-best lap"}</p>
    </div>
    <div className="driver-timing-section">
    <div className="driver-table-heading"><h4>Laps & sectors</h4><span>Click a lap or split to seek</span></div>
    <DriverLapTable best={best} />
    </div>
  </div>;
}
