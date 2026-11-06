"use client";

import { useEffect, useState } from "react";
import { qualityForLap, segmentReview, curveSvg, steeringQuality } from "@/lib/driveday/review";
import { debriefReport } from "@/lib/driveday/report";
import { saveFile } from "@/lib/driveday/download";
import { fmt, clock } from "@/lib/driveday/format";
import { posAt } from "@/lib/driveday/geometry";
import { sameRoute } from "@/lib/driveday/comparison";
import { Analysis } from "@/lib/driveday/analysis";
import { setupLabels, numericSetupKeys } from "@/lib/driveday/setup";
import { Session } from "@/lib/driveday/session";
import { rebuildRun } from "@/lib/driveday/rebuild";
import { runStore, loadUserState } from "@/lib/driveday/storage";
import type { RunData, RunListEntry, UserState } from "@/lib/driveday/types";
import { useFrame, usePlayer, useUi } from "../context";

const signed = (value: number | null, digits = 2) => value == null ? "—" : (value >= 0 ? "+" : "−") + Math.abs(value).toFixed(digits);

export function QualityPanel() {
  const p = usePlayer(); useUi(p);
  return <div className="review-panel">
    {!p.session.D.quality && <p className="review-notice">Source detail unavailable. Reimport to refresh.</p>}
    <div className="scroll"><table className="tb2"><thead><tr><th>Lap</th><th>Samples</th><th>GPS records</th><th>Interpolated</th><th>Largest gap</th></tr></thead><tbody>
      {p.session.TLAPS.map(lap => { const q = qualityForLap(p.session.D, lap); return <tr key={lap.t0} onClick={() => p.setT(lap.t0 - p.T0)}><td>{lap.name}</td><td>{q.samples.hz.toFixed(1)} Hz</td><td>{q.gps ? q.gps.hz.toFixed(1) + " Hz" : "Unknown"}</td><td>{q.interpolated ?? "Unknown"}</td><td>{(Math.max(q.samples.maxGap, q.gps?.maxGap ?? 0) / 1000).toFixed(2)} s</td></tr>; })}
    </tbody></table></div>
    <details><summary>GPS and telemetry gaps</summary>{p.session.TLAPS.flatMap(lap => {
      const quality = qualityForLap(p.session.D, lap);
      return [...quality.samples.gaps.map(gap => ({ ...gap, kind: "Telemetry" })), ...(quality.gps?.gaps ?? []).map(gap => ({ ...gap, kind: "GPS" }))].map(gap => <button className="review-jump" key={lap.t0 + gap.kind + gap.start} onClick={() => p.setT(gap.start - p.T0)}>{lap.name} · {gap.kind} · {clock(gap.start - p.T0)} · {(gap.duration / 1000).toFixed(2)} s</button>);
    })}</details>

    <details><summary>Position-change rate (estimate)</summary>{p.session.TLAPS.map(lap => <p key={lap.t0}>{lap.name}: {qualityForLap(p.session.D, lap).updates?.hz.toFixed(1) ?? "unknown"} Hz (estimated).</p>)}</details>
    <details><summary>Steering signal checks</summary>{p.session.TLAPS.map(lap => { const q = steeringQuality(lap); return <p key={lap.t0}>{lap.name}: {q.jumps} jumps / {q.pairs} samples.</p>; })}</details>
  </div>;
}

