"use client";

import { useContext, useMemo, useState } from "react";
import { sampleQuality } from "@/lib/driveday/review";
import { fmt } from "@/lib/driveday/format";
import type { Player } from "@/lib/driveday/player";
import type { RunListEntry } from "@/lib/driveday/types";
import { runLabel } from "@/lib/driveday/storage";
import { Menu } from "./Menu";
import { PlayerCtx, useUi } from "./context";

export type Theme = "auto" | "light" | "dark";

interface Props {
  runs: RunListEntry[];
  current: string | null;
  theme: Theme;
  onTheme: () => void;
  onSelect: (id: string) => void;
  onDb: (day: number) => void;
  onRemove: (id: string) => void;
}

function Kpis() {
  const p = useContext(PlayerCtx)!;
  useUi(p);
  const a = p.analysis;
  const { runs } = p.session;
  const eligible = runs.filter(l => a.valid(l));
  const pool = eligible.length ? eligible : runs;
  let bi = 0;
  pool.forEach((l, i) => {
    if (a.adj(l) < a.adj(pool[bi])) bi = i;
  });
  const bA = eligible.length ? a.adj(pool[bi]) : NaN;
  const total = runs.reduce((s, l) => s + a.conesOf(l), 0);
  const cb = a.conesOf(pool[bi]);
  const pen = a.pen();
  const items: [string, string | number, React.ReactNode][] = [
    ["Best real", fmt(a.bestLap), <div className="pop" key="r"><h5>Best real</h5><p>{eligible.length ? "Fastest eligible logged lap: " + a.bestRun.name : "No eligible completed laps."}</p></div>],
    ["Best adjusted", fmt(bA), (
      <div className="pop" key="a">
        <h5>Best adjusted</h5>
        <p>{eligible.length ? "Real time plus " + pen.toFixed(1) + " s for every cone." : "No eligible completed laps."}</p>
        <table><tbody>
          <tr><td>{eligible.length ? pool[bi].name + " real" : "No eligible lap"}</td><td>{eligible.length ? fmt(pool[bi].dur) : "—"}</td></tr>
          <tr><td>{cb} cone{cb === 1 ? "" : "s"} x {pen.toFixed(1)} s</td><td>+{(cb * pen).toFixed(3)}</td></tr>
          <tr className="tot"><td>Adjusted</td><td>{fmt(bA)}</td></tr>
        </tbody></table>
      </div>
    )],
    ["Cones", total, <div className="pop" key="c"><h5>Cones</h5><p>Total cones hit across the timed laps. Set them per lap in the Laps panel.</p></div>],
    ["Theoretical best", fmt(a.theo), (
      <div className="pop" key="t">
        <h5>Theoretical best</h5>
        <p>Your fastest S1, S2 and S3, each taken from whichever lap it was set on, added together. It is a target, not a lap you drove.</p>
        <table><tbody>
          {a.bestSec.map((b, k) => <tr key={k}><td>S{k + 1}</td><td>{b.label}</td><td>{fmt(b.v)}</td></tr>)}
          <tr className="tot"><td colSpan={2}>Sum</td><td>{fmt(a.theo)}</td></tr>
        </tbody></table>
        <p>Your best real lap is {fmt(a.bestLap)}, so {((a.bestLap - a.theo) / 1000).toFixed(3)} s is still on the table.</p>
      </div>
    )],
  ];
  /* adjusted time and the cone count only say something once a cone has been hit */
  const shown = total ? items : items.filter(([label]) => label === "Best real" || label === "Theoretical best");
  return (
    <div className="kpis" id="kpis">
      {shown.map(([label, value, pop]) => (
        <div className="kpi has" tabIndex={0} key={label}>
          <b>{value}</b>
          <span>{label} ⓘ</span>
          {pop}
        </div>
      ))}
    </div>
  );
}

function Title({ run }: { run: RunListEntry | undefined }) {
  const p = useContext(PlayerCtx);
  if (!p) return <><h1 id="title">LAPDOG</h1><div className="sub" id="sub">Your driveday, replayed.</div></>;
  return <RunTitle p={p} run={run} />;
}

function RunTitle({ p, run }: { p: Player; run: RunListEntry | undefined }) {
  useUi(p);
  const m = p.session.D.meta;
  const samples = useMemo(() => sampleQuality(p.session.D.quality?.sampleTimes ?? p.session.TLAPS.flatMap(lap => lap.ch.t.map(t => t + lap.t0)).sort((a, b) => a - b)), [p.session]);
  const date = run?.ts ? new Date(run.ts).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "";
  const parts = [
    m.driver, m.venue, m.event, date,
    `${p.session.runs.length} timed laps`,
    `${p.session.RL.toFixed(0)} m ${p.session.open ? "course" : "loop"}`,
    `Recorded samples ${samples.hz.toFixed(1)} Hz`,
  ].filter(Boolean);
  return (
    <>
      <h1 id="title">{m.name || "LAPDOG"}</h1>
      <div className="sub" id="sub">{parts.join(" · ")}</div>
    </>
  );
}

