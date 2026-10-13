import { Geo } from "./geometry";
import { sectorsOf } from "./rebuild";
import { cumulative, matchTrack, reproject } from "./trackMatching";
import type { Pt, RunData, Track, XY } from "./types";

/** Upgrade saved GPS geometry without reimporting or changing telemetry, lap times, or note IDs. */
export function assignStoredLayout(data: RunData, previous: Track, known: Track[]): { data: RunData; track: Track } {
  const origin = data.origin ?? previous;
  const paths = data.laps.filter(l => l.kind === "lap").map(l => l.pts.map(p => [p[0], p[1]] as XY));
  let track = matchTrack(paths, !!data.open, origin, known);
  if (!track) {
    track = matchTrack(paths, !!data.open, origin, [previous]);
    if (!track) {
      // Older imports could have used a reference that ran the other way.
      const ref = paths[0];
      if (!ref || ref.length < 2) return { data, track: previous };
      const line = data.open ? [...ref] : [...ref, ref[0]];
      const cum = cumulative(line);
      track = { ...previous, id: previous.id + "-direction-" + data.meta.sessId, name: previous.name + " · alternate direction", ...origin, ref: line, cum, RL: cum[cum.length - 1], edges: undefined };
    }
  }
  const geo = new Geo(track.ref, track.cum, track.RL, !!track.open);
  const laps = data.laps.map(lap => {
    const u: number[] = [];
    const pts: Pt[] = lap.pts.map((p, i) => {
      const xy = reproject([p[0], p[1]], origin, track);
      const near = geo.nearest(...xy);
      const s = near.s;
      const along = track.open ? s : i === 0 ? (s > track.RL / 2 ? s - track.RL : s) : s + track.RL * Math.round((u[i - 1] - s) / track.RL);
      u.push(i ? Math.max(u[i - 1], along) : along);
      return [xy[0], xy[1], p[2], s, near.d];
    });
    return { ...lap, pts, ch: { ...lap.ch, u }, dev: pts.reduce((n, p) => n + p[4], 0) / pts.length,
      sec: sectorsOf(u, lap.ch.t, track.RL, lap.dur, lap.kind === "lap") };
  });
  return { track, data: { ...data, ref: track.ref, cum: track.cum, RL: track.RL, edges: track.edges,
    origin: { lat0: track.lat0, lon0: track.lon0 }, layoutVersion: 3, laps,
    meta: { ...data.meta, trackId: track.id, trackName: track.name } } };
}
