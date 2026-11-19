"use client";

import { useEffect, useState } from "react";
import { comparisonLap, sameRoute } from "@/lib/driveday/comparison";
import { fmt } from "@/lib/driveday/format";
import { rebuildRun } from "@/lib/driveday/rebuild";
import { runLabel, runStore } from "@/lib/driveday/storage";
import type { RunData, RunListEntry } from "@/lib/driveday/types";
import { usePlayer } from "../context";
import { Pick } from "../controls";

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
      <Pick ariaLabel="Comparison run" value={id || "none"} onChange={v => setId(v === "none" ? "" : v)} options={[{ value: "none", label: "Choose a run on this route" }, ...runs.map(r => ({ value: r.id, label: runLabel(r) }))]} />
    </label>
    {data && <label>Ghost lap
      <Pick ariaLabel="Comparison lap" value={p.cmp === "external" && p.externalKey.startsWith(id + ":") ? p.externalKey.slice(id.length + 1) : "none"}
        onChange={v => { if (v !== "none") p.setExternalGhost(comparisonLap(p.session, data, +v), id + ":" + v); }}
        options={[{ value: "none", label: "Choose a completed lap" }, ...data.laps.flatMap((l, i) => l.kind === "lap" ? [{ value: String(i), label: l.name + " · " + fmt(l.dur) }] : [])]} />
    </label>}
    {!runs.length && <p className="note2">No other runs on this route.</p>}
  </div>;
}
