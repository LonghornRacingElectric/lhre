import { gpsProblem, recordingSamples } from "./gps-quality";
import { alignShift, convertSession, type ConvertResult } from "./convert";
import { pathDirection } from "./direction";
import { Geo } from "./geometry";
import { builtinTracks, tracksFromLayout } from "./layouts";
import { sectorsOf } from "./sectors";
import { metaOf, runStore } from "./storage";
import { cumulative, matchTrack, reproject } from "./trackMatching";
import type { Pt, RunData, SessionExport, Track, XY } from "./types";

/** Reserved recording filenames are layout imports; other files require the explicit Import layout action. */
export const isLayoutRecording = (name: string): boolean => /^layout(?:[_ -]session)?\.json$/i.test(name);

/** An intentional centreline recording, never a driver's arbitrary fastest lap. */
export function layoutTracksFromSession(json: SessionExport, fileName: string): Track[] {
  const courses = convertSession(json, fileName, []);
  if (courses.length !== 1 || courses[0].D.open) throw new Error("A layout recording must contain one closed route. Use a full centreline circuit, not a point-to-point run or both directions.");
  const { D, track } = courses[0];
  if (!D.laps.some(l => l.kind === "lap" && l.pts.length >= 30)) throw new Error("The layout recording needs at least 30 GPS fixes in a complete circuit.");
  const direction = pathDirection(D.ref);
  if (direction === "unknown") throw new Error("The layout recording has no clear overall direction. Check that it contains one complete circuit.");
  const n = Math.max(3, Math.round(D.RL));
  const geo = new Geo(D.ref, D.cum, D.RL);
  const ky = 6378137 * Math.PI / 180, kx = ky * Math.cos(track.lat0 * Math.PI / 180);
  const line: [number, number][] = Array.from({ length: n }, (_, i) => {
    const p = geo.at(i * D.RL / n);
    return [Number((track.lat0 + p[1] / ky).toFixed(7)), Number((track.lon0 + p[0] / kx).toFixed(7))];
  });
  return tracksFromLayout({
    name: D.meta.venue || D.meta.name || fileName.replace(/\.json$/i, ""),
    venue: D.meta.venue, driver: D.meta.driver, from: fileName,
    recorded: new Date(Math.min(...D.laps.filter(l => l.kind === "lap").map(l => l.t0))).toISOString(),
    direction, lengthM: D.RL, line,
  }, false);
}

/** Re-express existing GPS in the base layout's coordinate frame; all logged times and channels stay intact. */
export function attachLayout(data: RunData, previous: Track, layouts: Track[]): RunData | null {
  if (data.layoutDetached || data.telemetryOnly) return null;
  const paths = data.laps.filter(l => l.kind === "lap").map(l => l.pts.map(p => [p[0], p[1]] as XY));
  const track = matchTrack(paths, !!data.open, previous, layouts);
  if (!track?.edges) return null;
  const mapped = paths.flatMap(path => path.map(p => reproject(p, previous, track)));
  const probe = mapped.filter((_, i) => i % Math.max(1, Math.floor(mapped.length / 300)) === 0);
  let [dx, dy] = alignShift(track.ref, probe);
  if (Math.hypot(dx, dy) > 6) { dx = 0; dy = 0; }
  const move = (points: XY[]): XY[] => points.map(p => [p[0] + dx, p[1] + dy]);
  const ref = move(track.ref), geo = new Geo(ref, track.cum, track.RL, !!track.open);
  const laps = data.laps.map(lap => {
    const u: number[] = [];
    const pts: Pt[] = lap.pts.map((p, i) => {
      const xy = reproject([p[0], p[1]], previous, track), near = geo.nearest(...xy);
      let distance = near.s;
      if (!track.open) distance = i === 0 ? (distance > track.RL / 2 ? distance - track.RL : distance) : distance + track.RL * Math.round((u[i - 1] - distance) / track.RL);
      u.push(i === 0 ? distance : Math.max(u[i - 1], distance));
      return [xy[0], xy[1], p[2], near.s, near.d];
    });
    return { ...lap, pts, ch: { ...lap.ch, u },
      dev: pts.reduce((sum, p) => sum + p[4], 0) / Math.max(1, pts.length),
      sec: sectorsOf(u, lap.ch.t, track.RL, lap.dur, lap.kind !== "partial"),
    };
  });
  return { ...data, layoutOriginal: data.layoutOriginal ?? (data.edges ? undefined : data), ref, cum: track.cum, RL: track.RL, laps, layoutShift: [dx, dy],
    edges: { ...track.edges, centre: move(track.edges.centre), left: move(track.edges.left), right: move(track.edges.right) },
    meta: { ...data.meta, trackId: track.id, trackName: track.name },
  };
}

/** Persist the generated road and upgrade matching runs, regardless of import order. */
export async function importLayoutRecording(json: SessionExport, fileName: string): Promise<{ tracks: Track[]; updated: string[] }> {
  const layouts = layoutTracksFromSession(json, fileName);
  const previous = [...builtinTracks(), ...await runStore.tracks()];
  for (const track of layouts) await runStore.putTrack(track);
  const updated: string[] = [];
  for (const meta of await runStore.list()) {
    const data = await runStore.get(meta.id);
    if (!data || data.layoutDetached) continue;
    const source = previous.find(t => t.id === data.meta.trackId) ?? layouts.find(t => t.id === data.meta.trackId);
    if (!source) continue;
    const attached = attachLayout(data, source, layouts);
    if (!attached) continue;
    await runStore.put({ ...metaOf(attached), added: meta.added }, attached);
    updated.push(meta.id);
  }
  return { tracks: layouts, updated };
}

