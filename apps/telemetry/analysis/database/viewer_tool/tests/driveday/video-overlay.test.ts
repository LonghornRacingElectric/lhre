import { describe, expect, it } from "vitest";
import { roadLine, defaultLine } from "../../src/lib/driveday/video-overlay";
import type { Lap } from "../../src/lib/driveday/types";

const straight = (side = 0): Lap => {
  const t = Array.from({ length: 21 }, (_, i) => i * 1000);
  const u = t.map(v => v / 100);
  const pts = u.map(s => [s, side, 10, s, 0]);
  return { kind: "lap", dur: 20000, pts, ch: { t, u, x: u, y: u.map(() => side), spd: u.map(() => 22), brkp: u.map(() => 20), thr: u.map(() => 0) } } as Lap;
};

describe("recorded racing-line video projection", () => {
  it("projects a straight ahead of the car toward the horizon", () => {
    const lap = straight();
    const points = roadLine(lap, lap, 5000, defaultLine);
    expect(points.length).toBeGreaterThan(10);
    expect(points.every(p => Math.abs(p.x - 0.5) < 0.001)).toBe(true);
    expect(points[0].y).toBeGreaterThan(points.at(-1)!.y);
    expect(points.every(p => p.y > defaultLine.horizon && p.y < 1)).toBe(true);
    expect(points[0].brake).toBe(20);
  });
  it("places a ghost to the correct side and applies calibration", () => {
    const points = roadLine(straight(), straight(2), 5000, defaultLine);
    expect(points[0].x).toBeLessThan(0.5);
    const shifted = roadLine(straight(), straight(2), 5000, { ...defaultLine, center: 0.6 });
    expect(shifted[0].x - points[0].x).toBeCloseTo(0.1);
  });
  it("does not fabricate a line beyond the finish or while stationary", () => {
    const lap = straight();
    expect(roadLine(lap, lap, lap.dur, defaultLine)).toEqual([]);
    expect(roadLine({ ...lap, ch: { ...lap.ch, spd: lap.ch.spd.map(() => 0) } }, lap, 5000, defaultLine)).toEqual([]);
  });
});
