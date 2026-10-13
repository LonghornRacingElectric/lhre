import { describe, expect, it } from "vitest";
import { GPS_RIGHT_OFFSET_M, TRACK_WIDTH_M, trackEdges } from "../src/lib/geometry";
import type { XY } from "../src/lib/types";

const circle = (r: number, n = 360, ccw = true): XY[] => {
  const p: XY[] = [];
  for (let i = 0; i < n; i++) {
    const a = ((ccw ? 1 : -1) * 2 * Math.PI * i) / n;
    p.push([r * Math.cos(a), r * Math.sin(a)]);
  }
  p.push(p[0]);
  return p;
};
const radii = (a: XY[]) => a.map((q) => Math.hypot(q[0], q[1]));

describe("track edges", () => {
  it("uses 11.5 ft wide and a 1 ft right shift", () => {
    expect(TRACK_WIDTH_M).toBeCloseTo(3.5052, 4);
    expect(GPS_RIGHT_OFFSET_M).toBeCloseTo(0.3048, 4);
  });

  it("moves the centre right of travel and puts the edges half a width either side", () => {
    /* counter-clockwise: right of travel is outward */
    const E = trackEdges(circle(20));
    const h = TRACK_WIDTH_M / 2;
    for (const r of radii(E.centre)) expect(r).toBeCloseTo(20 + GPS_RIGHT_OFFSET_M, 2);
    for (const r of radii(E.left)) expect(r).toBeCloseTo(20 + GPS_RIGHT_OFFSET_M - h, 2);
    for (const r of radii(E.right)) expect(r).toBeCloseTo(20 + GPS_RIGHT_OFFSET_M + h, 2);
    expect(E.centre[0]).toEqual(E.centre[E.centre.length - 1]);
  });

  it("works the other way round too", () => {
    /* clockwise: right of travel is inward */
    const E = trackEdges(circle(20, 360, false));
    for (const r of radii(E.centre)) expect(r).toBeCloseTo(20 - GPS_RIGHT_OFFSET_M, 2);
    for (const r of radii(E.right)) expect(r).toBeCloseTo(20 - GPS_RIGHT_OFFSET_M - TRACK_WIDTH_M / 2, 2);
  });

  it("shifts an open straight to the right of travel", () => {
    const line: XY[] = Array.from({ length: 20 }, (_, i) => [i, 0] as XY); // heading east, so right is south
    const E = trackEdges(line, true);
    expect(E.centre.length).toBe(20);
    E.centre.forEach((q) => expect(q[1]).toBeCloseTo(-GPS_RIGHT_OFFSET_M, 6));
    E.left.forEach((q) => expect(q[1]).toBeCloseTo(-GPS_RIGHT_OFFSET_M + TRACK_WIDTH_M / 2, 6));
    E.right.forEach((q) => expect(q[1]).toBeCloseTo(-GPS_RIGHT_OFFSET_M - TRACK_WIDTH_M / 2, 6));
  });

  it("pinches the inner edge of a hairpin tighter than half the width instead of folding it", () => {
    const E = trackEdges(circle(1.2));
    for (const r of radii(E.left)) expect(r).toBeGreaterThan(0);
  });
});
