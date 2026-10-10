import { describe, expect, it } from "vitest";
import { Analysis, gpsStats } from "../src/lib/analysis";
import { convertSession } from "../src/lib/convert";
import { buildDebrief, lapsCsv } from "../src/lib/debrief";
import { energyModel } from "../src/lib/energy";
import { Session } from "../src/lib/session";
import { defaultUnits } from "../src/lib/units";
import { emptyUserState } from "../src/lib/types";
import { synthSession } from "./fixtures/synth";

const s = new Session(convertSession(synthSession({ laps: 8 }), "synth.json", []).D);
const units = defaultUnits();

describe("Analysis", () => {
  it("finds the best lap and a theoretical best no slower than it", () => {
    const a = new Analysis(s, emptyUserState());
    expect(a.bestLap).toBe(Math.min(...s.runs.map((l) => l.dur)));
    expect(a.theo).toBeLessThanOrEqual(a.bestLap + 1);
  });

  it("leaves skipped laps out of the bests", () => {
    const u = emptyUserState();
    const fastest = s.runs.reduce((p, l) => (l.dur < p.dur ? l : p));
    u.flags[String(s.lapNo(fastest))] = { x: 1 };
    const a = new Analysis(s, u);
    expect(a.bestLap).toBeGreaterThan(fastest.dur);
    expect(a.valid(fastest)).toBe(false);
  });

  it("counts a cone against the lap its moment falls in, and adds the penalty", () => {
    const u = emptyUserState();
    const lap = s.runs[2];
    u.ce.push({ id: "a", t: lap.t0 - s.T0 + 5000 });
    u.ce.push({ id: "b", t: lap.t0 - s.T0 + 9000 });
    u.cones["4"] = 1; // an untimed cone on lap 4
    const a = new Analysis(s, u);
    expect(a.conesOf(lap)).toBe(2);
    expect(a.conesOf(s.runs[0])).toBe(0);
    expect(a.conesOf(s.runs[3])).toBe(1);
    expect(a.adj(lap)).toBe(lap.dur + 2 * 2 * 1000);
    u.setup.pen = 3;
    a.recompute(u);
    expect(a.adj(lap)).toBe(lap.dur + 2 * 3 * 1000);
  });

  it("stitches a best-sectors lap as long as the sum of the best sectors", () => {
    const a = new Analysis(s, emptyUserState());
    const theo = a.theoLap();
    expect(theo).not.toBeNull();
    expect(Math.abs(theo!.dur - a.theo)).toBeLessThan(50);
    expect(theo!.ch.t.every((v, i, arr) => i === 0 || v >= arr[i - 1])).toBe(true);
  });

  it("measures how repeatable the brake points are", () => {
    const a = new Analysis(s, emptyUserState());
    expect(a.cons.bstd.length).toBe(s.zones.length);
    a.cons.bstd.forEach((v) => expect(v).toBeLessThan(15));
  });

  it("raises an alert only where a limit set in Setup was crossed", () => {
    const u = emptyUserState();
    u.setup.aMotT = 1000;
    expect(new Analysis(s, u).alerts.length).toBe(0);
    u.setup.aMotT = 10;
    const a = new Analysis(s, u);
    expect(a.alerts.length).toBeGreaterThan(0);
    expect(a.alerts[0].d.label).toBe("Motor");
  });

  it("gives the gain against a ghost, zero-sum over a lap against itself", () => {
    const a = new Analysis(s, emptyUserState());
    const g = a.gainFor(s.runs[3], s.runs[1], "x");
    expect(g).not.toBeNull();
    const total = g!.reduce((p, v) => p + v, 0);
    expect(Math.abs(total - (s.runs[3].dur - s.runs[1].dur))).toBeLessThan(1500);
    expect(a.gainFor(s.runs[3], s.runs[3], "x")).toBeNull();
  });
});

describe("run summaries", () => {
  const a = new Analysis(s, emptyUserState());
  it("writes a debrief with a pace line", () => {
    const d = buildDebrief(a, units);
    expect(d[0].t).toMatch(/^Pace: best/);
    expect(d.every((x) => x.t.length > 5)).toBe(true);
  });
  it("exports one CSV row per lap", () => {
    const rows = lapsCsv(a, units).split("\n");
    expect(rows.length).toBe(s.runs.length + 1);
    expect(rows[0]).toMatch(/^lap,real_s/);
  });
  it("reports the fix rate", () => {
    const g = gpsStats(s);
    expect(g.hz).toBeGreaterThan(1.5);
    expect(g.hz).toBeLessThan(2.1);
    expect(g.gaps).toBe(0);
  });
  it("runs the energy model without error", () => {
    const m = energyModel(s, emptyUserState());
    expect(m.socNow).toBeGreaterThan(0);
    expect(m.pace).toBeGreaterThan(0);
  });
});
