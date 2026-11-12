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
import { runStore, loadUserState, runLabel } from "@/lib/driveday/storage";
import type { RunData, RunListEntry, UserState } from "@/lib/driveday/types";
import { useFrame, usePlayer, useUi } from "../context";

const signed = (value: number | null, digits = 2) => value == null ? "—" : (value >= 0 ? "+" : "−") + Math.abs(value).toFixed(digits);

export function QualityPanel() {
  const p = usePlayer(); useUi(p);
  return <div className="review-panel"><p className="pdesc">Imported timestamps and recorded GPS availability. Derived speed, playback smoothing and lateral g are estimates.</p>
    {!p.session.D.quality && <p className="review-notice">Reimport this run to distinguish original GPS records from interpolated positions.</p>}
    <div className="scroll"><table className="tb2"><thead><tr><th>Lap</th><th>Samples</th><th>GPS records</th><th>Interpolated</th><th>Largest gap</th></tr></thead><tbody>
      {p.session.TLAPS.map(lap => { const q = qualityForLap(p.session.D, lap); return <tr key={lap.t0} onClick={() => p.setT(lap.t0 - p.T0)}><td>{lap.name}</td><td>{q.samples.hz.toFixed(1)} Hz</td><td>{q.gps ? q.gps.hz.toFixed(1) + " Hz" : "Unknown"}</td><td>{q.interpolated ?? "Unknown"}</td><td>{(Math.max(q.samples.maxGap, q.gps?.maxGap ?? 0) / 1000).toFixed(2)} s</td></tr>; })}
    </tbody></table></div>
    <details><summary>GPS and telemetry gaps</summary>{p.session.TLAPS.flatMap(lap => {
      const quality = qualityForLap(p.session.D, lap);
      return [...quality.samples.gaps.map(gap => ({ ...gap, kind: "Telemetry" })), ...(quality.gps?.gaps ?? []).map(gap => ({ ...gap, kind: "GPS" }))].map(gap => <button className="review-jump" key={lap.t0 + gap.kind + gap.start} onClick={() => p.setT(gap.start - p.T0)}>{lap.name} · {gap.kind} · {clock(gap.start - p.T0)} · {(gap.duration / 1000).toFixed(2)} s</button>);
    })}</details>
    <p className="note2">GPS records can repeat the same position. The export does not provide a hardware fix timestamp, so record rate is not proof of fresh GPS fixes. Gaps exceed three typical intervals, with a threshold clamped between 0.1 and 1.5 seconds.</p>
    <details><summary>Position-change rate (estimate)</summary>{p.session.TLAPS.map(lap => <p key={lap.t0}>{lap.name}: {qualityForLap(p.session.D, lap).updates?.hz.toFixed(1) ?? "unknown"} Hz. Stationary periods and rounding affect this estimate.</p>)}</details>
    <details><summary>Steering signal checks</summary><p className="note2">Changes over 180° between samples less than 1.5 seconds apart may indicate wrapping, decoding or sensor issues. Values are not recalibrated automatically.</p>{p.session.TLAPS.map(lap => { const q = steeringQuality(lap); return <p key={lap.t0}>{lap.name}: {q.jumps} large changes across {q.pairs} adjacent sample pairs.</p>; })}</details>
  </div>;
}

export function ValidityControls() {
  const p = usePlayer(); useUi(p);
  const rules = p.user.validity ?? { excludeCones: false, excludeOffCourse: true, excludeIncomplete: true };
  const options = [["excludeCones", "Exclude cone-hit laps"], ["excludeOffCourse", "Exclude marked off-course laps"], ["excludeIncomplete", "Exclude marked incomplete laps"]] as const;
  return <div className="review-panel"><p className="note2">Incomplete logger laps never count. Mark statuses in the lap table; override eligibility per completed lap below.</p>
    {options.map(([key, label]) => <label className="review-check" key={key}><input type="checkbox" checked={rules[key]} onChange={e => p.update(user => { user.validity = { ...rules, [key]: e.target.checked }; })} />{label}</label>)}
    {!p.runs.some(l => p.analysis.valid(l)) && <p role="status" className="review-notice">No eligible laps. Best times are unavailable until a lap is included.</p>}
    {p.runs.map(lap => { const no = p.session.lapNo(lap), flag = p.user.flags[no] ?? {}; return <div className="review-eligibility" key={no}><span>{lap.name}</span><select aria-label={"Eligibility for " + lap.name} value={flag.include == null ? "auto" : flag.include ? "include" : "exclude"} onChange={e => p.update(user => { const next = { ...(user.flags[no] ?? {}) }; if (e.target.value === "auto") delete next.include; else { next.include = e.target.value === "include"; if (next.include) delete next.x; } user.flags[no] = next; })}><option value="auto">Use rules</option><option value="include">Include in bests</option><option value="exclude">Exclude from bests</option></select><small>{p.analysis.valid(lap) ? "Included" : "Excluded"}</small></div>; })}
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
  return <div className="review-panel"><p className="pdesc">{L.name}{R ? " vs " + R.name : ". Choose a different ghost to compare."} · select a section to inspect braking and acceleration.</p>
    <svg className="review-chart" viewBox="0 0 620 150" role="img" aria-label="Speed comparison by distance"><polyline points={curves.lap} stroke="var(--blue)" fill="none" strokeWidth="2" />{curves.ghost && <polyline points={curves.ghost} stroke="var(--purple)" fill="none" strokeWidth="2" />}</svg>
    <small className="note2">Speed (mph) by distance · blue: watched lap · purple: ghost</small>
    <div className="scroll"><table className="tb2"><thead><tr><th>Section</th><th>This lap</th><th>Lost / gained</th></tr></thead><tbody>{segments.map((section, i) => { const review = segmentReview(L, R, section, p.RL); return <tr key={section.name} className={i === selected ? "on" : ""} tabIndex={0} onClick={() => setSelected(i)} onKeyDown={e => { if (e.key === "Enter") setSelected(i); }}><td>{section.name}</td><td>{fmt(review.time)}</td><td className={review.delta == null ? "" : review.delta > 0 ? "pos" : "neg"}>{signed(review.delta == null ? null : review.delta / 1000, 3)} s</td></tr>; })}</tbody></table></div>
    {result && <><h4>{segment.name} · driving events</h4><div className="scroll"><table className="tb2"><thead><tr><th>Event</th><th>Watched lap</th><th>Ghost</th><th>Difference</th></tr></thead><tbody>{([ ["Brake starts", result.brake, comparison?.brake], ["Minimum speed", result.apex, comparison?.apex], ["Throttle ≥50%", result.throttle, comparison?.throttle] ] as const).map(([name, event, reference]) => <tr key={name}><td>{name}</td><td><button className="review-link" disabled={!event} onClick={() => jump(event?.time)}>{event ? event.distance.toFixed(1) + " m" : "—"}</button></td><td>{reference ? reference.distance.toFixed(1) + " m" : "—"}</td><td>{signed(event && reference ? event.distance - reference.distance : null)} m</td></tr>)}</tbody></table></div><label className="review-check"><input type="checkbox" checked={markers} onChange={e => setMarkers(e.target.checked)} /> Show event markers on track</label><button className="btn" onClick={() => jump(result.brake?.time ?? result.apex?.time)}>Jump to this section / video</button></>}
    <p className="note2">Red delta means time lost to the ghost; green means gained. Brake onset uses 8% of peak pressure within 40 m before the section. Events require recorded samples; no event is invented when a crossing is missing.</p>
  </div>;
}

