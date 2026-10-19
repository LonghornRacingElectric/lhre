import { readFileSync } from "fs";
import { describe, expect, it } from "vitest";
import { Analysis } from "../src/lib/analysis";
import { convertSession } from "../src/lib/convert";
import { lapsCsv } from "../src/lib/debrief";
import { lapCutJoin, roadOf } from "../src/lib/geometry";
import { rebuildRun } from "../src/lib/rebuild";
import { Session } from "../src/lib/session";
import { emptyUserState, type SessionExport } from "../src/lib/types";
import { defaultUnits } from "../src/lib/units";

const path = process.env.DRIVEDAY_COTA_SAMPLE;
describe.skipIf(!path)("COTA course 1 manual-cut regression", () => {
  const data = path ? JSON.parse(readFileSync(path, "utf8")) as SessionExport : null;
  const courses = data ? convertSession(data, "COTA.json") : [];
  it("recognises the whole loop and its reverse instead of a short open-course chord", () => {
    expect(courses).toHaveLength(2);
    expect(courses.every((c) => !c.D.open)).toBe(true);
    expect(courses[1].label).toContain("reverse of 1");
    expect(courses[0].D.RL).toBeGreaterThan(390);
    expect(courses[0].D.RL).toBeLessThan(430);
    expect(courses[0].D.laps.every((l) => l.dev < 3)).toBe(true);
  });
  it("keeps logger labels and timings through analysis and CSV", () => {
    const s = new Session(rebuildRun(courses[0].D));
    expect(s.runs.map((l) => l.name)).toEqual(["Manual 3", "Manual 5"]);
    s.runs.forEach((l) => expect(l.dur).toBe(data!.laps.find((source) => source.label === l.name)!.durationMs));
    const a = new Analysis(s, emptyUserState());
    expect(a.bestSec.every((b) => ["Manual 3", "Manual 5"].includes(b.label))).toBe(true);
    expect(lapsCsv(a, defaultUnits())).toContain("Manual 3,");
  });
  it("draws a measured overlay without a closing chord and labels any untimed lap-cut join", () => {
    const s = new Session(rebuildRun(courses[0].D));
    const l = s.runs[0];
    const road = roadOf(l);
    expect(road[road.length - 1].slice(0, 2)).not.toEqual(road[0].slice(0, 2));
    const join = lapCutJoin(l, s.geo)!;
    expect(join).not.toBeNull();
    expect(join.distance).toBeGreaterThan(1);
    expect(join.distance).toBeLessThan(s.RL * 0.15);
    expect(join.points.every((point) => s.geo.nearest(point[0], point[1]).d < 0.01)).toBe(true);
    expect(join.points[0]).not.toEqual(l.pts[l.pts.length - 1].slice(0, 2));
    expect(lapCutJoin({ ...l, kind: "partial" }, s.geo)).toBeNull();
  });
});
