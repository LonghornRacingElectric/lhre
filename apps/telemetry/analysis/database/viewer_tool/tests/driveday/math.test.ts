import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { circDist, interp, med, nice, pctl, sd, theil, timeAverage, wrap } from "../../src/lib/driveday/math";

describe("math", () => {
  it("smooths spikes without dropping samples or biasing a constant ramp", () => {
    const t = Array.from({ length: 21 }, (_, i) => i * 50);
    const constant = timeAverage(t, t.map(() => 10));
    expect(constant).toEqual(t.map(() => 10));
    expect(timeAverage(t, t.map((v) => v / 100))[10]).toBeCloseTo(5, 8);
    const spike = timeAverage(t, t.map((_, i) => i === 10 ? 10 : 0));
    expect(spike).toHaveLength(t.length);
    expect(spike[10]).toBeGreaterThan(0);
    expect(spike[10]).toBeLessThan(2);
  });
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
  it("nice axis bounds always contain the data and span more than nothing", () => {
    fc.assert(fc.property(fc.double({ min: -1e6, max: 1e6, noNaN: true }), fc.double({ min: 0, max: 1e6, noNaN: true }), fc.integer({ min: 2, max: 6 }), (lo, span, n) => {
      const r = nice(lo, lo + span, n);
      expect(r.hi).toBeGreaterThan(r.lo);
      /* nice() snaps a value within 1e-9 of a tick onto it, so containment holds to that */
      const tol = 1e-8 * (r.hi - r.lo);
      expect(r.lo).toBeLessThanOrEqual(lo + tol);
      expect(r.hi).toBeGreaterThanOrEqual(lo + span - tol);
      expect(r.t.length).toBeGreaterThanOrEqual(2);
    }), { seed: 20261120, numRuns: 1000 });
  });
  it("nice gives a flat or empty series an axis instead of a zero range", () => {
    for (const [lo, hi] of [[3.6, 3.6], [0, 0], [Infinity, -Infinity], [NaN, NaN]]) {
      const r = nice(lo, hi, 4);
      expect(Number.isFinite(r.lo) && Number.isFinite(r.hi)).toBe(true);
      expect(r.hi).toBeGreaterThan(r.lo);
    }
  });
});
