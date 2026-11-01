import { interp } from "./math";
import { speedEnvelope } from "./timeline-window";
import type { Lap } from "./types";

export const PEDAL_WIDTH = 640;
export const PEDAL_HEIGHT = 160;

export function activeDriverSector(lap: Lap, elapsed: number): number | null {
  if (lap.kind !== "lap" && lap.kind !== "partial") return null;
  let end = 0;
  for (let sector = 0; sector < 3; sector++) {
    const duration = lap.sec[sector];
    if (duration == null || elapsed + 0.001 < end + duration) return sector;
    end += duration;
  }
  return 2;
}

export function driverPedalsAt(lap: Lap, distance: number) {
  return { throttle: interp(lap.ch.u, lap.ch.thr, distance), brake: interp(lap.ch.u, lap.ch.brkp, distance) };
}

/** Pixel envelopes retain short pedal peaks; only the drawing is simplified. */
export function pedalPath(lap: Lap, key: "thr" | "brkp", length: number): string {
  if (!(length > 0)) return "";
  const indices = speedEnvelope(lap.ch.u, lap.ch[key], 0, length, PEDAL_WIDTH);
  let path = "", last = -1;
  for (const index of indices) {
    const distance = lap.ch.u[index], value = lap.ch[key][index];
    if (distance < 0 || distance > length || !Number.isFinite(value)) { last = -1; continue; }
    const move = last < 0 || lap.ch.t[index] - lap.ch.t[last] > 1500;
    const x = distance / length * PEDAL_WIDTH;
    const y = PEDAL_HEIGHT * (1 - Math.max(0, Math.min(100, value)) / 100);
    path += `${move ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    last = index;
  }
  return path;
}
