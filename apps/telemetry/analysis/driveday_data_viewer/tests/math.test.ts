import { describe, expect, it } from "vitest";
import { circDist, interp, med, nice, pctl, sd, theil, wrap } from "../src/lib/math";

describe("math", () => {
  it("interpolates and clamps", () => {
    expect(interp([0, 10], [0, 100], 5)).toBe(50);
    expect(interp([0, 10], [0, 100], -3)).toBe(0);
    expect(interp([0, 10], [0, 100], 99)).toBe(100);
    expect(interp([0, 0, 10], [1, 2, 3], 0)).toBe(1);
  });
  it("median, percentile, deviation", () => {
    expect(med([3, 1, 2])).toBe(2);
    expect(med([1, 2, 3, 4])).toBe(2.5);
    expect(pctl([1, 2, 3, 4, 5], 0.5)).toBe(3);
    expect(sd([2, 2, 2])).toBe(0);
    expect(sd([1])).toBe(0);
  });
  it("Theil–Sen ignores one wild reading", () => {
    const xs = [0, 1, 2, 3, 4, 5];
    const ys = [0, -2, -4, -6, 400, -10];
    expect(theil(xs, ys)).toBeCloseTo(-2, 5);
  });
  it("wraps around a loop", () => {
    expect(wrap(-3, 100)).toBe(97);
    expect(wrap(103, 100)).toBe(3);
    expect(circDist(2, 98, 100)).toBe(4);
  });
  it("nice axis bounds contain the data", () => {
    const n = nice(3.2, 47, 4);
    expect(n.lo).toBeLessThanOrEqual(3.2);
    expect(n.hi).toBeGreaterThanOrEqual(47);
  });
});
