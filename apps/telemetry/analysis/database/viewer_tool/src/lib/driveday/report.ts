import { buildDebrief } from "./debrief";
import { fmt } from "./format";
import { curveSvg, segmentReview } from "./review";
import type { Player } from "./player";
import { setupLabels } from "./setup";

export const escapeHtml = (value: unknown) => String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]!);

/** A portable snapshot with inline SVG and no scripts, network resources or raw video. */
export function debriefReport(p: Player, selected: number[]): string {
  const e = escapeHtml, ghost = p.refLap();
  const laps = selected.map(i => p.laps[i]).filter(l => l?.kind === "lap");
  if (!laps.length) throw new Error("Select at least one completed lap.");
  const cards = laps.map(lap => {
    const curves = curveSvg(lap, ghost === lap ? null : ghost, p.RL);
    const rows = p.session.segs.map(segment => {
      const result = segmentReview(lap, ghost === lap ? null : ghost, segment, p.RL);
      return `<tr><td>${e(segment.name)}</td><td>${e(fmt(result.time))}</td><td>${result.delta == null ? "—" : e((result.delta >= 0 ? "+" : "−") + (Math.abs(result.delta) / 1000).toFixed(3))}</td></tr>`;
    }).join("");
    return `<section><h2>${e(lap.name)}</h2><p>Logged: ${e(fmt(lap.dur))} · Adjusted: ${e(fmt(p.analysis.adj(lap)))} · Cones: ${p.analysis.conesOf(lap)} · ${p.analysis.valid(lap) ? "Included in bests" : "Excluded from bests"}</p>
    <p>Sectors: ${lap.sec.map(v => e(fmt(v))).join(" / ")}</p>
    <svg viewBox="0 0 620 150" role="img" aria-label="Speed by distance"><rect width="620" height="150" fill="#edf2f6"/><polyline points="${curves.lap}" fill="none" stroke="#176cd4" stroke-width="2"/>${curves.ghost ? `<polyline points="${curves.ghost}" fill="none" stroke="#b14bc6" stroke-width="2"/>` : ""}</svg><p class="small">Speed by distance (mph). Blue: this lap${curves.ghost ? "; purple: ghost " + e(ghost?.name) : ""}. Missing recorded distances are omitted.</p>
    <table><thead><tr><th>Section</th><th>Time</th><th>vs ghost (s)</th></tr></thead><tbody>${rows}</tbody></table></section>`;
  }).join("");
  const notes = p.user.marks.filter(mark => laps.some(lap => mark.t + p.T0 >= lap.t0 && mark.t + p.T0 < lap.t1));
  return `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${e(p.session.D.meta.name)} — LAPDOG debrief</title><style>body{font:15px/1.6 system-ui,sans-serif;max-width:1000px;margin:32px auto;padding:0 20px;color:#17212b}h1,h2{line-height:1.3}section{margin:24px 0;padding:20px;border:1px solid #dbe1e6;border-radius:10px;break-inside:avoid}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:6px;border-bottom:1px solid #dde3e9}svg{width:100%;max-height:220px}.small{font-size:12px;color:#536274}@media print{body{margin:0}section{border-color:#bbb}}@media(max-width:600px){section{padding:12px}}</style><body>
    <h1>${e(p.session.D.meta.name)}</h1><p>${e(p.session.D.meta.driver)} · ${e(p.session.D.meta.venue)} · ${e(p.session.D.meta.event)}</p><p>Route ${p.RL.toFixed(0)} m · Best eligible lap ${e(fmt(p.analysis.bestLap))} · Generated ${e(new Date().toLocaleString())}</p>
    <section><h2>Run debrief</h2><ul>${buildDebrief(p.analysis, p.units).map(item => `<li>${e(item.t)}</li>`).join("")}</ul></section>${cards}
    <section><h2>Setup</h2><table>${Object.entries(p.user.setup).map(([key, value]) => `<tr><th>${e(setupLabels[key] ?? key)}</th><td>${e(value)}</td></tr>`).join("")}</table></section>
    <section><h2>Notes and bookmarks</h2><ul>${notes.map(mark => `<li>${e(mark.category ?? "note")} · ${(mark.t / 1000).toFixed(1)} s: ${e(mark.text)}${mark.video ? " · Video " + e(mark.video.name) + " @ " + mark.video.time.toFixed(2) + " s" : ""}</li>`).join("") || "<li>No bookmarks for these laps.</li>"}</ul></section>
    <p class="small">Logged lap times are retained. Speed and lateral acceleration are GPS estimates. Section boundaries are analysis boundaries. This report contains selected analysis and notes, not the video or raw session export.</p></body></html>`;
}
