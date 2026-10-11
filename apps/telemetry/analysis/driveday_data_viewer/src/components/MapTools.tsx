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

export function MapTools({ dockHidden, onToggleDock }: { dockHidden: boolean; onToggleDock: () => void }) {
  const p = usePlayer();
  useUi(p);
  const { session: s } = p;
  const check = (label: string, k: "showSeg" | "showCones" | "ghostAll" | "raceMode" | "consMode" | "layoutOnly", title?: string) => (
    <label className="chk" title={title}>
      <input type="checkbox" checked={p[k]} onChange={(e) => p.set(k, e.target.checked)} /> {label}
    </label>
  );
  return (
    <div className="maptools">
      <div className="seg" role="group" aria-label="Colour the lap by">
        {MODES.map(([m, label, title]) => (
          <button key={m} data-m={m} aria-pressed={p.mode === m} title={title} onClick={() => p.set("mode", m)}>{label}</button>
        ))}
      </div>
      <Menu label="Overlays">
        {check("Corners and braking zones", "showSeg")}
        {check("Cone hits", "showCones", "Show each cone hit where it happened on the track")}
        {check("All laps", "ghostAll")}
        {check("Race replay", "raceMode", "Every clean lap as a dot, all running from the start/finish line together")}
        {check("Consistency", "consMode", "Where you braked and where you were slowest, on every clean lap")}
        {check("Layout only", "layoutOnly", "Show only the standard track layout with its sector colours and gates")}
        <div className="mrow">
          <span className="lab">Base overlay</span>
          <select aria-label="Base overlay lap" value={p.baseIdx} onChange={(e) => p.set("baseIdx", +e.target.value)}>
            {s.TLAPS.map((l) => {
              const i = s.laps.indexOf(l);
              return <option key={i} value={i}>{l.kind === "out" ? "Out lap" : l.kind === "partial" ? l.name.replace(" (in progress)", "") : l.name}</option>;
            })}
          </select>
        </div>
      </Menu>
      <span className="sp" />
      <button className="btn" type="button" title="Hide or show the panels (D)" onClick={onToggleDock}>{dockHidden ? "Show panels" : "Hide panels"}</button>
    </div>
  );
}
