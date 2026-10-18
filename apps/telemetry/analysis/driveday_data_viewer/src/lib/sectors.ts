import { interp } from "./math";
import type { Seg } from "./segments";
import type { Lap } from "./types";

export type SectorGates = [number, number];

export function validGates(g: number[], length: number): g is SectorGates {
  return g.length === 2 && g.every(Number.isFinite) && g[0] > 0 && g[1] > g[0] && g[1] < length;
}

/** Prefer corner/straight transitions near balanced driving-time blocks, fixed for every lap. */
export function suggestGates(lap: Lap, segments: Seg[], length: number): SectorGates {
  const targets = [1 / 3, 2 / 3].map((f) => interp(lap.ch.t, lap.ch.u, lap.dur * f));
  const candidates = segments.filter((s) => s.type === "s" && s.len >= Math.min(12, length * 0.05))
    .flatMap((s) => [s.a, (s.a + s.len) % length])
    .filter((d) => d > length * 0.1 && d < length * 0.9);
  const gates = targets.map((target, k) => {
    const usable = candidates.filter((d) => Math.abs(interp(lap.ch.u, lap.ch.t, d) / lap.dur - (k + 1) / 3) <= 0.12);
    return usable.length ? usable.reduce((a, b) => Math.abs(b - target) < Math.abs(a - target) ? b : a) : target;
  });
  return validGates(gates, length) ? gates : [length / 3, 2 * length / 3];
}

/** Crossing time from actual fixes; missing gates stay missing rather than becoming invented equal times. */
export function sectorTimes(lap: { ch: Pick<Lap["ch"], "u" | "t">; dur: number; kind: Lap["kind"] }, gates: SectorGates): (number | null)[] {
  return timesAtGates(lap.ch.u, lap.ch.t, lap.dur, lap.kind !== "partial", gates);
}

/** Distance comparison uses logged start/finish anchors, not each lap's GPS zero crossing. */
export function elapsedAtDistance(lap: Lap, distance: number, length: number): number {
  if (distance <= 0) return 0;
  if (distance >= length) return lap.dur;
  return Math.max(0, Math.min(lap.dur, interp(lap.ch.u, lap.ch.t, distance)));
}

/** Fixed distance gates shared by import and playback; missing crossings stay unavailable. */
export function sectorsOf(u: number[], tt: number[], RL: number, dur: number, complete: boolean, gates = [RL / 3, 2 * RL / 3]): (number | null)[] {
  return timesAtGates(u, tt, dur, complete, gates);
}

function timesAtGates(u: number[], tt: number[], dur: number, complete: boolean, gates: number[]): (number | null)[] {
  const cross = (th: number): number | null => {
    if (u[0] === th && tt[0] >= 0 && tt[0] <= dur) return tt[0];
    for (let k = 1; k < u.length; k++) {
      if (u[k - 1] < th && u[k] >= th) {
        const t = tt[k - 1] + (tt[k] - tt[k - 1]) * (th - u[k - 1]) / (u[k] - u[k - 1]);
        return t >= 0 && t <= dur ? t : null;
      }
    }
    return null;
  };
  const c1 = cross(gates[0]);
  const c2 = cross(gates[1]);
  return [c1, c1 != null && c2 != null && c2 >= c1 ? c2 - c1 : null, complete && c2 != null ? dur - c2 : null];
}
