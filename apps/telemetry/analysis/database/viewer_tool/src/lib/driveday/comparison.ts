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
  // Use the active run's pressure scale and gates so the overlays mean the same thing.
  const lap = prepareLap(raw, session.brkMax, session.brkrMax);
  lap.name = (other.meta.driver || other.meta.name || "Other run") + " · " + raw.name;
  lap.sec = sectorTimes(lap, session.gates);
  return lap;
}
