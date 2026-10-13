import { assignStoredLayout } from "./assignLayout";
import { testLayouts } from "./layouts";
import { metaOf, runStore } from "./storage";
import type { Track } from "./types";

/** Match older saved runs before opening the viewer, so upgrades also work on existing browser data. */
export async function upgradeStoredLayouts(): Promise<void> {
  const saved = await runStore.tracks();
  const entries = (await runStore.list()).sort((a, b) => a.added - b.added);
  const known: Track[] = [...testLayouts];
  for (const entry of entries) {
    const data = await runStore.get(entry.id);
    if (!data) continue;
    const previous = [...testLayouts, ...saved].find(t => t.id === data.meta.trackId);
    if (!previous) continue;
    if (data.layoutVersion === 3) {
      if (!known.some(t => t.id === previous.id)) known.push(previous);
      continue;
    }
    const assigned = assignStoredLayout(data, previous, known);
    if (!known.some(t => t.id === assigned.track.id)) known.push(assigned.track);
    await runStore.putTrack(assigned.track);
    await runStore.put({ ...metaOf(assigned.data), added: entry.added }, assigned.data);
  }
}