export function SetupComparePanel() {
  const p = usePlayer(); useUi(p);
  const [runs, setRuns] = useState<RunListEntry[]>([]), [id, setId] = useState("");
  const [other, setOther] = useState<{ data: RunData; user: UserState; best: number } | null>(null);
  useEffect(() => { let alive = true; void runStore.list().then(list => { if (alive) setRuns(list.filter(r => r.id !== p.session.D.meta.sessId && r.trackId === p.session.D.meta.trackId)); }); return () => { alive = false; }; }, [p]);
  useEffect(() => { let alive = true; setOther(null); if (id) void runStore.get(id).then(data => { if (!alive || !data) return; if (!sameRoute(p.session.D, data)) { p.toast("Choose the same route and direction.", "err"); return; } const user = loadUserState(data.meta.sessId, data); const session = new Session(rebuildRun(data)); setOther({ data, user, best: new Analysis(session, user).bestLap }); }); return () => { alive = false; }; }, [id, p]);
  const keys = [...new Set([...Object.keys(p.user.setup), ...Object.keys(other?.user.setup ?? {})])];
  return <div className="review-panel"><label>Compare setup with<select aria-label="Setup comparison run" value={id} onChange={e => setId(e.target.value)}><option value="">Choose a run on this route</option>{runs.map(run => <option key={run.id} value={run.id}>{runLabel(run)}</option>)}</select></label>{other && <><p>Best eligible: {fmt(p.analysis.bestLap)} vs {fmt(other.best)}</p><div className="scroll"><table className="tb2"><thead><tr><th>Setting</th><th>This run</th><th>Other run</th><th>Change</th></tr></thead><tbody>{keys.map(key => { const a = p.user.setup[key], b = other.user.setup[key]; const numeric = numericSetupKeys.has(key) && a != null && b != null && a !== "" && b !== "" && Number.isFinite(+a) && Number.isFinite(+b); return <tr key={key} className={a !== b ? "on" : ""}><td>{setupLabels[key] ?? key}</td><td>{a ?? "—"}</td><td>{b ?? "—"}</td><td>{numeric ? signed(+a! - +b!) : a === b ? "Same" : "Changed"}</td></tr>; })}</tbody></table></div><p className="note2">Tyre pressures, alignment, suspension and conditions come from each run&apos;s Setup panel. Differences show association, not proof that a setup change caused a lap-time change.</p></>}{!keys.length && <p className="empty">Add setup and conditions to each run under Setup first.</p>}</div>;
}

export function ReportPanel() {
  const p = usePlayer(); useUi(p);
  const [selected, setSelected] = useState(() => p.runs.filter(l => p.analysis.valid(l)).map(l => p.laps.indexOf(l)));
  return <div className="review-panel"><p className="pdesc">Export a self-contained debrief with selected laps, speed comparison charts, section deltas, setup and bookmarks. Open it offline or print it to PDF.</p>
    {p.runs.map(lap => { const i = p.laps.indexOf(lap); return <label className="review-check" key={i}><input type="checkbox" checked={selected.includes(i)} onChange={e => setSelected(current => e.target.checked ? [...current, i] : current.filter(index => index !== i))} />{lap.name} · {fmt(lap.dur)}</label>; })}
    <button className="btn primary" disabled={!selected.length} onClick={() => { try { const report = debriefReport(p, selected); if (!saveFile("lapdog-debrief.html", report, "text/html")) p.toast("Could not download this report.", "err"); } catch (error) { p.toast((error as Error).message, "err"); } }}>Download debrief</button>
  </div>;
}
