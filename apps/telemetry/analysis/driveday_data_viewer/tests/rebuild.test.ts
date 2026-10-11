import { describe, expect, it } from "vitest";
import { convertSession } from "../src/lib/convert";
import { rebuildRun } from "../src/lib/rebuild";
import { synthSession } from "./fixtures/synth";

const J = synthSession({ laps: 8 });
const D0 = convertSession(J, "synth.json", [])[0].D;
const timed = (D: typeof D0) => D.laps.filter((l) => l.kind === "lap");

describe("rebuildRun with the logger's marks", () => {
  const D = rebuildRun(D0);
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
