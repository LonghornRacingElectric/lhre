import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { convertSession } from "../src/lib/convert";
import { attachLayout, convertDriverSession, detachLayout, importLayoutRecording, isLayoutRecording, layoutTracksFromSession } from "../src/lib/layout-import";
import { builtinTracks } from "../src/lib/layouts";
import { comparisonLap } from "../src/lib/comparison";
import { rebuildRun } from "../src/lib/rebuild";
import { Session } from "../src/lib/session";
import { metaOf, runStore } from "../src/lib/storage";
import { reproject } from "../src/lib/trackMatching";
import type { SessionExport } from "../src/lib/types";
import { synthCourses } from "./fixtures/courses";
import { synthSession } from "./fixtures/synth";

const fixture = (name: string): SessionExport => JSON.parse(readFileSync(new URL(`../test-data/${name}.json`, import.meta.url), "utf8"));
const layout = fixture("layout"), a = fixture("run-driver-a"), b = fixture("run-driver-b");

describe("centreline layout imports", () => {
  it("recognizes only intentional layout filenames, with an explicit action for other names", () => {
    expect(isLayoutRecording("layout.json")).toBe(true);
    expect(isLayoutRecording("Layout_session.json")).toBe(true);
    expect(isLayoutRecording("run-driver-a.json")).toBe(false);
    expect(isLayoutRecording("a-driver-layout-comparison.json")).toBe(false);
  });
  it("calculates both physical edges of a new arbitrary layout, without bundling it", () => {
    const tracks = layoutTracksFromSession(layout, "anything.json");
    expect(tracks).toHaveLength(2);
    expect(tracks[0].id).not.toBe(tracks[1].id);
    expect(tracks.every(t => !t.builtin && t.edges && t.RL > 600)).toBe(true);
    const [ccw, cw] = tracks;
    expect(cw.edges!.left).toEqual([...ccw.edges!.right].reverse());
    expect(cw.edges!.right).toEqual([...ccw.edges!.left].reverse());
    expect(ccw.edges!.width).toBeCloseTo(11.5 * 0.3048);
    const gaps = ccw.edges!.left.map((p, i) => Math.hypot(p[0] - ccw.edges!.right[i][0], p[1] - ccw.edges!.right[i][1]));
    expect(gaps.sort((x, y) => x - y)[Math.floor(gaps.length / 2)]).toBeCloseTo(11.5 * 0.3048, 2);
    expect(layoutTracksFromSession(layout, "renamed.json").map(t => t.id)).toEqual(tracks.map(t => t.id));
  });
  it("rejects incomplete/open recordings instead of drawing a false closing edge", () => {
    expect(() => layoutTracksFromSession(synthCourses(), "layout.json")).toThrow(/closed route/);
    expect(() => layoutTracksFromSession({ nope: true } as unknown as SessionExport, "layout.json")).toThrow();
  });
  it("attaches road edges on later imports and keeps directions separate", () => {
    const tracks = layoutTracksFromSession(layout, "layout.json");
    const first = convertSession(a, "a.json", tracks), second = convertSession(b, "b.json", tracks);
    expect(first).toHaveLength(2);
    first.forEach((c, i) => {
      expect(c.D.edges).toBeDefined();
      expect(c.isNew).toBe(false);
      expect(c.D.meta.trackId).toBe(second[i].D.meta.trackId);
      const session = new Session(rebuildRun(c.D)), other = rebuildRun(second[i].D);
      expect(comparisonLap(session, other, other.laps.findIndex(l => l.kind === "lap")).ch.brkp.every(v => v <= 100)).toBe(true);
    });
    expect(first[0].D.meta.trackId).not.toBe(first[1].D.meta.trackId);
  });
  it("only automatically adopts imported roads and can restore the exact baseline", () => {
    const input = JSON.stringify(a);
    const roads = layoutTracksFromSession(layout, "layout.json");
    const blocked = convertDriverSession(a, "a.json", roads.map(t => ({ ...t, builtin: true })));
    expect(blocked.every(c => !c.D.edges)).toBe(true);
    const unrelatedBuiltins = convertDriverSession(a, "a.json", builtinTracks());
    expect(unrelatedBuiltins.every(c => !c.D.edges)).toBe(true);
    const attached = convertDriverSession(a, "a.json", roads);
    for (const c of attached) {
      expect(c.D.edges).toBeDefined();
      const baseline = c.D.layoutOriginal!;
      expect(baseline).toBeDefined();
      const detached = detachLayout(c.D);
      expect(detached).toEqual({ ...baseline, layoutDetached: true });
      expect(detached.edges).toBeUndefined();
      expect(attachLayout(detached, c.track, roads)).toBeNull();
      c.D.laps.forEach((lap, i) => {
        expect(lap.ch.thr).toBe(baseline.laps[i].ch.thr);
        expect(lap.ch.brk).toBe(baseline.laps[i].ch.brk);
        expect(lap.dur).toBe(baseline.laps[i].dur);
        const restored = reproject([lap.pts[0][0], lap.pts[0][1]], roads.find(t => t.id === c.D.meta.trackId)!, c.track);
        expect(restored[0]).toBeCloseTo(baseline.laps[i].pts[0][0], 5);
        expect(restored[1]).toBeCloseTo(baseline.laps[i].pts[0][1], 5);
      });
      const legacy = detachLayout({ ...c.D, layoutOriginal: undefined });
      expect(legacy.edges).toBeUndefined();
      legacy.laps.forEach((lap, i) => {
        expect(lap.pts.map(p => p.slice(0, 3))).toEqual(c.D.laps[i].pts.map(p => p.slice(0, 3)));
        expect(lap.ch.thr).toBe(c.D.laps[i].ch.thr);
        expect(lap.dur).toBe(c.D.laps[i].dur);
      });
    }
    expect(JSON.stringify(a)).toBe(input);
  });
  it("upgrades already imported drivers without changing their GPS, telemetry, lap IDs or times", async () => {
    const first = convertSession(a, "a.json"), second = convertSession(b, "b.json", first.map(c => c.track));
    const originals = [...first, ...second];
    for (const c of originals) {
      await runStore.putTrack(c.track);
      await runStore.put({ ...metaOf(c.D), added: 123 }, c.D);
    }
    const unrelated = convertSession(synthSession({ sessionId: "unrelated-layout-import-test" }), "unrelated.json")[0];
    await runStore.putTrack(unrelated.track);
    await runStore.put(metaOf(unrelated.D), unrelated.D);
    try {
      const result = await importLayoutRecording(layout, "layout.json");
      expect(result.updated.sort()).toEqual(originals.map(c => c.D.meta.sessId).sort());
      for (const c of originals) {
        const updated = (await runStore.get(c.D.meta.sessId))!;
        expect(updated.edges).toBeDefined();
        expect(updated.meta.sessId).toBe(c.D.meta.sessId);
        const newTrack = result.tracks.find(t => t.id === updated.meta.trackId)!;
        expect(newTrack).toBeDefined();
        updated.laps.forEach((l, i) => {
          const old = c.D.laps[i];
          expect([l.name, l.t0, l.t1, l.dur, l.wh]).toEqual([old.name, old.t0, old.t1, old.dur, old.wh]);
          expect(l.ch.t).toBe(old.ch.t);
          expect(l.ch.thr).toBe(old.ch.thr);
          expect(l.ch.brk).toBe(old.ch.brk);
          const restored = reproject([l.pts[0][0], l.pts[0][1]], newTrack, c.track);
          expect(restored[0]).toBeCloseTo(old.pts[0][0], 5);
          expect(restored[1]).toBeCloseTo(old.pts[0][1], 5);
        });
      }
      expect((await runStore.get(unrelated.D.meta.sessId))!.edges).toBeUndefined();
      expect(attachLayout(unrelated.D, unrelated.track, result.tracks)).toBeNull();
      const saved = await runStore.list();
      expect(saved.filter(m => result.updated.includes(m.id)).every(m => m.added === 123)).toBe(true);
      const id = originals[0].D.meta.sessId;
      const detached = detachLayout((await runStore.get(id))!);
      await runStore.put(metaOf(detached), detached);
      const reimport = await importLayoutRecording(layout, "layout.json");
      expect(reimport.updated).not.toContain(id);
      expect((await runStore.get(id))!.edges).toBeUndefined();
      expect((await runStore.get(id))!.layoutDetached).toBe(true);
    } finally {
      for (const c of [...originals, unrelated]) await runStore.del(c.D.meta.sessId);
    }
  });
});
