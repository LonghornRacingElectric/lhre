"use client";

import { useEffect, useState } from "react";
import { comparisonLap, sameRoute } from "@/lib/driveday/comparison";
import { fmt } from "@/lib/driveday/format";
import { rebuildRun } from "@/lib/driveday/rebuild";
import { runLabel, runStore } from "@/lib/driveday/storage";
import type { RunData, RunListEntry } from "@/lib/driveday/types";
import { usePlayer } from "../context";

export function ExternalComparison() {
  const p = usePlayer();
  const [runs, setRuns] = useState<RunListEntry[]>([]);
  const [data, setData] = useState<RunData | null>(null);
  const [id, setId] = useState("");
  useEffect(() => {
    let alive = true;
    void runStore.list().then(list => { if (alive) setRuns(list.filter(r => r.id !== p.session.D.meta.sessId && r.trackId === p.session.D.meta.trackId)); });
    return () => { alive = false; };
  }, [p]);
  useEffect(() => {
    let alive = true;
    setData(null);
    if (id) void runStore.get(id).then(d => {
      if (!alive) return;
      if (d && sameRoute(p.session.D, d)) setData(rebuildRun(d));
      else p.toast("This run uses a different route. Reimport both files to match their layouts.", "err");
    });
    return () => { alive = false; };
  }, [id, p]);
  return <div className="external-comparison">
    <label>Compare another file
      <select aria-label="Comparison run" value={id} onChange={e => setId(e.target.value)}>
        <option value="">Choose a run on this route</option>
        {runs.map(r => <option key={r.id} value={r.id}>{runLabel(r)}</option>)}
      </select>
    </label>
    {data && <label>Ghost lap
      <select aria-label="Comparison lap" value={p.cmp === "external" && p.externalKey.startsWith(id + ":") ? p.externalKey.slice(id.length + 1) : ""}
        onChange={e => { if (e.target.value !== "") p.setExternalGhost(comparisonLap(p.session, data, +e.target.value), id + ":" + e.target.value); }}>
        <option value="">Choose a completed lap</option>
        {data.laps.map((l, i) => l.kind === "lap" && <option key={i} value={i}>{l.name} · {fmt(l.dur)}</option>)}
      </select>
    </label>}
    {!runs.length && <p className="note2">No other runs on this route.</p>}
  </div>;
}
