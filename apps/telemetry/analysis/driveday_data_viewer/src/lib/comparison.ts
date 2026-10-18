import { prepareLap, type Session } from "./session";
import { sectorTimes } from "./sectors";
import type { RunData } from "./types";

/** A stored track ID identifies a shared coordinate frame, direction and start line. */
export function sameRoute(a: RunData, b: RunData): boolean {
  return !!a.meta.trackId && a.meta.trackId === b.meta.trackId && !!a.open === !!b.open &&
    Math.abs(a.RL - b.RL) <= Math.max(2, a.RL * 0.02);
}

export function comparisonLap(session: Session, other: RunData, index: number) {
  if (!sameRoute(session.D, other)) throw new Error("Choose a run on the same route and direction.");
  const raw = other.laps[index];
  if (!raw || raw.kind !== "lap") throw new Error("Choose a completed lap.");
  // Keep percentages identical to opening this driver as the main run.
  const peak = (key: "brk" | "brkr") => other.laps.reduce((m, l) => l.ch[key].reduce((n, v) => Math.max(n, v), m), 1);
  const a = session.D.layoutShift ?? [0, 0];
  const b = other.layoutShift ?? [0, 0];
  const moved = { ...raw, pts: raw.pts.map(q => [q[0] + a[0] - b[0], q[1] + a[1] - b[1], q[2], q[3], q[4]] as typeof q) };
  const lap = prepareLap(moved, peak("brk"), peak("brkr"));
  lap.name = (other.meta.driver || other.meta.name || "Other run") + " · " + raw.name;
  lap.sec = sectorTimes(lap, session.gates);
  return lap;
}
