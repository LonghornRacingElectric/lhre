"use client";

import type { Mode } from "@/lib/player";
import { Menu } from "./Menu";
import { usePlayer, useUi } from "./context";

const MODES: [Mode, string, string?][] = [
  ["speed", "Speed"],
  ["pedal", "Pedals"],
  ["acc", "Accel"],
  ["gain", "Gain", "Colour the lap by where it gains or loses time against the ghost"],
  ["sec", "Sector"],
];

export function MapTools({ dockHidden, onToggleDock }: { dockHidden?: boolean; onToggleDock?: () => void } = {}) {
  const p = usePlayer();
  useUi(p);
  const { session: s } = p;
  const check = (label: string, k: "showSeg" | "showCones" | "ghostAll" | "raceMode" | "consMode" | "layoutOnly" | "showEdges", title?: string) => (
    <label className="chk" title={title}>
      <input type="checkbox" checked={p[k]} onChange={(e) => p.set(k, e.target.checked)} /> {label}
    </label>
  );
  return (
    <div className="maptools">
      <label className="lab">Colour by{" "}
        <select className="mini" aria-label="Colour the lap by" value={p.mode} onChange={(e) => p.set("mode", e.target.value as Mode)}>
          {MODES.map(([m, label]) => <option key={m} value={m}>{label}</option>)}
        </select>
      </label>
      <Menu label="Overlays">
        {check("Corners and braking zones", "showSeg")}
        {check("Cone hits", "showCones", "Show each cone hit where it happened on the track")}
        {check("All laps", "ghostAll")}
        {check("Race replay", "raceMode", "Every clean lap as a dot, all running from the start/finish line together")}
        {check("Consistency", "consMode", "Where you braked and where you were slowest, on every clean lap")}
        {check("Layout only", "layoutOnly", "Show only the standard track layout with its sector colours and gates")}
        {s.D.edges && check("Track edges", "showEdges", "Draw the base layout's road at its real width (11.5 ft), centred 1 ft right of the layout drive's GPS line")}
        {!s.D.edges && <div className="mrow">
          <span className="lab">Base overlay</span>
          <select aria-label="Base overlay lap" value={p.baseIdx} onChange={(e) => p.set("baseIdx", +e.target.value)}>
            {s.TLAPS.map((l) => {
              const i = s.laps.indexOf(l);
              return <option key={i} value={i}>{l.kind === "out" ? "Out lap" : l.kind === "partial" ? l.name.replace(" (in progress)", "") : l.name}</option>;
            })}
          </select>
        </div>}
      </Menu>
      <Menu label="Sectors">
        <span>{p.sectorMethod === "manual" ? "Custom boundaries" : "Equal-distance thirds"}</span>
        <button className="btn" type="button" onClick={() => p.setSectorAtPlayhead(0)}>End S1 here</button>
        <button className="btn" type="button" onClick={() => p.setSectorAtPlayhead(1)}>End S2 here</button>
        <button className="btn" type="button" onClick={() => p.autoSectors()}>Reset to equal distances</button>
        <button className="btn" type="button" onClick={() => p.suggestSectors()}>Suggest corner-based boundaries</button>
        <span className="note2">Pause a completed lap at a useful landmark, then set its boundary. Saved for this track in this browser.</span>
      </Menu>
      <span className="sp" />
      {onToggleDock && <button className="btn" type="button" title="Hide or show the panels (D)" onClick={onToggleDock}>{dockHidden ? "Show panels" : "Hide panels"}</button>}
    </div>
  );
}
