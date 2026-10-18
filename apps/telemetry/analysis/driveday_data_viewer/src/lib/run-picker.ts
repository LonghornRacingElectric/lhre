import { roadWidth } from "./geometry";
import { layoutId, layoutName } from "./layouts";
import { directionLabel } from "./direction";
import type { RunListEntry, Track } from "./types";

export type BrowserEntry = Omit<RunListEntry, "laps" | "best">;

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
export const driverOf = (run: BrowserEntry) => run.driver.trim() || "Driver unspecified";

/** The store backfills this geometry-derived field for imports predating the picker. */
export function directionOf(run: BrowserEntry): string {
  return directionLabel(run.direction ?? "unknown");
}

export function locationOf(run: BrowserEntry): string {
  const venue = run.venue.trim();
  if (venue) return venue.replace(/\s+/g, " ");
  // Built-in layouts carry a location name even if the export has no venue.
  if (run.trackId.startsWith("layout-")) return run.track.replace(/\s*·\s*(?:counter[ -]?clockwise|clockwise)\s*$/i, "").trim() || "Location unspecified";
  return "Location unspecified";
}

function dayOf(ts: number): string {
  const d = new Date(ts);
  if (!(ts > 0) || !Number.isFinite(d.getTime())) return "";
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export interface DriveDayGroup<T extends BrowserEntry = BrowserEntry> { id: string; date: string; location: string; label: string; runs: T[] }

/** Derived afresh from saved metadata, so imports need no manual folders or persistent grouping. */
export function driveDayGroups<T extends BrowserEntry>(runs: T[]): DriveDayGroup<T>[] {
  const groups = new Map<string, DriveDayGroup<T>>();
  for (const run of runs) {
    const date = dayOf(run.ts), location = locationOf(run);
    const id = JSON.stringify([date, location.toLocaleLowerCase()]);
    if (!groups.has(id)) {
      const dateLabel = date ? new Date(run.ts).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "Date unspecified";
      groups.set(id, { id, date, location, label: `${dateLabel} · ${location}`, runs: [] });
    }
    groups.get(id)!.runs.push(run);
  }
  for (const group of groups.values()) group.runs.sort((a, b) => collator.compare(directionOf(a), directionOf(b)) || b.ts - a.ts || collator.compare(a.name, b.name) || collator.compare(driverOf(a), driverOf(b)) || collator.compare(a.id, b.id));
  return [...groups.values()].sort((a, b) => b.date.localeCompare(a.date) || collator.compare(a.location, b.location));
}

export function filterRuns<T extends BrowserEntry>(runs: T[], driver: string, direction: string, search: string): T[] {
  const query = search.trim().toLocaleLowerCase();
  return runs.filter(run => (!driver || driverOf(run) === driver) && (!direction || directionOf(run) === direction) &&
    (!query || [run.name, run.event, driverOf(run), directionOf(run), run.track].join(" ").toLocaleLowerCase().includes(query)));
}

export function filterOptions(runs: BrowserEntry[], value: (run: BrowserEntry) => string): string[] {
  return [...new Set(runs.map(value))].sort(collator.compare);
}

/** Layout metadata is a separate browser entry, never a fabricated timed run. */
export function layoutEntries(tracks: Track[]): BrowserEntry[] {
  const roads = new Map<string, Track>();
  for (const track of tracks) if (track.edges && !roads.has(layoutId(track))) roads.set(layoutId(track), track);
  return [...roads.values()].map(t => ({
    id: layoutId(t), name: layoutName(t), driver: "", venue: t.venue, track: layoutName(t), trackId: layoutId(t),
    event: `${t.RL.toFixed(0)} m · ${(roadWidth(t.edges!) / 0.3048).toFixed(1)} ft wide`,
    ts: t.created, added: t.created,
  }));
}
