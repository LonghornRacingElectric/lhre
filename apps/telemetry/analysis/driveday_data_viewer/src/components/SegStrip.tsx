"use client";

import { usePlayer, useFrame, useUi } from "./context";

/** Corners, straights and braking zones as chips. Click one to zoom the map there. */
export function SegStrip() {
  const p = usePlayer();
  useUi(p);
  useFrame(p);
  const { segs, zones } = p.session;
  return (
    <div className="segstrip panel" id="segstrip" aria-label="Corners, straights and braking zones">
      <button className="sc fit" type="button" onClick={() => p.flyFit()}>Full track</button>
      {segs.map((g, i) => (
        <button key={g.name} className={"sc " + g.type + (i === p.curSeg ? " act" : "")} type="button" title={g.name + " · " + g.len.toFixed(0) + " m"} onClick={() => p.focusSeg(i)}>{g.name}</button>
      ))}
      {zones.length > 0 && <span className="sepv" />}
      {zones.map((z, i) => (
        <button key={z.name} className={"sc b" + (i === p.curZone ? " act" : "")} type="button" title={"Braking zone " + z.name} onClick={() => p.focusZone(i)}>{z.name}</button>
      ))}
    </div>
  );
}