function SettingsMenu({ theme, onTheme }: Pick<Props, "theme" | "onTheme">) {
  const p = useContext(PlayerCtx);
  return p ? <RunSettings p={p} theme={theme} onTheme={onTheme} /> : (
    <Menu label={<span title="Settings" aria-label="Settings">⚙</span>}>
      <div className="mrow">
        <span className="lab">Theme</span>
        <button className="btn" type="button" onClick={onTheme}>Theme: {theme}</button>
      </div>
    </Menu>
  );
}

function RunSettings({ p, theme, onTheme }: Pick<Props, "theme" | "onTheme"> & { p: Player }) {
  useUi(p);
  return (
    <Menu label={<span title="Settings" aria-label="Settings">⚙</span>}>
      {(
        <div className="mrow">
          <span className="lab">Units</span>
          <span className="seg" id="unitseg" role="group" aria-label="Units">
            <button type="button" title="Speed units" onClick={() => p.setUnits({ ...p.units, spd: p.units.spd === "mph" ? "kph" : "mph" })}>{p.units.spd === "kph" ? "km/h" : "mph"}</button>
            <button type="button" title="Temperature units" onClick={() => p.setUnits({ ...p.units, temp: p.units.temp === "C" ? "F" : "C" })}>°{p.units.temp}</button>
          </span>
        </div>
      )}
      <div className="mrow">
        <span className="lab">Theme</span>
        <button className="btn" type="button" title="Switch between automatic, light and dark" onClick={onTheme}>Theme: {theme}</button>
      </div>
    </Menu>
  );
}

interface DbDayEntry { day_id: number; date: string; track_name: string | null; event_type: string | null; driver_name: string | null; laps: number; flags: number }

/** Drive days in the orion database that have laps; picking one loads it into the run list. */
export function DbMenu({ onDb }: { onDb: (day: number) => void }) {
  const [days, setDays] = useState<DbDayEntry[] | string | null>(null);
  const load = () => {
    setDays(null);
    fetch("/api/driveday/db")
      .then(async (r) => { const j = await r.json(); if (!r.ok) throw new Error(j.error || r.statusText); setDays(j.days); })
      .catch((e: Error) => setDays(e.message));
  };
  return (
    <div className="run-selector"><Menu wide ariaLabel="Load a drive day from the database" label="From database" onToggle={(open) => { if (open) load(); }}>
      {days === null ? <p className="run-selector-note">Loading drive days…</p>
        : typeof days === "string" ? <p className="run-selector-note">Database unavailable: {days}</p>
        : !days.length ? <p className="run-selector-note">No drive days with laps yet.</p>
        : <div className="run-selector-list" aria-label="Drive days">
          {days.map((d) => <div className="run-selector-row" key={d.day_id}>
            <button className="run-choice" type="button" onClick={() => onDb(d.day_id)}>
              <strong>{d.date}{d.track_name ? " · " + d.track_name : ""}</strong>
              <small>Day {d.day_id} · {d.laps} laps{d.flags ? " · " + d.flags + " flags" : ""}{d.event_type ? " · " + d.event_type : ""}{d.driver_name ? " · " + d.driver_name : ""}</small>
            </button>
          </div>)}
        </div>}
    </Menu></div>
  );
}

export function Header({ runs, current, theme, onTheme, onSelect, onRemove, onDb }: Props) {
  const p = useContext(PlayerCtx);
  return (
    <header className="top">
      <div>
        <Title run={runs.find((r) => r.id === current)} />
        <div className="runbar">
          {runs.length > 0 && (
            <div className="run-selector"><Menu wide ariaLabel="Select run" label={<span className="run-selector-label">{runs.find(r => r.id === current)?.name || "Choose run"}</span>}>
              <div className="run-selector-list" aria-label="Saved runs">
                {runs.map(r => <div className={"run-selector-row" + (r.id === current ? " selected" : "")} key={r.id}>
                  <button className="run-choice" type="button" aria-current={r.id === current ? "true" : undefined} title={runLabel(r)} onClick={() => onSelect(r.id)}><strong>{r.name || "Untitled run"}</strong><small>{runLabel(r)}</small></button>
                  <button className="run-delete" type="button" aria-label={"Delete " + (r.name || "Untitled run")} title="Delete this saved run" onClick={() => onRemove(r.id)}>Delete</button>
                </div>)}
              </div>
            </Menu></div>
          )}
          <DbMenu onDb={onDb} />
          <SettingsMenu theme={theme} onTheme={onTheme} />
          <div id="workspace-actions" />
        </div>
      </div>
      {p && <Kpis />}
    </header>
  );
}