export function DrivingReviewPanel() {
  const p = usePlayer(); useUi(p); useFrame(p);
  const { L } = p.curState(), ghost = p.refLap(), R = ghost === L ? null : ghost;
  const [selected, setSelected] = useState(0);
  const [markers, setMarkers] = useState(false);
  const segments = p.session.segs, segment = segments[selected];
  const result = segment ? segmentReview(L, R, segment, p.RL) : null;
  const comparison = segment && R ? segmentReview(R, null, segment, p.RL) : null;
  useEffect(() => {
    if (!markers || !segment) { p.reviewMarkers = []; p.schedule(); return; }
    const a = segmentReview(L, null, segment, p.RL), b = R ? segmentReview(R, null, segment, p.RL) : null;
    const points = (lap: typeof L, review: typeof a, suffix: string) => ([ [review.brake, "Brake", "#ff5964"], [review.apex, "Min speed", "#58a9ff"], [review.throttle, "Throttle", "#42e59b"] ] as const).flatMap(([event, name, color]) => event ? [{ point: posAt(lap, event.time), label: name + suffix, color }] : []);
    p.reviewMarkers = [...points(L, a, ""), ...(R && b ? points(R, b, " · ghost") : [])]; p.schedule(true);
    return () => { p.reviewMarkers = []; p.schedule(true); };
  }, [p, L, R, segment, markers]);
  const curves = curveSvg(L, R, p.RL);
  const jump = (time: number | null | undefined) => { if (time != null) { p.play(false); p.setT(L.t0 - p.T0 + time); } };
  return <div className="review-panel"><div className="analysis-status">{L.name}{R ? " vs " + R.name : " · no comparison"}</div>
    <svg className="review-chart" viewBox="0 0 620 150" role="img" aria-label="Speed comparison by distance"><polyline points={curves.lap} stroke="var(--blue)" fill="none" strokeWidth="2" />{curves.ghost && <polyline points={curves.ghost} stroke="var(--purple)" fill="none" strokeWidth="2" />}</svg>
    <small className="note2">Speed (mph) by distance · blue: watched lap · purple: ghost</small>
    <div className="scroll"><table className="tb2"><thead><tr><th>Section</th><th>This lap</th><th>Lost / gained</th></tr></thead><tbody>{segments.map((section, i) => { const review = segmentReview(L, R, section, p.RL); return <tr key={section.name} className={i === selected ? "on" : ""} tabIndex={0} onClick={() => setSelected(i)} onKeyDown={e => { if (e.key === "Enter") setSelected(i); }}><td>{section.name}</td><td>{fmt(review.time)}</td><td className={review.delta == null ? "" : review.delta > 0 ? "pos" : "neg"}>{signed(review.delta == null ? null : review.delta / 1000, 3)} s</td></tr>; })}</tbody></table></div>
    {result && <><h4>{segment.name} · driving events</h4><div className="scroll"><table className="tb2"><thead><tr><th>Event</th><th>Watched lap</th><th>Ghost</th><th>Difference</th></tr></thead><tbody>{([ ["Brake starts", result.brake, comparison?.brake], ["Minimum speed", result.apex, comparison?.apex], ["Throttle ≥50%", result.throttle, comparison?.throttle] ] as const).map(([name, event, reference]) => <tr key={name}><td>{name}</td><td><button className="review-link" disabled={!event} onClick={() => jump(event?.time)}>{event ? event.distance.toFixed(1) + " m" : "—"}</button></td><td>{reference ? reference.distance.toFixed(1) + " m" : "—"}</td><td>{signed(event && reference ? event.distance - reference.distance : null)} m</td></tr>)}</tbody></table></div><label className="review-check"><input type="checkbox" checked={markers} onChange={e => setMarkers(e.target.checked)} /> Show event markers on track</label><button className="btn" onClick={() => jump(result.brake?.time ?? result.apex?.time)}>Jump to section</button></>}

  </div>;
}

export function SetupComparePanel() {
  const p = usePlayer(); useUi(p);
  const [runs, setRuns] = useState<RunListEntry[]>([]), [id, setId] = useState("");
  const [other, setOther] = useState<{ data: RunData; user: UserState; best: number } | null>(null);
  useEffect(() => { let alive = true; void runStore.list().then(list => { if (alive) setRuns(list.filter(r => r.id !== p.session.D.meta.sessId && r.trackId === p.session.D.meta.trackId)); }); return () => { alive = false; }; }, [p]);
  useEffect(() => { let alive = true; setOther(null); if (id) void runStore.get(id).then(data => { if (!alive || !data) return; if (!sameRoute(p.session.D, data)) { p.toast("Choose the same route and direction.", "err"); return; } const user = loadUserState(data.meta.sessId, data); const session = new Session(rebuildRun(data)); setOther({ data, user, best: new Analysis(session, user).bestLap }); }); return () => { alive = false; }; }, [id, p]);
  const keys = [...new Set([...Object.keys(p.user.setup), ...Object.keys(other?.user.setup ?? {})])];
  return <div className="review-panel"><label>Compare setup with<select aria-label="Setup comparison run" value={id} onChange={e => setId(e.target.value)}><option value="">Choose a run on this route</option>{runs.map(run => <option key={run.id} value={run.id}>{run.name || "Untitled run"}</option>)}</select></label>{other && <><p>Best eligible: {fmt(p.analysis.bestLap)} vs {fmt(other.best)}</p><div className="scroll"><table className="tb2"><thead><tr><th>Setting</th><th>This run</th><th>Other run</th><th>Change</th></tr></thead><tbody>{keys.map(key => { const a = p.user.setup[key], b = other.user.setup[key]; const numeric = numericSetupKeys.has(key) && a != null && b != null && a !== "" && b !== "" && Number.isFinite(+a) && Number.isFinite(+b); return <tr key={key} className={a !== b ? "on" : ""}><td>{setupLabels[key] ?? key}</td><td>{a ?? "—"}</td><td>{b ?? "—"}</td><td>{numeric ? signed(+a! - +b!) : a === b ? "Same" : "Changed"}</td></tr>; })}</tbody></table></div></>}{!keys.length && <p className="empty">No setup recorded.</p>}</div>;
}

export function ReportPanel() {
  const p = usePlayer(); useUi(p);
  const [selected, setSelected] = useState(() => p.runs.filter(l => p.analysis.valid(l)).map(l => p.laps.indexOf(l)));
  return <div className="review-panel">
    {p.runs.map(lap => { const i = p.laps.indexOf(lap); return <label className="review-check" key={i}><input type="checkbox" checked={selected.includes(i)} onChange={e => setSelected(current => e.target.checked ? [...current, i] : current.filter(index => index !== i))} />{lap.name} · {fmt(lap.dur)}</label>; })}
    <button className="btn primary" disabled={!selected.length} onClick={() => { try { const report = debriefReport(p, selected); if (!saveFile("lapdog-debrief.html", report, "text/html")) p.toast("Could not download this report.", "err"); } catch (error) { p.toast((error as Error).message, "err"); } }}>Download debrief</button>
  </div>;
}
