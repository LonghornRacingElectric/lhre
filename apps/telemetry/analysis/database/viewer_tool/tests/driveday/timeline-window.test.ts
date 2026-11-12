import { describe, expect, it } from "vitest";
import { timelineWindow, zoomWindow, speedEnvelope } from "../../src/lib/driveday/timeline-window";

describe("timeline zoom", () => {
  it("zooms around the cursor and clamps panning to session bounds", () => {
    expect(zoomWindow(100000, 0, 100000, 2, 25000)).toEqual({ start: 12500, end: 62500, span: 50000 });
    expect(timelineWindow(100000, -5000, 15000)).toEqual({ start: 0, end: 20000, span: 20000 });
    expect(timelineWindow(100000, 95000, 115000)).toEqual({ start: 80000, end: 100000, span: 20000 });
  });
  it("keeps two seconds available for inspection of 50 Hz samples", () => {
    expect(zoomWindow(100000, 0, 2000, 2, 1000).span).toBe(2000);
  });
  it("preserves speed spikes and dips within a rendered pixel", () => {
    const times = Array.from({ length: 500 }, (_, i) => i * 20);
    const speeds = times.map(() => 20); speeds[201] = 55; speeds[202] = 2;
    const points = speedEnvelope(times, speeds, 0, 10000, 10);
    expect(points).toContain(201); expect(points).toContain(202);
    expect(points.length).toBeLessThan(50); expect(times).toHaveLength(500);
  });
});
