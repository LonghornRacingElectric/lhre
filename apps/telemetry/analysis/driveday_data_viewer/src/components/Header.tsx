"use client";

import { useContext, useRef } from "react";
import { gpsStats } from "@/lib/analysis";
import { fmt } from "@/lib/format";
import type { Player } from "@/lib/player";
import type { RunListEntry } from "@/lib/types";
import { runLabel } from "@/lib/storage";
import { Menu } from "./Menu";
import { PlayerCtx, useUi } from "./context";

export type Theme = "auto" | "light" | "dark";

interface Props {
  runs: RunListEntry[];
  current: string | null;
  theme: Theme;
  onTheme: () => void;
  onSelect: (id: string) => void;
  onImport: (files: FileList) => void;
  onRemove: () => void;
}

function Kpis() {
  const p = useContext(PlayerCtx)!;
  useUi(p);
  const a = p.analysis;
  const { runs } = p.session;
  let bi = 0;
  runs.forEach((l, i) => {
    if (a.adj(l) < a.adj(runs[bi])) bi = i;
  });
  const bA = a.adj(runs[bi]);
  const total = runs.reduce((s, l) => s + a.conesOf(l), 0);
  const cb = a.conesOf(runs[bi]);
  const pen = a.pen();
  const bn = runs.indexOf(a.bestRun) + 1;
  const items: [string, string | number, React.ReactNode][] = [
    ["Best real", fmt(a.bestLap), <div className="pop" key="r"><h5>Best real</h5><p>The fastest lap on the clock, with no cone penalty. Lap {bn}.</p></div>],
    ["Best adjusted", fmt(bA), (
      <div className="pop" key="a">
        <h5>Best adjusted</h5>
        <p>Real time plus {pen.toFixed(1)} s for every cone.</p>
        <table><tbody>
          <tr><td>Lap {bi + 1} real</td><td>{fmt(runs[bi].dur)}</td></tr>
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
          {a.bestSec.map((b, k) => <tr key={k}><td>S{k + 1}</td><td>Lap {b.lap}</td><td>{fmt(b.v)}</td></tr>)}
          <tr className="tot"><td colSpan={2}>Sum</td><td>{fmt(a.theo)}</td></tr>
        </tbody></table>
        <p>Your best real lap is {fmt(a.bestLap)}, so {((a.bestLap - a.theo) / 1000).toFixed(3)} s is still on the table.</p>
      </div>
    )],
  ];
  return (
    <div className="kpis" id="kpis">
      {items.map(([label, value, pop]) => (
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
  if (!p) return <><h1 id="title">Drive day visualizer</h1><div className="sub" id="sub" /></>;
  return <RunTitle p={p} run={run} />;
}

function RunTitle({ p, run }: { p: Player; run: RunListEntry | undefined }) {
  useUi(p);
  const m = p.session.D.meta;
  const gp = gpsStats(p.session);
  const date = run?.ts ? new Date(run.ts).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "";
  const parts = [
    m.driver, m.venue, m.event, date,
    `${p.session.runs.length} timed laps`,
    `${p.session.RL.toFixed(0)} m loop`,
    `GPS ${gp.hz.toFixed(1)} Hz${gp.gaps ? ` with ${gp.gaps} gap${gp.gaps === 1 ? "" : "s"}` : ""}`,
    p.sf ? `start/finish moved ${p.sf.toFixed(0)} m` : "",
  ].filter(Boolean);
  return (
    <>
      <h1 id="title">{m.name || "Drive day visualizer"}</h1>
      <div className="sub" id="sub">{parts.join(" · ")}</div>
    </>
  );
}

function SettingsMenu({ theme, onTheme, onRemove }: Pick<Props, "theme" | "onTheme" | "onRemove">) {
  const p = useContext(PlayerCtx);
  return p ? <RunSettings p={p} theme={theme} onTheme={onTheme} onRemove={onRemove} /> : (
    <Menu label="Settings">
      <div className="mrow">
        <span className="lab">Theme</span>
        <button className="btn" type="button" onClick={onTheme}>Theme: {theme}</button>
      </div>
    </Menu>
  );
}

function RunSettings({ p, theme, onTheme, onRemove }: Pick<Props, "theme" | "onTheme" | "onRemove"> & { p: Player }) {
  useUi(p);
  return (
    <Menu label="Settings">
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
      <div className="mrow">
        <button className="btn" type="button" title="Remove this run from the browser" onClick={onRemove}>Remove this run</button>
      </div>
    </Menu>
  );
}

export function Header({ runs, current, theme, onTheme, onSelect, onImport, onRemove }: Props) {
  const file = useRef<HTMLInputElement>(null);
  const p = useContext(PlayerCtx);
  return (
    <header className="top">
      <div>
        <Title run={runs.find((r) => r.id === current)} />
        <div className="runbar">
          <select aria-label="Driver run" value={current ?? ""} onChange={(e) => onSelect(e.target.value)}>
            {runs.length === 0 && <option value="">No runs</option>}
            {runs.map((r) => <option key={r.id} value={r.id}>{runLabel(r)}</option>)}
          </select>
          <button className="btn primary" type="button" title="Import one or more session JSON files" onClick={() => file.current?.click()}>Import JSON</button>
          <input
            ref={file}
            type="file"
            accept=".json,application/json"
            multiple
            hidden
            onChange={(e) => {
              if (e.target.files?.length) onImport(e.target.files);
              e.target.value = "";
            }}
          />
          <SettingsMenu theme={theme} onTheme={onTheme} onRemove={onRemove} />
        </div>
      </div>
      {p && <Kpis />}
    </header>
  );
}
