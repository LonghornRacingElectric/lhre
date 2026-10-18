import { describe, expect, it } from "vitest";
import { convertSession } from "../src/lib/convert";
import { rebuildRun } from "../src/lib/rebuild";
import { Session } from "../src/lib/session";
import { COURSE_LEN, synthCourses } from "./fixtures/courses";

const J = synthCourses();
const courses = convertSession(J, "courses.json", []);

describe("a session with separate start and finish lines, and a run the other way", () => {
  it("opens each way round as its own course", () => {
    expect(courses.length).toBe(2);
    expect(courses.map((c) => c.label)).toEqual(["Course 1", "Course 2 (reverse of 1)"]);
    expect(courses.map((c) => c.D.meta.sessId)).toEqual(["sess-courses-1~1", "sess-courses-1~2"]);
  });

  it("treats a course with a different finish line as open, not a loop", () => {
    courses.forEach((c) => {
      expect(c.D.open).toBe(true);
      expect(c.track.open).toBe(true);
      expect(Math.abs(c.D.RL - COURSE_LEN)).toBeLessThan(COURSE_LEN * 0.1);
    });
  });

  it("keeps only the timed runs and says what it left out", () => {
    courses.forEach((c) => expect(c.D.laps.filter((l) => l.kind === "lap").length).toBe(2));
    expect(courses[0].D.warnings.join(" ")).toMatch(/Lap 2, Lap 5 are too short or slow/);
    expect(courses.every((c) => c.D.laps.every((l) => l.kind === "lap"))).toBe(true);
  });

  it("keeps the logger's times", () => {
    const dur = (c: number) => courses[c].D.laps.map((l) => l.dur);
    expect(dur(0)).toEqual([J.laps[0].durationMs, J.laps[2].durationMs]);
    expect(dur(1)).toEqual([J.laps[3].durationMs, J.laps[5].durationMs]);
  });

  it("runs each lap from the start line to the finish line", () => {
    courses.forEach((c) =>
      c.D.laps.forEach((l) => {
        expect(l.ch.u[0]).toBeLessThan(5);
        expect(c.D.RL - l.ch.u[l.ch.u.length - 1]).toBeLessThan(5);
        for (let i = 1; i < l.ch.u.length; i++) expect(l.ch.u[i]).toBeGreaterThanOrEqual(l.ch.u[i - 1]);
        expect((l.sec as number[]).reduce((a, b) => a + b, 0)).toBeCloseTo(l.dur, 0);
      }),
    );
  });

  it("finds corners and straights without wrapping round", () => {
    const s = new Session(rebuildRun(courses[0].D));
    expect(s.segs.length).toBeGreaterThan(1);
    expect(s.segs[0].a).toBe(0);
    const last = s.segs[s.segs.length - 1];
    expect(last.a + last.len).toBeCloseTo(s.RL, 0);
    expect(s.segs.some((g) => g.type === "c")).toBe(true);
  });
});

describe("the start and finish lines", () => {
  it("sit where the logger timed each run to begin and end", () => {
    courses.forEach((c) => {
      const a = c.D.ref[0];
      const b = c.D.ref[c.D.ref.length - 1];
      c.D.laps.forEach((l) => {
        const p0 = l.pts[0];
        const p1 = l.pts[l.pts.length - 1];
        expect(Math.hypot(p0[0] - a[0], p0[1] - a[1])).toBeLessThan(6);
        expect(Math.hypot(p1[0] - b[0], p1[1] - b[1])).toBeLessThan(6);
      });
    });
  });
  it("keeps open-course samples and logged times while recalculating splits", () => {
    courses.forEach((c) => {
      const rebuilt = rebuildRun(c.D);
      rebuilt.laps.forEach((l, i) => {
        expect(l.ch).toBe(c.D.laps[i].ch);
        expect(l.pts).toBe(c.D.laps[i].pts);
        expect(l.dur).toBe(c.D.laps[i].dur);
        if (l.kind === "lap") expect(l.sec.reduce<number>((sum, v) => sum + (v ?? 0), 0)).toBeCloseTo(l.dur);
      });
    });
  });
});
