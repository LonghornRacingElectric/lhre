import { interp } from "./math";
import { elapsedAtDistance } from "./sectors";
import { pieces, type Seg } from "./segments";
import type { Lap, RunData } from "./types";

export function sampleQuality(times: number[]) {
  const deltas = times.slice(1).map((t, i) => t - times[i]).filter(t => t > 0).sort((a, b) => a - b);
  const median = deltas.length ? deltas[Math.floor(deltas.length / 2)] : 0;
  const threshold = Math.min(1500, Math.max(100, median * 3));
  const gaps = times.slice(1).flatMap((t, i) => t - times[i] > threshold ? [{ start: times[i], end: t, duration: t - times[i] }] : []);
  return { hz: median ? 1000 / median : 0, maxGap: deltas.at(-1) ?? 0, gaps };
}

export function qualityForLap(D: RunData, lap: Lap) {
  const within = (times: number[]) => times.filter(t => t >= lap.t0 && t <= lap.t1);
  const samples = D.quality ? within(D.quality.sampleTimes) : lap.ch.t.map(t => t + lap.t0);
  return { samples: sampleQuality(samples), gps: D.quality ? sampleQuality(within(D.quality.gpsTimes)) : null,
    updates: D.quality ? sampleQuality(within(D.quality.positionUpdateTimes)) : null,
    interpolated: D.quality ? within(D.quality.interpolatedTimes).length : null };
}

/** Report source jumps without assuming a steering sensor's sign or calibration. */
export function steeringQuality(lap: Lap) {
  let jumps = 0, pairs = 0;
  for (let i = 1; i < lap.ch.t.length; i++) {
    const dt = lap.ch.t[i] - lap.ch.t[i - 1];
    if (dt <= 0 || dt > 1500) continue;
    pairs++;
    if (Math.abs(lap.ch.steer[i] - lap.ch.steer[i - 1]) > 180) jumps++;
  }
  return { jumps, pairs };
}

/** Do not infer split times outside recorded distance coverage. */
export function crossing(lap: Lap, distance: number, length: number): number | null {
  if (distance <= 0) return 0;
  if (distance >= length) return lap.kind === "lap" ? lap.dur : null;
  if (!lap.ch.u.length || distance < lap.ch.u[0] || distance > lap.ch.u.at(-1)!) return null;
  return elapsedAtDistance(lap, distance, length);
}

export function segmentReview(lap: Lap, ghost: Lap | null, segment: Seg, length: number) {
  const ranges = pieces(segment.a, segment.len, length);
  const duration = (L: Lap) => {
    let sum = 0;
    for (const [a, b] of ranges) {
      const start = crossing(L, a, length), end = crossing(L, b, length);
      if (start == null || end == null || end < start) return null;
      sum += end - start;
    }
    return sum;
  };
  const indices = lap.ch.u.flatMap((s, i) => s >= segment.a && s <= Math.min(length, segment.a + segment.len) ? [i] : []);
  const apex = indices.length ? indices.reduce((a, b) => lap.ch.spd[b] < lap.ch.spd[a] ? b : a) : null;
  const endDistance = segment.a + segment.len;
  const brakeStart = lap.ch.u.findIndex((s, i) => s >= Math.max(0, segment.a - 40) && s <= Math.min(length, endDistance) && lap.ch.brkp[i] > 8 && (i === 0 || lap.ch.brkp[i - 1] <= 8));
  const throttle = apex == null ? -1 : lap.ch.u.findIndex((s, i) => i >= apex && s <= Math.min(length, endDistance + 30) && lap.ch.thr[i] >= 50);
  const a = duration(lap), b = ghost ? duration(ghost) : null;
  return { time: a, delta: a == null || b == null ? null : a - b,
    brake: brakeStart < 0 ? null : { distance: lap.ch.u[brakeStart], time: lap.ch.t[brakeStart] },
    apex: apex == null ? null : { distance: lap.ch.u[apex], time: lap.ch.t[apex], speed: lap.ch.spd[apex] },
    throttle: throttle < 0 ? null : { distance: lap.ch.u[throttle], time: lap.ch.t[throttle] } };
}

export function curveSvg(lap: Lap, ghost: Lap | null, length: number) {
  const points = (L: Lap) => Array.from({ length: 101 }, (_, i) => {
    const d = length * i / 100;
    return { x: 10 + i * 6, distance: d, speed: interp(L.ch.u, L.ch.spd, d) };
  }).filter(point => point.distance >= L.ch.u[0] && point.distance <= L.ch.u.at(-1)!);
  const all = [...lap.ch.spd, ...(ghost?.ch.spd ?? [])].filter(Number.isFinite);
  const max = Math.max(1, ...all);
  const path = (L: Lap) => points(L).map(p => `${p.x.toFixed(1)},${(130 - p.speed / max * 110).toFixed(1)}`).join(" ");
  return { lap: path(lap), ghost: ghost ? path(ghost) : null };
}
