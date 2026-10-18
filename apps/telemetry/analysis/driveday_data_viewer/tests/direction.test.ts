import { describe, expect, it, vi } from "vitest";
import { directionName, pathDirection, withRunDirection } from "../src/lib/direction";
import { convertSession } from "../src/lib/convert";
import { builtinTracks } from "../src/lib/layouts";
import { metaOf, runStore } from "../src/lib/storage";
import { driveDayGroups } from "../src/lib/run-picker";
import type { XY } from "../src/lib/types";
import { synthCourses } from "./fixtures/courses";

const circle = (from = 0, to = 2 * Math.PI): XY[] => Array.from({ length: 201 }, (_, i) => {
  const a = from + (to - from) * i / 200;
  return [40 * Math.cos(a), 40 * Math.sin(a)];
});

describe("GPS-derived course direction", () => {
  it("classifies both directions without layout IDs or course names", () => {
    const path = circle();
    expect(pathDirection(path)).toBe("ccw");
    expect(pathDirection([...path].reverse())).toBe("cw");
    expect(pathDirection(circle(1, 1 + 2 * Math.PI))).toBe("ccw");
  });
  it("is unchanged by translation, rotation, driving speed, sampling density or mild GPS noise", () => {
    const path = circle().flatMap((p, i) => {
      const a = 0.7, x = p[0] * Math.cos(a) - p[1] * Math.sin(a), y = p[0] * Math.sin(a) + p[1] * Math.cos(a);
      const q: XY = [x + 100000 + Math.sin(i * 2) * 0.15, y - 200000 + Math.cos(i * 3) * 0.15];
      return Array.from({ length: i < 100 ? 5 : 1 }, () => q);
    });
    expect(pathDirection(path)).toBe("ccw");
    expect(pathDirection([...path].reverse())).toBe("cw");
  });
  it("supports separate start and finish lines without counting a drive back", () => {
    expect(pathDirection(circle(0, Math.PI))).toBe("ccw");
    expect(pathDirection(circle(Math.PI, 0))).toBe("cw");
    const courses = convertSession(synthCourses(), "anything.json");
    expect(courses.map(c => c.D.meta.direction)).toEqual(["ccw", "cw"]);
    expect(courses.map(c => c.label)).toEqual(["Counter-clockwise", "Clockwise"]);
    expect(courses.every(c => !/Course \d|reverse of/i.test(c.D.meta.name))).toBe(true);
  });
  it("does not invent a direction for straight paths, stationary GPS jitter, or balanced figure-eights", () => {
    expect(pathDirection([])).toBe("unknown");
    expect(pathDirection([[0, 0], [30, 0], [100, 0]])).toBe("unknown");
    expect(pathDirection([[0, 0], [0.1, -0.1], [0, 0.1], [0, 0]])).toBe("unknown");
    const eight: XY[] = Array.from({ length: 401 }, (_, i) => { const a = i / 400 * Math.PI * 2; return [40 * Math.sin(a), 20 * Math.sin(2 * a)]; });
    expect(pathDirection(eight)).toBe("unknown");
  });
  it("agrees with both built-in layout geometries without consulting their names", () => {
    const [ccw, cw] = builtinTracks();
    expect(pathDirection(ccw.ref)).toBe("ccw");
    expect(pathDirection(cw.ref)).toBe("cw");
  });
  it("relabels old course suffixes without changing run IDs, laps, timestamps or geometry", () => {
    const data = convertSession(synthCourses(), "session.json")[0].D;
    const old = { ...data, meta: { ...data.meta, direction: undefined, name: "Session 2 · Course 1", trackName: "Layout · Course 1" } };
    const named = withRunDirection(old);
    expect(named.meta.name).toBe("Session 2 · Counter-clockwise");
    expect(named.meta.sessId).toBe(old.meta.sessId);
    expect(named.meta.trackId).toBe(old.meta.trackId);
    expect(named.laps).toBe(old.laps);
    expect(named.ref).toBe(old.ref);
    expect(old.meta.name).toBe("Session 2 · Course 1");
    expect(directionName("Session · Course 2 (reverse of 1)", "cw")).toBe("Session · Clockwise");
  });
  it("backfills and caches saved run-list metadata, preserving import order and raw data", async () => {
    const data = convertSession(synthCourses(), "session.json")[0].D;
    const old = { ...data, meta: { ...data.meta, sessId: "direction-migration-test", direction: undefined, name: "Old session · Course 1" } };
    const meta = { ...metaOf(old), name: old.meta.name, direction: undefined, added: 123 };
    await runStore.put(meta, old);
    const spy = vi.spyOn(runStore, "get");
    try {
      const migrated = (await runStore.list()).find(m => m.id === meta.id)!;
      expect(migrated.direction).toBe("ccw");
      expect(migrated.name).toBe("Old session · Counter-clockwise");
      expect(migrated.added).toBe(123);
      spy.mockClear();
      await runStore.list();
      expect(spy).not.toHaveBeenCalled();
      expect((await runStore.get(meta.id))!.laps).toBe(old.laps);
    } finally { spy.mockRestore(); await runStore.del(meta.id); }
  });
  it("sorts opposite directions together within a drive day", () => {
    const courses = convertSession(synthCourses(), "same-day.json");
    const groups = driveDayGroups(courses.map(c => metaOf(c.D)));
    expect(groups[0].runs.map(r => r.direction)).toEqual(["cw", "ccw"]);
  });
});
