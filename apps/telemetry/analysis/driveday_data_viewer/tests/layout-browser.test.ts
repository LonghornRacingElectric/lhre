import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Workspace } from "../src/components/Workspace";
import { roadWidth, TRACK_WIDTH_M } from "../src/lib/geometry";
import { layoutId } from "../src/lib/layouts";
import { LayoutView } from "../src/components/LayoutView";
import { convertSession } from "../src/lib/convert";
import { importLayoutRecording, layoutTracksFromSession } from "../src/lib/layout-import";
import { driveDayGroups, layoutEntries } from "../src/lib/run-picker";
import { metaOf, runStore } from "../src/lib/storage";
import type { SessionExport, Track } from "../src/lib/types";

const fixture = (name: string): SessionExport => JSON.parse(readFileSync(new URL(`../test-data/${name}.json`, import.meta.url), "utf8"));
const recording = fixture("layout");
const tracks = layoutTracksFromSession(recording, "layout.json");

describe("saved layouts without driver runs", () => {
  it("makes separate browsable entries with no fake laps or best times", () => {
    const entries = layoutEntries(tracks);
    expect(entries).toHaveLength(1);
    expect(entries.every(e => !("laps" in e) && !("best" in e))).toBe(true);
    expect(driveDayGroups(entries)).toHaveLength(1);
    expect(entries[0].name).not.toMatch(/clockwise/i);
    expect(layoutEntries([...tracks].reverse())[0].id).toBe(entries[0].id);
    expect(layoutEntries(tracks.map(t => ({ ...t, edges: undefined })))).toEqual([]);
  });
  it("renders the saved road without a Player, Session or driver data", () => {
    const html = renderToStaticMarkup(createElement(LayoutView, { track: tracks[0] }));
    expect(html).toContain('aria-label="Saved track layout"');
    expect(html).toContain("Saved layout");
    expect(html).toContain("11.5 ft wide");
    expect(html).toContain("<svg");
    expect(html).not.toContain('role="meter"');
    expect(html).not.toContain("Lap time");
  });
  it("keeps the workspace, analysis tabs and blank playback without a driver", () => {
    const html = renderToStaticMarkup(createElement<{ track?: Track }>(Workspace, { track: tracks[0] }));
    for (const label of ["Video window", "Track window", "Analysis window", "Telemetry", "Corners", "Debrief", "Empty timeline", "Saved track layout"]) expect(html).toContain(label);
    expect(html).toContain("disabled");
    expect(html).not.toContain('role="meter"');
  });
  it("recovers legacy missing or invalid widths without showing NaN", () => {
    for (const width of [undefined, NaN, Infinity, 0, -1]) {
      const edges = { ...tracks[0].edges!, width: width as number };
      expect(roadWidth(edges)).toBeGreaterThan(3);
      const track = { ...tracks[0], edges };
      const html = renderToStaticMarkup(createElement(LayoutView, { track }));
      expect(html).not.toMatch(/NaN|Infinity/);
      expect(layoutEntries([track])[0].event).toContain("11.5 ft wide");
    }
    expect(roadWidth({ left: [], right: [] })).toBe(TRACK_WIDTH_M);
  });
  it("keeps the layout saved and selectable before and after driver imports", async () => {
    expect(await runStore.list()).toEqual([]);
    const result = await importLayoutRecording(recording, "layout.json");
    expect(result.updated).toEqual([]);
    expect(await runStore.list()).toEqual([]);
    const saved = layoutEntries(await runStore.tracks());
    expect(saved.map(e => e.id).sort()).toEqual([layoutId(result.tracks[0])]);
    const drivers = convertSession(fixture("run-driver-a"), "a.json", await runStore.tracks());
    try {
      for (const c of drivers) await runStore.put(metaOf(c.D), c.D);
      expect(await runStore.list()).toHaveLength(2);
      expect(layoutEntries(await runStore.tracks()).map(e => e.id).sort()).toEqual(saved.map(e => e.id).sort());
      const before = await runStore.get(drivers[0].D.meta.sessId);
      await runStore.delLayout(saved[0].id);
      expect(layoutEntries(await runStore.tracks())).toEqual([]);
      expect(await runStore.get(drivers[0].D.meta.sessId)).toEqual(before);
    } finally { for (const c of drivers) await runStore.del(c.D.meta.sessId); }
  });
});
