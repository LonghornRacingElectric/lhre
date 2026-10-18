"use client";

import { sectorClass } from "@/lib/analysis";
import { lapsCsv, summaryText } from "@/lib/debrief";
import { copyText, saveFile } from "@/lib/download";
import { fmt } from "@/lib/format";
import { clock } from "@/lib/format";
import { usePlayer, useUi } from "../context";
import { Field } from "./Tile";

const FLAGS: [string, string][] = [["", "—"], ["off", "Off line"], ["traffic", "Traffic"], ["spin", "Spin"], ["mistake", "Mistake"], ["skip", "Skip lap"]];

function ConeList() {
  const p = usePlayer();
  const { ce } = p.user;
  if (!ce.length) return <div className="conelist"><span className="cl-empty">Press C the moment a cone is hit. It is pinned to that moment and to that spot on the track.</span></div>;
  return (
    <div className="conelist">
      {ce.map((e, i) => {
        const w = p.coneWhere(e);
        if (w.lap.kind !== "lap") return null;
        return (
          <span className="cchip" key={e.id}>
            <button type="button" title="Jump to this hit" onClick={() => p.setT(Math.max(0, e.t - 500))}>
              {w.lap.name} · {clock(w.rel)}{w.seg ? " · " + w.seg : ""}
            </button>
            <button className="x" type="button" aria-label="Delete this cone hit" onClick={() => p.deleteCone(i)}>×</button>
          </span>
        );
      })}
    </div>
  );
}

export function LapsPanel() {
  const p = usePlayer();
  useUi(p);
  const a = p.analysis;
  const { laps, runs, base } = p.session;
  const order = laps.map((_, i) => i).sort((x, y) => {
    const r = { out: 0, lap: 1, partial: 2, theo: 3 } as const;
    return r[laps[x].kind] - r[laps[y].kind] || x - y;
  });
  const bA = Math.min(...runs.map((l) => a.adj(l)));
  const m = p.session.D.meta;
  return (
    <>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Lap</th>
              <th><i style={{ background: "var(--s1)" }} />S1</th>
              <th><i style={{ background: "var(--s2)" }} />S2</th>
              <th><i style={{ background: "var(--s3)" }} />S3</th>
              <th>Real</th><th>Cones</th><th>Adjusted</th><th>Mark</th>
            </tr>
          </thead>
          <tbody id="tb">
            {order.map((i, n) => {
              const l = laps[i];
              const isLap = l.kind === "lap";
              const c = a.conesOf(l);
              const no = i + 1;
              const f = p.user.flags[no] || {};
              const cur = f.x ? "skip" : f.f || "";
              const prev = n > 0 ? laps[order[n - 1]].kind : null;
              const sep = (n > 0 && prev !== l.kind && l.kind !== "out") || (isLap && prev === "out");
              const bestR = isLap && Math.abs(l.dur - a.bestLap) < 1;
              const bestA = isLap && Math.abs(a.adj(l) - bA) < 1;
              const tag = l.kind === "out" ? "OUT" : l.kind === "partial" ? "PARTIAL" : l === base ? "BASE" : "";
              return (
                <tr
                  key={i}
                  tabIndex={0}
                  className={(sep ? "sep" : "") + (i === p.sel ? " on" : "") + (isLap && !a.valid(l) ? " excl" : "")}
                  onClick={(e) => {
                    if ((e.target as HTMLElement).closest("select,input,label,button")) return;
                    p.seekLap(i);
                  }}
                  onKeyDown={(e) => {
                    if ((e.target as HTMLElement).closest("button,select")) return;
                    const row = e.currentTarget;
                    if (e.key === "Enter") { e.preventDefault(); p.seekLap(i); }
                    if (e.key === "ArrowDown") { e.preventDefault(); (row.nextElementSibling as HTMLElement | null)?.focus(); }
                    if (e.key === "ArrowUp") { e.preventDefault(); (row.previousElementSibling as HTMLElement | null)?.focus(); }
                  }}
                >
                  <td>
                    {l.name.replace(" (in progress)", "")}
                    {tag && <span className="tag">{tag}</span>}
                    {p.cmp === "lap" && i === p.ghostLapI && <span className="tag" style={{ color: "var(--rival)" }}>GHOST</span>}
                  </td>
                  {[0, 1, 2].map((k) => (
                    <td key={k}><span className={"c " + sectorClass(a, l, k)}>{l.sec && l.sec[k] != null ? fmt(l.sec[k]) : "—"}</span></td>
                  ))}
                  <td><span className={"c0" + (bestR ? " c p" : "")}>{l.kind === "partial" ? "—" : fmt(l.dur)}</span></td>
                  <td>
                    {isLap ? (
                      <span className="step">
                        <button type="button" aria-label={"Remove a cone on " + l.name} disabled={!c} onClick={() => p.setCone(no, -1)}>−</button>
                        <b className={c ? "on" : ""}>{c}</b>
                        <button type="button" aria-label={"Add a cone on " + l.name} onClick={() => p.setCone(no, 1)}>+</button>
                      </span>
                    ) : "—"}
                  </td>
                  <td><span className={"c0" + (bestA ? " c p" : "")}>{isLap ? fmt(a.adj(l)) : "—"}</span></td>
                  <td>
                    {isLap && (
                      <>
                        <button className="gbtn" type="button" title="Use this lap as the ghost" onClick={() => { p.set("cmp", "lap"); p.set("ghostLapI", i); }}>Ghost</button>
                        <select
                          className="flagsel"
                          aria-label={"Mark for " + l.name}
                          title="Skip leaves the lap out of the bests, the debrief and the consistency view"
                          value={cur}
                          onChange={(e) => p.setFlag(no, e.target.value === "skip" ? { f: "", x: 1 } : { f: e.target.value, x: 0 })}
                        >
                          {FLAGS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
                        </select>
                      </>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="penrow">
        <Field label="Penalty per cone (s)" k="pen" value={p.user.setup.pen} placeholder="2" onValue={(v) => p.setSetup("pen", v)} />
        <span>Real time plus the penalty for each cone. Click a lap to jump to it.</span>
      </div>
      <div className="penrow" style={{ borderTop: "1px solid var(--line)" }}>
        <button
          className="btn"
          type="button"
          onClick={() => {
            const ok = saveFile((m.name || "session").replace(/[^\w.-]+/g, "_") + "-laps.csv", lapsCsv(a, p.units), "text/csv");
            p.toast(ok ? "Lap table exported." : "Could not save the file.", ok ? "" : "err");
          }}
        >
          Export CSV
        </button>
        <button
          className="btn"
          type="button"
          onClick={async () => {
            const ok = await copyText(summaryText(a, p.units));
            p.toast(ok ? "Summary copied." : "Could not copy. Select the debrief text instead.", ok ? "" : "err");
          }}
        >
          Copy summary
        </button>
      </div>
      <ConeList />
    </>
  );
}
