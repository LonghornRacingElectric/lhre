"use client";

import { useContext, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { lsGet, lsSet } from "@/lib/storage";
import { defaultWorkspace, movePane, resizePair, restoreWorkspace, PANE_IDS, type PaneId, type PaneState, type WorkspaceLayout } from "@/lib/workspace";
import type { RunData, Track } from "@/lib/types";
import { LayoutView } from "./LayoutView";
import { Dock, PanelDock } from "./Dock";
import { MapTools } from "./MapTools";
import { MapView } from "./MapView";
import { SegStrip } from "./SegStrip";
import { WorkspacePane, PaneDivider } from "./WorkspacePane";
import { VideoPanel } from "./panels/VideoPanel";
import { Transport } from "./Transport";
import { PlayerCtx } from "./context";

/** Independent video, track and analysis windows share one playback clock. */
export function Workspace({ track, telemetry }: { track?: Track; telemetry?: RunData } = {}) {
  const p = useContext(PlayerCtx);
  const [layout, setLayout] = useState(() => restoreWorkspace(lsGet<WorkspaceLayout | null>("workspace", null)));
  const [timelineMode, setTimelineMode] = useState<"full" | "track">(() => lsGet<string>("timeline-layout", "full") === "track" ? "track" : "full");
  const [toolbarHost, setToolbarHost] = useState<HTMLElement | null>(null);
  useEffect(() => { setToolbarHost(document.getElementById("workspace-actions")); }, []);
  const [timelineHeight, setTimelineHeight] = useState(() => Math.max(48, Math.min(240, lsGet<number>("timeline-height", 108))));
  const resizeTimeline = (height: number) => { setTimelineHeight(height); lsSet("timeline-height", height); };
  const [z, setZ] = useState<Record<PaneId, number>>({ video: 100, track: 101, analysis: 102 });
  const nextZ = useRef(102);
  const update = (fn: (current: WorkspaceLayout) => WorkspaceLayout) => setLayout(current => {
    const next = fn(current); lsSet("workspace", next); return next;
  });
  const patch = (id: PaneId, changes: Partial<PaneState>) => update(current => ({ ...current, panes: { ...current.panes, [id]: { ...current.panes[id], ...changes } } }));
  const names: Record<PaneId, string> = { video: "Video", track: "Track", analysis: "Analysis" };
  const docked = layout.order.filter(id => layout.panes[id].visible && !layout.panes[id].floating);
  const toggleTimelineDock = () => { const next = timelineMode === "track" ? "full" : "track"; setTimelineMode(next); lsSet("timeline-layout", next); };

  useEffect(() => {
    const fit = () => setLayout(current => {
      const panes = { ...current.panes }; let changed = false;
      for (const id of PANE_IDS) {
        const pane = panes[id];
        if (!pane.floating) continue;
        const width = Math.min(pane.width, window.innerWidth - 16), height = Math.min(pane.height, window.innerHeight - 16);
        const x = Math.max(8, Math.min(pane.x, window.innerWidth - width - 8));
        const y = Math.max(8, Math.min(pane.y, window.innerHeight - height - 8));
        if (width !== pane.width || height !== pane.height || x !== pane.x || y !== pane.y) { panes[id] = { ...pane, width, height, x, y }; changed = true; }
      }
      if (!changed) return current;
      const next = { ...current, panes }; lsSet("workspace", next); return next;
    });
    fit(); window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, []);

  useEffect(() => {
    p?.schedule(true);
  }, [p, layout, timelineMode, timelineHeight]);

  useEffect(() => {
    if (!p) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest(".video-panel, .pane-header, .layout-toolbar, .pane-divider, .pane-resize, .timeline-resize")) return;
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
        if (p.noteOpen) p.setNoteOpen(false);
      } else if (k === "d" || k === "D") setLayout(current => { const next = { ...current, panes: { ...current.panes, analysis: { ...current.panes.analysis, visible: !current.panes.analysis.visible } } }; lsSet("workspace", next); return next; });
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [p]);

  /* other runs on this layout and direction, for the Driver ghost */
  useEffect(() => {
    void p?.findRivals();
  }, [p]);

  useEffect(() => {
    const save = () => p?.savePos();
    addEventListener("pagehide", save);
    return () => removeEventListener("pagehide", save);
  }, [p]);

  const transport = () => p ? <Transport height={timelineHeight} onHeight={resizeTimeline} docked={timelineMode === "track"} onPlacement={toggleTimelineDock} /> :
    <div className="transport panel empty-transport" aria-label="Playback unavailable without a driver run">
      <div className="trow"><button className="btn" disabled aria-label="Previous lap">⏮</button><button className="btn play" disabled>Play</button><button className="btn" disabled aria-label="Next lap">⏭</button><span className="time">— / —</span><span className="sp" /><button className="btn" disabled>Ghost: Off</button><button className="btn" disabled>+ Note</button><button className="btn" disabled>+ Cone</button></div>
      <div className="empty-timeline" style={{ height: timelineHeight }} aria-label="Empty timeline">{telemetry ? "GPS playback unavailable. Review recorded channels against time in Analysis." : "Import or select a driver run to see laps and telemetry."}</div>
    </div>;

  const toolbar = <div className="layout-toolbar" aria-label="Workspace layout">
        <span className="lab">Windows</span>
        {PANE_IDS.map(id => <button className="chip" key={id} aria-pressed={layout.panes[id].visible} onClick={() => patch(id, { visible: !layout.panes[id].visible })}>{names[id]}</button>)}
        <span className="sp" /><span className="layout-hint">Drag headers to move · dividers to resize</span>
        <button className="btn" onClick={() => { update(() => defaultWorkspace()); setTimelineMode("full"); lsSet("timeline-layout", "full"); resizeTimeline(108); }}>Reset layout</button>
      </div>;
  return (
    <div className="workspace" id="main">
      {toolbarHost ? createPortal(toolbar, toolbarHost) : toolbar}
      <div className="workspace-panes">
        {PANE_IDS.map(id => <WorkspacePane key={id} id={id} title={names[id]} state={layout.panes[id]} order={layout.order.indexOf(id)} z={z[id]}
          onRaise={() => setZ(current => ({ ...current, [id]: ++nextZ.current }))}
          onPatch={changes => patch(id, changes)}
          onMove={direction => { const target = layout.order[layout.order.indexOf(id) + direction]; if (target) update(current => movePane(current, id, target)); }}
          onDrop={source => update(current => movePane(current, source, id))}>
          {id === "video" ? p ? <VideoPanel visible={layout.panes.video.visible} /> : <div className="empty">{telemetry ? "Synchronized replay is unavailable without a GPS-supported run." : "No driver run selected."}</div> : id === "analysis" ? p ? <Dock /> : <PanelDock telemetry={telemetry} /> : !p && telemetry ? <section className="empty gps-unavailable" aria-label="GPS unavailable"><h3>GPS route unavailable</h3><p>Missing, frozen or interrupted GPS cannot establish a trustworthy track.</p><p>Recorded telemetry and logged interval times remain available in Analysis. No map, speed estimate, sectors or distance comparison are invented.</p></section> : !p && track ? <><LayoutView track={track} />{timelineMode === "track" && layout.panes.track.visible && transport()}</> :
            <section className="stage" aria-label="Player"><MapTools /><MapView /><SegStrip />{timelineMode === "track" && layout.panes.track.visible && transport()}</section>}
        </WorkspacePane>)}
        {docked.slice(0, -1).map((id, i) => <PaneDivider key={id + docked[i + 1]} left={id} right={docked[i + 1]} order={layout.order.indexOf(id)} fraction={layout.panes[id].weight / (layout.panes[id].weight + layout.panes[docked[i + 1]].weight)} onResize={fraction => update(current => resizePair(current, id, docked[i + 1], fraction))} />)}
        {!docked.length && <div className="workspace-empty">All windows are hidden or floating. Use the buttons above to restore them.</div>}
      </div>
      {(timelineMode !== "track" || !layout.panes.track.visible) && <section className={"workspace-timeline" + (timelineHeight < 70 ? " is-compact" : "")} aria-label="Playback and full-width timeline">{transport()}</section>}
    </div>
  );
}
