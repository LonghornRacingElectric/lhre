import { describe, expect, it } from "vitest";
import { convertSession } from "../src/lib/convert";
import { rebuildRun } from "../src/lib/rebuild";
import { synthSession } from "./fixtures/synth";

const J = synthSession({ laps: 8 });
const D0 = convertSession(J, "synth.json", []).D;
const sum = (a: number[]) => a.reduce((x, y) => x + y, 0);
const timed = (D: typeof D0) => D.laps.filter((l) => l.kind === "lap");

describe("rebuildRun with the logger's marks", () => {
  const D = rebuildRun(D0, 0);
  it("keeps every lap time", () => {
    expect(timed(D).map((l) => l.dur)).toEqual(timed(D0).map((l) => l.dur));
  });
  it("makes each lap end exactly where the next begins", () => {
    const t = timed(D);
    for (let i = 0; i < t.length - 1; i++) {
      const a = t[i].pts[t[i].pts.length - 1];
      const b = t[i + 1].pts[0];
      expect(Math.hypot(a[0] - b[0], a[1] - b[1])).toBeLessThan(0.01);
    }
  });
  it("keeps the per-lap energy to within a few percent", () => {
    timed(D).forEach((l, i) => {
      /* the gap between the last fix of one lap and the first of the next is shared out, so not exact to the watt-hour */
      expect(Math.abs((l.wh ?? 0) - J.laps[i].energyWh) / J.laps[i].energyWh).toBeLessThan(0.05);
    });
  });
  it("starts and ends each lap on a sample time", () => {
    timed(D).forEach((l) => {
      expect(l.ch.t[0]).toBe(0);
      expect(l.ch.t[l.ch.t.length - 1]).toBe(Math.round(l.dur));
    });
  });
});

describe("rebuildRun with a moved start/finish line", () => {
  const shift = 40;
  const D = rebuildRun(D0, shift);
  const t = timed(D);
  it("cuts the same number of laps at the new line", () => {
    expect(t.length).toBe(8);
    expect(D.meta.sf).toBe(shift);
    expect(D.warnings.some((w) => /moved/.test(w))).toBe(true);
  });
  it("starts each lap 40 m along the loop from where it did", () => {
    t.forEach((l) => expect(l.ch.u[0]).toBeLessThan(2));
  });
  it("keeps the total time and roughly the energy", () => {
    const orig = sum(timed(D0).map((l) => l.dur));
    const now = sum(t.map((l) => l.dur));
    expect(Math.abs(now - orig)).toBeLessThan(2500);
    const e0 = sum(timed(D0).map((l) => l.wh ?? 0));
    const e1 = sum(t.map((l) => l.wh ?? 0));
    expect(Math.abs(e1 - e0) / e0).toBeLessThan(0.1);
  });
  it("has no jump between laps", () => {
    for (let i = 0; i < t.length - 1; i++) {
      const a = t[i].pts[t[i].pts.length - 1];
      const b = t[i + 1].pts[0];
      expect(Math.hypot(a[0] - b[0], a[1] - b[1])).toBeLessThan(0.01);
    }
  });
  it("still splits laps into three sectors that add up", () => {
    t.forEach((l) => expect((l.sec as number[]).reduce((a, b) => a + b, 0)).toBeCloseTo(l.dur, 0));
  });
  it("leaves the loop the same length", () => {
    expect(Math.abs(D.RL - D0.RL)).toBeLessThan(0.1);
  });
});
