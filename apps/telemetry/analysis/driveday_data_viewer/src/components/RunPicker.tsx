"use client";

import { useId, useRef, useState } from "react";
import { directionOf, driveDayGroups, driverOf, filterOptions, filterRuns, layoutEntries, type BrowserEntry } from "@/lib/run-picker";
import type { RunListEntry, Track } from "@/lib/types";

interface Props {
  runs: RunListEntry[];
  current: string | null;
  layouts: Track[];
  currentLayout: string | null;
  onRemoveLayout: (id: string) => void;
  onSelectLayout: (id: string) => void;
  onSelect: (id: string) => void;
  onRemove: (id: string) => void;
}

/** Native modal supplies keyboard focus containment and Escape, above floating workspace panes. */
export function RunPicker({ runs, current, layouts, currentLayout, onSelectLayout, onSelect, onRemove, onRemoveLayout }: Props) {
  const id = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"runs" | "layouts">("runs");
  const [day, setDay] = useState("");
  const [driver, setDriver] = useState("");
  const [direction, setDirection] = useState("");
  const [search, setSearch] = useState("");
  const savedLayouts = layoutEntries(layouts);
  const entries: BrowserEntry[] = tab === "runs" ? runs : savedLayouts;
  const selectedId = tab === "runs" ? current : currentLayout;
  const groups = driveDayGroups(entries);
  const selectedDay = groups.find(g => g.runs.some(r => r.id === selectedId));
  const group = groups.find(g => g.id === day) ?? selectedDay ?? groups[0];
  const currentEntry = currentLayout ? savedLayouts.find(l => l.id === currentLayout) : runs.find(r => r.id === current);
  const currentDay = currentEntry ? driveDayGroups([currentEntry])[0] : undefined;
  const available = group?.runs ?? [];
  const drivers = filterOptions(available, driverOf), directions = filterOptions(available, directionOf);
  // If a filtered run is deleted, stale filter values must not hide the remaining runs.
  const activeDriver = drivers.includes(driver) ? driver : "";
  const activeDirection = directions.includes(direction) ? direction : "";
  const shown = filterRuns(available, activeDriver, activeDirection, search);
  const summary = currentEntry ? `${currentDay?.label} · ${currentLayout ? "Layout" : driverOf(currentEntry)}${currentLayout ? "" : " · " + directionOf(currentEntry)}` : "Choose a run or layout";

  const clearFilters = () => { setDriver(""); setDirection(""); setSearch(""); };
  const close = () => dialog.current?.close();
  const show = () => {
    setTab(currentLayout || !runs.length && savedLayouts.length ? "layouts" : "runs");
    setDay(currentDay?.id ?? "");
    clearFilters();
    const pop = dialog.current;
    if (!pop) return;
    pop.showModal();
    setOpen(true);
    if (window.innerWidth > 600) {
      const anchor = trigger.current!.getBoundingClientRect();
      pop.style.left = Math.max(12, Math.min(anchor.left, window.innerWidth - pop.getBoundingClientRect().width - 12)) + "px";
      pop.style.top = Math.max(12, Math.min(anchor.bottom + 8, window.innerHeight - 300)) + "px";
      pop.style.maxHeight = `calc(100dvh - ${parseFloat(pop.style.top) + 12}px)`;
    } else {
      pop.style.removeProperty("left"); pop.style.removeProperty("top"); pop.style.removeProperty("max-height");
    }
    searchInput.current?.focus();
  };

  return <div className="run-picker">
    <button ref={trigger} className="btn run-picker-trigger" type="button" disabled={!runs.length && !savedLayouts.length}
      aria-haspopup="dialog" aria-expanded={open} aria-controls={id} title={summary} onClick={show}>
      <span>{currentEntry ? <><strong>{currentDay?.label}</strong><small>{currentLayout ? "Layout" : `${driverOf(currentEntry)} · ${directionOf(currentEntry)}`}</small></> : "Choose a run or layout"}</span>
      <span aria-hidden="true">▾</span>
    </button>
    <dialog ref={dialog} id={id} className="run-picker-dialog" aria-labelledby={id + "-title"}
      onClose={() => { setOpen(false); trigger.current?.focus(); }}
      onClick={e => {
        if (e.target !== dialog.current) return;
        const box = dialog.current.getBoundingClientRect();
        if (e.clientX < box.left || e.clientX > box.right || e.clientY < box.top || e.clientY > box.bottom) close();
      }}>
      <div className="run-picker-heading"><h2 id={id + "-title"}>Choose a run or layout</h2><button className="btn" type="button" aria-label="Close run picker" onClick={close}>✕</button></div>
      <div className="run-picker-tabs" role="tablist" aria-label="Saved data" onKeyDown={e => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
        const next = e.key === "Home" ? "runs" : e.key === "End" ? "layouts" : tab === "runs" ? "layouts" : "runs";
        setTab(next); clearFilters();
        e.currentTarget.querySelector<HTMLButtonElement>(`[data-tab="${next}"]`)?.focus(); e.preventDefault();
      }}>
        {(["runs", "layouts"] as const).map(kind => <button key={kind} type="button" role="tab" data-tab={kind} id={id + "-" + kind} aria-controls={id + "-panel"} aria-selected={tab === kind} tabIndex={tab === kind ? 0 : -1}
          onClick={() => { setTab(kind); clearFilters(); }}>{kind === "runs" ? "Runs" : "Layouts"}</button>)}
      </div>
      <div className="run-picker-panel" role="tabpanel" id={id + "-panel"} aria-labelledby={id + "-" + tab}>
      <div className="run-picker-filters">
        <label className="run-picker-day">Drive day<select disabled={!groups.length} value={group?.id ?? ""} onChange={e => { setDay(e.target.value); clearFilters(); }}>
          {!groups.length && <option value="">{tab === "runs" ? "No saved runs" : "No saved layouts"}</option>}
          {groups.map(g => <option key={g.id} value={g.id}>{g.label}</option>)}
        </select></label>
        {tab === "runs" && <label>Driver<select disabled={!available.length} value={activeDriver} onChange={e => setDriver(e.target.value)}><option value="">All drivers</option>{drivers.map(d => <option key={d}>{d}</option>)}</select></label>}
        {tab === "runs" && <label>Direction<select disabled={!available.length} value={activeDirection} onChange={e => setDirection(e.target.value)}><option value="">All directions</option>{directions.map(d => <option key={d}>{d}</option>)}</select></label>}
        <label className="run-picker-search">Search this drive day<input ref={searchInput} disabled={!available.length} type="search" placeholder={tab === "runs" ? "Driver, direction, or session name" : "Layout name"} value={search} onChange={e => setSearch(e.target.value)}
          onKeyDown={e => { if (e.key === "ArrowDown") { dialog.current?.querySelector<HTMLButtonElement>(".run-picker-choice")?.focus(); e.preventDefault(); } }} /></label>
      </div>
      <ul className="run-picker-list" aria-label={tab === "runs" ? "Runs in this drive day" : "Layouts in this drive day"} onKeyDown={e => {
        if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key) || !(e.target as HTMLElement).classList.contains("run-picker-choice")) return;
        const choices = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>(".run-picker-choice"));
        const i = choices.indexOf(e.target as HTMLButtonElement);
        const next = e.key === "Home" ? 0 : e.key === "End" ? choices.length - 1 : (i + (e.key === "ArrowDown" ? 1 : choices.length - 1)) % choices.length;
        choices[next]?.focus(); e.preventDefault();
      }}>
        {shown.map(run => <li key={run.id} className={run.id === selectedId ? "selected" : ""}>
          <button className="run-picker-choice" type="button" aria-current={run.id === selectedId ? "true" : undefined} onClick={() => { close(); if (tab === "layouts") onSelectLayout(run.id); else onSelect(run.id); }}>
            <span className="run-picker-row-title"><strong>{tab === "layouts" ? "Track layout" : driverOf(run)}</strong>{tab === "runs" && <span className="run-picker-direction">{directionOf(run)}</span>}{run.id === selectedId && <span className="run-picker-current">✓ Current</span>}</span>
            <span className="run-picker-session">{run.name || "Untitled run"}</span>
            {run.event && <small>{run.event}</small>}
          </button>
          <button className="run-delete" type="button" aria-label={`Delete ${tab === "layouts" ? "layout" : driverOf(run)} · ${run.name || "Untitled run"}`} onClick={() => tab === "layouts" ? onRemoveLayout(run.id) : onRemove(run.id)}>Delete</button>
        </li>)}
      </ul>
      {!shown.length && <div className="run-picker-empty"><p>{!available.length ? tab === "layouts" ? "No saved layouts. Use Import layout to add a centreline recording." : "No runs for this drive day. Import a driver recording to add one." : "Nothing matches these filters."}</p>{available.length > 0 && <button className="btn" type="button" onClick={clearFilters}>Clear filters</button>}</div>}
      </div>
    </dialog>
  </div>;
}