/** Driver imports only adopt user-imported roads, while retaining their GPS-derived baseline. */
export function convertDriverSession(json: SessionExport, fileName: string, saved: Track[]) {
  if (!Array.isArray(json?.laps) || !Array.isArray(json?.sampleTail)) throw new Error("This does not look like a session export.");
  const samples = recordingSamples(json);
  const timed = json.laps.filter(l => Number.isFinite(l.startMs) && l.endMs > l.startMs);
  const rejected = timed.flatMap(l => {
    const reason = gpsProblem(samples.filter(s => s.t >= l.startMs && s.t <= l.endMs), l.startMs, l.endMs);
    return reason ? [{ label: l.label || "Logged interval", start: l.startMs, end: l.endMs, reason }] : [];
  });
  const good = timed.filter(l => !rejected.some(r => r.start === l.startMs && r.end === l.endMs));
  const roads = saved.filter(t => t.edges && !t.builtin);
  let baseline: ConvertResult[] = [];
  if (good.length) {
    try {
      baseline = convertSession({ ...json, laps: good }, fileName, saved.filter(t => !t.builtin && !t.edges),
        { start: Math.min(...timed.map(l => l.startMs)), end: Math.max(...timed.map(l => l.endMs)) });
    } catch (error) {
      // Unusable course geometry must not discard otherwise recorded sensor channels.
      rejected.push(...good.map(l => ({ label: l.label || "Logged interval", start: l.startMs, end: l.endMs,
        reason: "GPS analysis unavailable: " + (error as Error).message })));
    }
  }
  rejected.sort((a, b) => a.start - b.start);
  const issues = rejected.map(r => `${r.label}: ${r.reason}`);
  const result: ConvertResult[] = baseline.map(c => ({ ...c, D: { ...(attachLayout(c.D, c.track, roads) ?? c.D), gpsIssues: issues } }));
  if (rejected.length) {
    const name = (json.sessionInfo?.name || fileName.replace(/\.json$/i, "")) + " · Telemetry only";
    const id = (json.sessionInfo?.id || "file-" + fileName + "-" + (json.savedAt ?? "")) + "~telemetry";
    const track: Track = { id: "", name: "GPS unavailable", date: "", event: "", num: 0, venue: json.sessionInfo?.venue || "",
      lat0: 0, lon0: 0, ref: [], cum: [], RL: 0, gates: [], created: 0, from: fileName };
    const D: RunData = { ref: [], cum: [], RL: 0, laps: [], warnings: issues, gpsIssues: issues,
      telemetryOnly: { samples, intervals: rejected }, meta: { sessId: id, name, driver: json.sessionInfo?.driver || json.metadata?.driver || "",
        venue: track.venue, event: json.sessionInfo?.eventType || "", trackId: "", trackName: "GPS unavailable", direction: "unknown",
        cellIdx: [], cutoff: json.soeCutoffCellV || 2.8, planLaps: 0, planKwh: 0 } };
    result.push({ D, track, isNew: false, renamed: false, label: "Telemetry only" });
  }
  if (!result.length) throw new Error("This session has no timed intervals.");
  return result;
}

/** Detach from the analysis reference, not merely hide the road drawing. */
export function detachLayout(data: RunData): RunData {
  if (data.layoutOriginal) return { ...data.layoutOriginal, layoutDetached: true };
  if (!data.edges) return { ...data, layoutDetached: true };
  // Legacy imports did not retain a baseline. Rebuild one from measured GPS in the current frame.
  const laps = data.laps.filter(l => l.kind === "lap" && l.pts.length > 2);
  if (!laps.length) throw new Error("Reimport the original driver JSON to restore its GPS reference.");
  const sorted = laps.map(lap => ({ lap, length: cumulative(lap.pts.map(p => [p[0], p[1]])).at(-1)! })).sort((a, b) => a.length - b.length);
  const points = sorted[Math.floor(sorted.length / 2)].lap.pts.map(p => [p[0], p[1]] as XY);
  const ref = points.map((p, i) => i === 0 || i === points.length - 1 ? p : [0.25 * points[i - 1][0] + 0.5 * p[0] + 0.25 * points[i + 1][0], 0.25 * points[i - 1][1] + 0.5 * p[1] + 0.25 * points[i + 1][1]] as XY);
  if (!data.open) ref.push(ref[0]);
  const cum = cumulative(ref), RL = cum[cum.length - 1], geo = new Geo(ref, cum, RL, !!data.open);
  const rebuilt = data.laps.map(l => {
    const u: number[] = [];
    const pts: Pt[] = l.pts.map((p, i) => {
      const near = geo.nearest(p[0], p[1]);
      let s = near.s;
      if (!data.open) s += RL * Math.round(((i ? u[i - 1] : 0) - s) / RL);
      u.push(i ? Math.max(u[i - 1], s) : s);
      return [p[0], p[1], p[2], near.s, near.d];
    });
    return { ...l, pts, ch: { ...l.ch, u }, dev: pts.reduce((n, p) => n + p[4], 0) / pts.length, sec: sectorsOf(u, l.ch.t, RL, l.dur, l.kind !== "partial") };
  });
  return { ...data, ref, cum, RL, laps: rebuilt, edges: undefined, layoutShift: undefined, layoutDetached: true,
    meta: { ...data.meta, trackId: "gps-" + data.meta.sessId, trackName: "Run GPS reference" },
    warnings: [...data.warnings, "Layout detached: GPS reference rebuilt from this saved run. Reimport the original driver JSON for a fresh conversion."],
  };
}
