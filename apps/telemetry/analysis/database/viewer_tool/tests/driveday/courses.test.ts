import { describe, expect, it } from "vitest";
import { convertSession } from "../../src/lib/driveday/convert";
import { rebuildRun } from "../../src/lib/driveday/rebuild";
import { Session } from "../../src/lib/driveday/session";
import { segAt } from "../../src/lib/driveday/segments";
import { COURSE_LEN, synthCourses } from "./fixtures/courses";
import { synthSession, TRACK_LEN } from "./fixtures/synth";
import type { LiveSample, SessionExport } from "../../src/lib/driveday/types";

const J = synthCourses();
const courses = convertSession(J, "courses.json", []);

describe("manual presses at different positions on a loop", () => {
  it("uses a full loop and preserves logger labels even when cuts are far apart", () => {
    const j = synthSession({ laps: 4 });
    const offsets = [0, 3500, -1800, 2300, 0];
    j.laps.forEach((l, i) => {
      l.startMs += offsets[i]; l.endMs += offsets[i + 1];
      l.durationMs = l.endMs - l.startMs;
      l.label = "Manual " + (i * 2 + 3);
    });
    const imported = convertSession(j, "varied-cuts.json");
    expect(imported).toHaveLength(1);
    expect(imported[0].D.open).toBeUndefined();
    expect(Math.abs(imported[0].D.RL - TRACK_LEN)).toBeLessThan(TRACK_LEN * 0.1);
    expect(imported[0].D.laps.filter((l) => l.kind === "lap").map((l) => l.name)).toEqual(j.laps.map((l) => l.label));
  });
});

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
    expect(segAt(s.segs, s.RL, s.RL, true)).toBe(s.segs.length - 1);
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
  it("leave an open course as it was imported", () => {
    courses.forEach((c) => expect(rebuildRun(c.D)).toBe(c.D));
  });
});

describe("a finish line beside the start line", () => {
  /* one 10 m/s run: north up the start straight, round, then east across it on y = 25 to finish at x = endX */
  const run = (endX: number) => {
    const KY = (6378137 * Math.PI) / 180;
    const KX = KY * Math.cos((30.3865 * Math.PI) / 180);
    const corners = [[0, 0], [0, 150], [-150, 150], [-150, 25], [endX, 25]];
    const sampleTail: LiveSample[] = [];
    let t = 1_700_200_000_000;
    corners.slice(1).forEach(([x, y], i) => {
      const [x0, y0] = corners[i];
      const n = Math.round(Math.hypot(x - x0, y - y0));
      for (let k = 0; k < n; k++, t += 100) {
        sampleTail.push({ t, lat: 30.3865 + (y0 + ((y - y0) * k) / n) / KY, lon: -97.7242 + (x0 + ((x - x0) * k) / n) / KX, speed: 10, hv_pack_v: 500, values: {} });
      }
    });
    const startMs = sampleTail[0].t;
    const endMs = sampleTail[sampleTail.length - 1].t;
    const laps = [{ label: "Run 1", startMs, endMs, durationMs: endMs - startMs, samples: [] }];
    return convertSession({ version: 1, source: "orion", laps, sampleTail } as unknown as SessionExport, "finish.json", [])[0].D;
  };

  it("is an open course when the run ends driving away from the start, with no chord closing it", () => {
    const D = run(15);
    expect(D.open).toBe(true);
    expect(Math.hypot(D.ref[0][0] - D.ref[D.ref.length - 1][0], D.ref[0][1] - D.ref[D.ref.length - 1][1])).toBeGreaterThan(20);
  });

  it("stays a loop when the run ends heading back to its start (an early press)", () => {
    expect(run(-15).open).toBeUndefined();
  });
});
