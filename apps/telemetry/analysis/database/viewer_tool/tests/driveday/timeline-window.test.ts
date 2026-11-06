import { describe, expect, it } from "vitest";
import { lapTimelineAxis, timelineWindow, zoomWindow, speedEnvelope } from "../../src/lib/driveday/timeline-window";

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

describe("lap-only timeline axis", () => {
  const laps = [{start:10000,end:20000},{start:40000,end:55000}];
  it("removes waiting time and maps scrub positions back to source timestamps", () => {
    const axis = lapTimelineAxis(80000,laps,false);
    expect(axis.duration).toBe(25000);
    expect(axis.toDisplay(45000)).toBe(15000);
    expect(axis.toSession(15000)).toBe(45000);
    expect(axis.toSession(10000)).toBe(40000);
    expect(axis.toDisplay(30000)).toBe(10000);
    expect(axis.includes(30000)).toBe(false);
    expect(axis.toSession(999999)).toBe(55000);
    expect(axis.toSession(-100)).toBe(10000);
  });
  it("preserves the full session when enabled or no laps exist", () => {
    for (const axis of [lapTimelineAxis(80000,laps,true),lapTimelineAxis(80000,[],false)]) {
      expect(axis.duration).toBe(80000);expect(axis.toSession(30000)).toBe(30000);expect(axis.toDisplay(30000)).toBe(30000);expect(axis.includes(30000)).toBe(true);
    }
  });
  it("merges overlapping intervals without dropping a sample interval twice", () => {
    const axis=lapTimelineAxis(50000,[{start:10000,end:30000},{start:20000,end:40000}],false);
    expect(axis.duration).toBe(30000);expect(axis.toSession(25000)).toBe(35000);
  });
});
