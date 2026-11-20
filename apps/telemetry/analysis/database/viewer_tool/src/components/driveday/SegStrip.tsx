"use client";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { usePlayer, useFrame, useUi } from "./context";

const chip = "shrink-0 font-mono";

/** Corners, straights and braking zones as chips. Click one to zoom the map there. */
export function SegStrip() {
  const p = usePlayer();
  useUi(p);
  useFrame(p);
  if (!p.showSeg) return null;
  const { segs, zones } = p.session;
  return (
    <div className="segstrip panel" id="segstrip" aria-label="Corners, straights and braking zones">
      <Button variant="tool" size="xs" className={chip} onClick={() => p.flyFit()}>Full track</Button>
      {segs.map((g, i) => (
        <Button key={g.name} variant={i === p.curSeg ? "default" : "tool"} size="xs" className={cn(chip, i !== p.curSeg && (g.type === "c" ? "border-[var(--s2)]" : "border-[var(--blue)]"))} title={g.name + " · " + g.len.toFixed(0) + " m"} onClick={() => p.focusSeg(i)}>{g.name}</Button>
      ))}
      {zones.length > 0 && <span className="sepv" />}
      {zones.map((z, i) => (
        <Button key={z.name} variant={i === p.curZone ? "default" : "tool"} size="xs" className={cn(chip, i !== p.curZone && "border-[var(--red)] text-[var(--red)]")} title={"Braking zone " + z.name} onClick={() => p.focusZone(i)}>{z.name}</Button>
      ))}
    </div>
  );
}
