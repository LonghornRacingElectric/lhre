"use client";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import type { Mode } from "@/lib/driveday/player";
import { Chk, Pick } from "./controls";
import { Menu } from "./Menu";
import { usePlayer, useUi } from "./context";

const MODES: { value: Mode; label: string; title?: string }[] = [
  { value: "speed", label: "Speed" },
  { value: "pedal", label: "Pedals" },
  { value: "acc", label: "Accel" },
  { value: "gain", label: "Gain", title: "Colour the lap by where it gains or loses time against the ghost" },
  { value: "sec", label: "Sector" },
];

export function MapTools({ dockHidden, onToggleDock }: { dockHidden?: boolean; onToggleDock?: () => void } = {}) {
  const p = usePlayer();
  useUi(p);
  const { session: s } = p;
  const check = (label: string, k: "showSeg" | "showCones" | "ghostAll" | "raceMode" | "consMode" | "layoutOnly", title?: string) => (
    <Chk checked={p[k]} onChange={(v) => p.set(k, v)} title={title}>{label}</Chk>
  );
  return (
    <div className="maptools">
      <Label className="lab gap-2">
        Colour by
        <Pick value={p.mode} onChange={(m) => p.set("mode", m)} options={MODES} ariaLabel="Colour the lap by" />
      </Label>
      <Menu label="Overlays">
        {check("Corners and braking zones", "showSeg")}
        {check("Cone hits", "showCones", "Show each cone hit where it happened on the track")}
        {check("All laps", "ghostAll")}
        {check("Race replay", "raceMode", "Every clean lap as a dot, all running from the start/finish line together")}
        {check("Consistency", "consMode", "Where you braked and where you were slowest, on every clean lap")}
        {check("Layout only", "layoutOnly", "Show only the standard track layout with its sector colours and gates")}
        <div className="mrow">
          <span className="lab">Base overlay</span>
          <Pick
            ariaLabel="Base overlay lap"
            value={String(p.baseIdx)}
            onChange={(v) => p.set("baseIdx", +v)}
            options={s.TLAPS.map((l) => {
              const i = s.laps.indexOf(l);
              return { value: String(i), label: l.kind === "out" ? "Out lap" : l.kind === "partial" ? l.name.replace(" (in progress)", "") : l.name };
            })}
          />
        </div>
      </Menu>
      <Menu label="Sectors">
        <span className="text-xs">{p.sectorMethod === "manual" ? "Custom boundaries" : "Automatic · balanced time, near corner/straight transitions"}</span>
        <Button variant="outline" size="sm" onClick={() => p.setSectorAtPlayhead(0)}>End S1 here</Button>
        <Button variant="outline" size="sm" onClick={() => p.setSectorAtPlayhead(1)}>End S2 here</Button>
        <Button variant="outline" size="sm" onClick={() => p.autoSectors()}>Auto-place sectors</Button>
        <span className="note2">Pause a completed lap at a useful landmark, then set its boundary. Saved for this track in this browser.</span>
      </Menu>
      <span className="sp" />
      {onToggleDock && (
        <Button variant="outline" size="sm" title="Hide or show the panels (D)" onClick={onToggleDock}>{dockHidden ? "Show panels" : "Hide panels"}</Button>
      )}
    </div>
  );
}
