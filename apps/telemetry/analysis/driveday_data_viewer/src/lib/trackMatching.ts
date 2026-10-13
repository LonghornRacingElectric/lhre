import { Geo } from "./geometry";
import type { Track, XY } from "./types";

export type Origin = Pick<Track, "lat0" | "lon0">;
const KY = 6378137 * Math.PI / 180;
const kx = (lat: number) => KY * Math.cos(lat * Math.PI / 180);
const distance = (a: XY, b: XY) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const median = (values: number[]) => {
  const a = [...values].sort((x, y) => x - y);
  const i = Math.floor(a.length / 2);
  return a.length % 2 ? a[i] : (a[i - 1] + a[i]) / 2;
};

/** GPS coordinates stay fixed when a run adopts another layout's local map origin. */
export function reproject(p: XY, from: Origin, to: Origin): XY {
  return [(p[0] / kx(from.lat0) + from.lon0 - to.lon0) * kx(to.lat0), p[1] + (from.lat0 - to.lat0) * KY];
}

/** Both paths must cover the same ground, at the same size, in the same direction. */
export function matchTrack(laps: XY[][], open: boolean, origin: Origin, tracks: Track[]): Track | undefined {
  let best: Track | undefined;
  let bestScore = 0;
  for (const track of tracks) {
    if (!!track.open !== open || track.RL <= 0 || track.ref.length < 2) continue;
    const paths = laps.filter(p => p.length >= 2).map(p => p.map(q => reproject(q, origin, track)));
    if (!paths.length) continue;
    const geo = new Geo(track.ref, track.cum, track.RL, open);
    if (open && paths.some(p => distance(p[0], track.ref[0]) > 6 || distance(p[p.length - 1], track.ref[track.ref.length - 1]) > 6)) continue;
    const samples = paths.flat().filter((_, i, all) => i % Math.max(1, Math.floor(all.length / 300)) === 0);
    const near = samples.filter(p => geo.nearest(...p).d <= 6).length / samples.length;
    if (near < 0.9) continue;
    const lengths = paths.map(p => p.slice(1).reduce((s, q, i) => s + distance(q, p[i]), 0));
    if (Math.abs(median(lengths) / track.RL - 1) > 0.12) continue;
    const lapGeos = paths.map((p, i) => new Geo(p, cumulative(p), lengths[i], true));
    const n = Math.max(2, Math.round(track.RL / 3));
    let covered = 0;
    for (let i = 0; i <= n; i++) {
      const p = geo.at(track.RL * i / n);
      if (lapGeos.some(l => l.nearest(p[0], p[1]).d <= 6)) covered++;
    }
    const coverage = covered / (n + 1);
    if (coverage < 0.9) continue;
    const progress = paths.map(p => {
      const positions = p.map(q => geo.nearest(...q).s);
      return positions.slice(1).reduce((sum, s, i) => {
        let ds = s - positions[i];
        if (!open) ds -= track.RL * Math.round(ds / track.RL);
        return sum + ds;
      }, 0);
    });
    // A median alone could accept a group containing reverse laps.
    if (progress.some(s => s <= track.RL * 0.4)) continue;
    const score = near * coverage + (track.edges ? 1 : 0);
    if (score > bestScore) { best = track; bestScore = score; }
  }
  return best;
}

export function cumulative(points: XY[]): number[] {
  const cum = [0];
  for (let i = 1; i < points.length; i++) cum.push(cum[i - 1] + distance(points[i], points[i - 1]));
  return cum;
}
