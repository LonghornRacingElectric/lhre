"use client";

import { useEffect, useRef, useState } from "react";
import { lsGet } from "@/lib/storage";
import { Dock } from "./Dock";
import { MapTools } from "./MapTools";
import { MapView } from "./MapView";
import { SegStrip } from "./SegStrip";
import { DEFAULT_DOCK, Split } from "./Split";
import { Transport } from "./Transport";
import { usePlayer } from "./context";

/** Map and transport on the left, panels on the right, and the keyboard shortcuts for all of it. */
export function Workspace() {
  const p = usePlayer();
  const main = useRef<HTMLDivElement>(null);
  const [dockPx, setDockPx] = useState(() => lsGet<number>("dockpx", DEFAULT_DOCK));
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    p.schedule(true);
  }, [p, dockPx, hidden]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) && (t as HTMLInputElement).type !== "range" && (t as HTMLInputElement).type !== "checkbox") return;
      if (t && (t.tagName === "BUTTON" || t.tagName === "SUMMARY") && (e.key === " " || e.key === "Enter")) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const k = e.key;
      if (k === " " || k === "k") { e.preventDefault(); p.play(!p.playing); }
      else if (k === "ArrowLeft") { e.preventDefault(); if (e.shiftKey) p.stepLap(-1); else p.setT(p.T - 2000); }
      else if (k === "ArrowRight") { e.preventDefault(); if (e.shiftKey) p.stepLap(1); else p.setT(p.T + 2000); }
      else if (k === ",") { e.preventDefault(); p.stepFix(-1); }
      else if (k === ".") { e.preventDefault(); p.stepFix(1); }
      else if (k === "l" || k === "L") p.cycleEnd();
      else if (k === "<" || k === ">") p.cycleRate(k === ">" ? 1 : -1);
      else if (k === "b" || k === "B") { e.preventDefault(); p.jumpZone(e.shiftKey ? -1 : 1); }
      else if (k === "[" || k === "]") p.jumpSeg(k === "]" ? 1 : -1);
      else if (k === "f" || k === "F") p.setFollow(!p.follow);
      else if (k === "0") p.flyFit();
      else if (k === "+" || k === "=") p.zoomBy(1.6);
      else if (k === "-" || k === "_") p.zoomBy(1 / 1.6);
      else if (k === "c" || k === "C") { e.preventDefault(); p.coneKey(e.shiftKey ? -1 : 1); }
      else if (k === "n" || k === "N") { e.preventDefault(); p.setNoteOpen(true); }
      else if (k === "i" || k === "I") p.setLoopA();
      else if (k === "o" || k === "O") p.setLoopB();
      else if (k === "x" || k === "X") p.clearLoop();
      else if (k === ";") p.loopSegment();
      else if (k === "r" || k === "R") p.set("raceMode", !p.raceMode);
      else if (k === "Escape") {
        if (p.pickSF) p.setPickSF(false);
        if (p.noteOpen) p.setNoteOpen(false);
      } else if (k === "d" || k === "D") setHidden((h) => !h);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [p]);

  useEffect(() => {
    const save = () => p.savePos();
    addEventListener("pagehide", save);
    return () => removeEventListener("pagehide", save);
  }, [p]);

  return (
    <div className={"main" + (hidden ? " nodock" : "")} id="main" ref={main} style={{ ["--dock" as string]: dockPx + "px" }}>
      <section className="stage" aria-label="Player">
        <MapTools dockHidden={hidden} onToggleDock={() => setHidden((h) => !h)} />
        <MapView />
        <SegStrip />
        <Transport />
      </section>
      <Split main={main} dock={dockPx} onDock={setDockPx} />
      <Dock />
    </div>
  );
}
