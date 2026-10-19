import type { LiveSample, SessionExport } from "./types";

/** Longer gaps cannot establish a driven route; never draw a chord through them. */
export const MAX_GPS_GAP_MS = 2000;
export const hasGps = (s: LiveSample): boolean => !!s && Number.isFinite(s.lat) && Number.isFinite(s.lon) &&
  Math.abs(s.lat) <= 90 && Math.abs(s.lon) <= 180 && (s.lat !== 0 || s.lon !== 0);

export function recordingSamples(json: SessionExport): LiveSample[] {
  const byTime = new Map<number, LiveSample>();
  for (const s of [...json.laps.flatMap(l => Array.isArray(l.samples) ? l.samples : []),
    ...(json.currentLap?.lapSamples ?? []), ...json.sampleTail]) {
    if (!s || !Number.isFinite(s.t)) continue;
    const old = byTime.get(s.t);
    byTime.set(s.t, old ? { ...old, ...s, values: { ...old.values, ...s.values },
      lat: hasGps(s) ? s.lat : old.lat, lon: hasGps(s) ? s.lon : old.lon,
      cellTemps: s.cellTemps?.length ? s.cellTemps : old.cellTemps } : s);
  }
  return [...byTime.values()].sort((a, b) => a.t - b.t);
}

export function gpsProblem(samples: LiveSample[], start: number, end: number): string | null {
  const fixes = samples.filter(hasGps);
  if (fixes.length < 12) return "Missing GPS: fewer than 12 valid fixes.";
  if (new Set(fixes.map(s => `${s.lat},${s.lon}`)).size < 12) return "Frozen or insufficient GPS: fewer than 12 distinct positions.";
  const ky = 6378137 * Math.PI / 180, kx = ky * Math.cos(fixes[0].lat * Math.PI / 180);
  let distance = 0, maxGap = Math.max(fixes[0].t - start, end - fixes[fixes.length - 1].t), unchangedAt = fixes[0].t;
  for (let i = 1; i < fixes.length; i++) {
    const a = fixes[i - 1], b = fixes[i], dt = b.t - a.t;
    const step = Math.hypot((b.lat - a.lat) * ky, (b.lon - a.lon) * kx);
    maxGap = Math.max(maxGap, dt);
    distance += step;
    if (dt > 0 && step / (dt / 1000) > 80) return "GPS jump: implied speed exceeds 80 m/s.";
    if (a.lat !== b.lat || a.lon !== b.lon) unchangedAt = b.t;
    // A held coordinate while the drivetrain is active is not a stationary GPS fix.
    if (b.t - unchangedAt > 5000 && (b.values?.accel_pedal_travel ?? 0) > 0.1 && (b.values?.motor_speed ?? 0) > 1000)
      return "Frozen GPS while throttle and motor RPM indicate activity.";
  }
  if (distance < 30) return "Insufficient GPS movement: less than 30 m of recorded route.";
  if (maxGap > MAX_GPS_GAP_MS) return "GPS outage: a gap longer than 2 seconds leaves the route incomplete.";
  return null;
}
