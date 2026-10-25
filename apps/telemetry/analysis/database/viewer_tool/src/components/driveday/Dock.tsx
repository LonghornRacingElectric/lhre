"use client";

import { useEffect, useState, type ReactNode } from "react";
import { lsGet, lsSet } from "@/lib/driveday/storage";
import { Menu } from "./Menu";
import { usePlayer, useUi } from "./context";
import { CarPanel } from "./panels/CarPanel";
import { CellsPanel } from "./panels/CellsPanel";
import { ChartPanel, CHARTS } from "./panels/ChartPanels";
import { ComparePanel } from "./panels/ComparePanel";
import { CornersPanel } from "./panels/CornersPanel";
import { EnergyPanel } from "./panels/EnergyPanel";
import { LapsPanel } from "./panels/LapsPanel";
import { AboutPanel, AlertsPanel, DebriefPanel, NotesPanel, SetupPanel } from "./panels/TextPanels";
import { TracePanel } from "./panels/TracePanel";
import { DrivingReviewPanel, QualityPanel, ReportPanel, SetupComparePanel } from "./panels/ReviewPanels";

interface PanelDef {
  id: string;
  chip: string;
  title: string;
  body: ReactNode;
}

const MAIN = ["laps", "trace", "corners", "debrief"];

const PANELS: PanelDef[] = [
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
  { id: "about", chip: "How it works", title: "How it works", body: <AboutPanel /> },
];

/** The panel chips and the one open panel beside the map. */
export function Dock() {
  const p = usePlayer();
  useUi(p);
  const [open, setOpen] = useState<string | null>(() => { const saved = lsGet<string | null>("panel", "laps"); return saved === "video" ? "laps" : saved; });
  useEffect(() => {
    const select = (event: Event) => { const id = (event as CustomEvent<string | null>).detail; setOpen(id == null || PANELS.some(panel => panel.id === id) ? id : "laps"); p.schedule(true); };
    window.addEventListener("driveday:panel", select);
    return () => window.removeEventListener("driveday:panel", select);
  }, [p]);
  const toggle = (id: string) => {
    const next = open === id ? null : id;
    setOpen(next);
    lsSet("panel", next);
    p.schedule(true);
  };
  const label = (d: PanelDef) => {
    if (d.id === "alerts" && p.analysis.alerts.length) return `${d.chip} (${p.analysis.alerts.length})`;
    if (d.id === "notes" && p.user.marks.length) return `${d.chip} (${p.user.marks.length})`;
    return d.chip;
  };
  const chip = (d: PanelDef) => (
    <button key={d.id} className="chip" type="button" aria-pressed={open === d.id} onClick={() => toggle(d.id)}>{label(d)}</button>
  );
  const cur = PANELS.find((d) => d.id === open);
  return (
    <aside className="dock" aria-label="Panels">
      <div className="chips" id="chips">
        {MAIN.map((id) => chip(PANELS.find((d) => d.id === id)!))}
        <Menu label={p.analysis.alerts.length ? "More •" : "More"} chip wide>
          {PANELS.filter((d) => !MAIN.includes(d.id)).map(chip)}
        </Menu>
      </div>
      <div className="panels" id="panels">
        {cur ? (
          <section className="pn panel" id={"pn-" + cur.id} key={cur.id}>
            <div className="pnh">
              <h3>{cur.title}</h3>
              <button className="x" type="button" aria-label={"Close " + cur.title} onClick={() => toggle(cur.id)}>×</button>
            </div>
            <div className="pnb">{cur.body}</div>
          </section>
        ) : (
          <div className="empty">No panels open. Pick one above.</div>
        )}
      </div>
    </aside>
  );
}
