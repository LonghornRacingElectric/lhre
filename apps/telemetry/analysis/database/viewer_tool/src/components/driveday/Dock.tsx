"use client";

import { useEffect, useState, type ReactNode } from "react";
import { lsGet, lsSet } from "@/lib/driveday/storage";
import { AnalysisWindow } from "./AnalysisWindow";
import { Menu } from "./Menu";
import { usePlayer, useUi } from "./context";
import { CarPanel } from "./panels/CarPanel";
import { CellsPanel } from "./panels/CellsPanel";
import { ChartPanel, CHARTS } from "./panels/ChartPanels";
import { ComparePanel } from "./panels/ComparePanel";
import { CornersPanel } from "./panels/CornersPanel";
import { EnergyPanel } from "./panels/EnergyPanel";
import { DriverPanel } from "./panels/DriverPanel";
import { LapsPanel } from "./panels/LapsPanel";
import { AlertsPanel, DebriefPanel, NotesPanel, SetupPanel } from "./panels/TextPanels";
import { TracePanel } from "./panels/TracePanel";
import { DrivingReviewPanel, QualityPanel, ReportPanel, SetupComparePanel } from "./panels/ReviewPanels";

interface PanelDef {
  id: string;
  chip: string;
  title: string;
  body: ReactNode;
}

const MAIN = ["driver", "laps", "trace", "corners"];

const PANELS: PanelDef[] = [
  { id: "driver", chip: "Driver", title: "Driver view", body: <DriverPanel /> },
  { id: "laps", chip: "Laps", title: "Laps and cones", body: <LapsPanel /> },
  { id: "trace", chip: "Telemetry", title: "Telemetry by distance", body: <TracePanel /> },
  { id: "corners", chip: "Corners", title: "Corners, straights and braking", body: <CornersPanel /> },
  { id: "compare", chip: "Compare", title: "Lap comparison", body: <ComparePanel /> },
  { id: "quality", chip: "Data quality", title: "Data quality", body: <QualityPanel /> },
  { id: "driving", chip: "Driving review", title: "Braking and section comparison", body: <DrivingReviewPanel /> },
  { id: "setupcompare", chip: "Setup comparison", title: "Setup comparison", body: <SetupComparePanel /> },
  { id: "report", chip: "Export debrief", title: "Shareable debrief", body: <ReportPanel /> },
  { id: "debrief", chip: "Debrief", title: "Debrief", body: <DebriefPanel /> },
  { id: "notes", chip: "Bookmarks", title: "Notes and video bookmarks", body: <NotesPanel /> },
  { id: "setup", chip: "Setup", title: "Run setup and notes", body: <SetupPanel /> },
  { id: "car", chip: "Car", title: "Car state", body: <CarPanel /> },
  { id: "cells", chip: "Cells", title: "Battery cells", body: <CellsPanel /> },
  { id: "energy", chip: "Energy plan", title: "Energy plan", body: <EnergyPanel /> },
  ...CHARTS.map((c) => ({
    id: c.id,
    chip: { sect: "Sectors", pedal: "Pedals", wh: "Energy/lap", kw: "Power", soc: "SOC", pv: "Pack V", cv: "Cell V", celltime: "Cell temps", temp: "Temps" }[c.id] ?? c.title,
    title: c.title,
    body: <ChartPanel id={c.id} />,
  })),
  { id: "alerts", chip: "Alerts", title: "Limit alerts", body: <AlertsPanel /> },
];

/** Independent analysis panels share one playback clock. */
export function Dock() {
  const [axis, setAxis] = useState<"vertical" | "horizontal">(() => lsGet("analysis-axis", "vertical"));
  const changeAxis = (next: "vertical" | "horizontal") => { setAxis(next); lsSet("analysis-axis", next); };
  const p = usePlayer();
  useUi(p);
  const [open, setOpen] = useState<string[]>(() => {
    const saved = lsGet<string | null>("panel", "driver");
    const stored = lsGet<string[]>("panels", saved ? [saved === "video" ? "laps" : saved] : []);
    const valid = [...new Set(stored)].filter(id => PANELS.some(panel => panel.id === id));
    return valid.length || !stored.includes("replay3d") ? valid : ["driver"];
  });
  useEffect(() => {
    const select = (event: Event) => {
      const detail = (event as CustomEvent<string[] | string | null>).detail;
      const next = [...new Set(Array.isArray(detail) ? detail : detail ? [detail] : [])].filter(id => PANELS.some(panel => panel.id === id));
      setOpen(next); lsSet("panels", next); p.schedule(true);
    };
    window.addEventListener("driveday:panel", select); window.addEventListener("driveday:panels", select);
    return () => { window.removeEventListener("driveday:panel", select); window.removeEventListener("driveday:panels", select); };
  }, [p]);
  const toggle = (id: string) => {
    setOpen(current => {
      const next = current.includes(id) ? current.filter(value => value !== id) : [...current, id];
      lsSet("panels", next); lsSet("panel", next[0] ?? null); return next;
    }); p.schedule(true);
  };
  const label = (d: PanelDef) => {
    if (d.id === "alerts" && p.analysis.alerts.length) return `${d.chip} (${p.analysis.alerts.length})`;
    if (d.id === "notes" && p.user.marks.length) return `${d.chip} (${p.user.marks.length})`;
    return d.chip;
  };
  const chip = (d: PanelDef) => (
    <button key={d.id} className="chip" type="button" aria-pressed={open.includes(d.id)} onClick={() => toggle(d.id)}>{label(d)}</button>
  );
  const selected = open.map(id => PANELS.find(d => d.id === id)!).filter(Boolean);
  return (
    <aside className="dock" aria-label="Panels">
      <div className="chips" id="chips">
        {[...new Set([...MAIN, ...open.filter(id => PANELS.some(panel => panel.id === id))])].map((id) => chip(PANELS.find((d) => d.id === id)!))}
        <button className="chip" type="button" aria-label="Arrange analysis panels side by side" aria-pressed={axis === "horizontal"} onClick={() => changeAxis(axis === "horizontal" ? "vertical" : "horizontal")}>⇄</button>
        <Menu label={p.analysis.alerts.length ? "More •" : "More"} chip wide>
          {PANELS.filter((d) => !MAIN.includes(d.id) && !open.includes(d.id)).map(chip)}
        </Menu>
      </div>
      <div className={"panels" + (selected.length > 1 ? " multi-panels" : "")} id="panels" data-axis={axis}>
        {selected.length ? selected.map(cur => (
          <AnalysisWindow key={cur.id} id={cur.id} title={cur.title} layoutKey={axis + open.join(",")} onClose={() => toggle(cur.id)} onMove={(source, side) => { if (side) changeAxis("horizontal"); setOpen(current => {
            const next = [...current]; const from = next.indexOf(source), to = next.indexOf(cur.id);
            if (from < 0 || to < 0) return current;
            next.splice(from, 1); const target = next.indexOf(cur.id); next.splice(target + (side === "right" ? 1 : 0), 0, source); lsSet("panels", next); return next;
          }); }}>{cur.body}</AnalysisWindow>
        )) : (
          <div className="empty">No panels open. Pick one above.</div>
        )}
      </div>
    </aside>
  );
}
