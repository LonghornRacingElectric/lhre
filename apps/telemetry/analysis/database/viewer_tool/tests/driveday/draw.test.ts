import fc from "fast-check";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { CHARTS } from "../../src/components/driveday/panels/ChartPanels";
import { convertSession } from "../../src/lib/driveday/convert";
import { cellStats, drawCells } from "../../src/lib/driveday/draw/cells";
import { barChart, drawEnergy } from "../../src/lib/driveday/draw/charts";
import { drawMap } from "../../src/lib/driveday/draw/map";
import { heat, invalidatePalette, palette, ramp, rampSpd } from "../../src/lib/driveday/draw/palette";
import { drawTimeline } from "../../src/lib/driveday/draw/timeline";
import { drawTrace, TRACES } from "../../src/lib/driveday/draw/traces";
import { energyModel } from "../../src/lib/driveday/energy";
import { Player, type Cmp, type Mode } from "../../src/lib/driveday/player";
import { rebuildRun } from "../../src/lib/driveday/rebuild";
import { Session } from "../../src/lib/driveday/session";
import { emptyUserState } from "../../src/lib/driveday/types";
import { defaultUnits } from "../../src/lib/driveday/units";
import { recordingCanvas, stubBrowser } from "./fixtures/canvas";
import { synthSession } from "./fixtures/synth";

/* Every canvas the viewer paints, drawn over many viewer states into a canvas that checks each call (fixtures/canvas.ts).
   A state is a property-test case: fast-check picks the colour mode, ghost, overlays, zoom, playhead and canvas size,
   with a fixed seed so a failure reproduces. The rules are the same for every painter: finite numbers only, colours
   and labels never built from NaN or undefined, and save() and restore() balanced. */

const SEED = 20261120;
const RUNS = 60;

const player = (laps: number, id: string) => {
  const D = rebuildRun(convertSession(synthSession({ laps, sessionId: id }), id + ".json")[0].D);
  return new Player(new Session(D), emptyUserState(), defaultUnits());
};

let p8: Player, p1: Player, rival: Player;
beforeAll(() => {
  stubBrowser();
  p8 = player(8, "draw-eight");
  p1 = player(1, "draw-one");
  rival = player(3, "draw-rival");
  /* marks the painters must place: a timed cone, a note and a review marker */
  const t = p8.laps[3].t0 - p8.T0 + 4000;
  p8.user.ce.push({ id: "cone", t });
  p8.user.marks.push({ id: "note", t: t + 2000, text: "Brake later", category: "braking" });
  p8.reviewMarkers = [{ point: p8.laps[3].pts[10] as [number, number], label: "Apex", color: "#fff" }];
});
afterAll(() => {
  [p8, p1, rival].forEach((p) => p.dispose());
  vi.unstubAllGlobals();
});
beforeEach(() => invalidatePalette());

const MODES: Mode[] = ["speed", "pedal", "acc", "gain", "sec"];
const CMPS: Cmp[] = ["best", "theo", "lap", "external", "none"];

/** One viewer state, applied to a player. */
const state = fc.record({
  mode: fc.constantFrom(...MODES),
  cmp: fc.constantFrom(...CMPS),
  ghostLap: fc.nat(10),
  ghostAll: fc.boolean(),
  showSeg: fc.boolean(),
  showCones: fc.boolean(),
  raceMode: fc.boolean(),
  consMode: fc.boolean(),
  layoutOnly: fc.boolean(),
  follow: fc.boolean(),
  zoom: fc.double({ min: 1, max: 14, noNaN: true }),
  at: fc.double({ min: 0, max: 1, noNaN: true }),
  w: fc.integer({ min: 40, max: 2400 }),
  h: fc.integer({ min: 40, max: 1400 }),
});
type State = typeof state extends fc.Arbitrary<infer S> ? S : never;

function apply(p: Player, s: State) {
  p.mode = s.mode;
  p.cmp = s.cmp;
  if (s.cmp === "external") p.setExternalGhost(rival.laps[1], "rival");
  p.cmp = s.cmp;
  p.ghostLapI = s.ghostLap % p.laps.length;
  Object.assign(p, { ghostAll: s.ghostAll, showSeg: s.showSeg, showCones: s.showCones, raceMode: s.raceMode, consMode: s.consMode, layoutOnly: s.layoutOnly, follow: s.follow });
  p.view.z = s.zoom;
  p.T = s.at * p.TD;
  p.sel = p.session.lapIndexAt(p.T);
}

const clean = (rec: { problems: string[]; depth: number }) => {
  expect(rec.problems).toEqual([]);
  expect(rec.depth).toBe(0);
};

describe("the map", () => {
  it("draws every viewer state with finite paths, real colours and balanced save/restore", () => {
    fc.assert(fc.property(state, fc.boolean(), (s, one) => {
      const p = one ? p1 : p8;
      apply(p, s);
      const { canvas, rec } = recordingCanvas(s.w, s.h);
      drawMap(p, canvas);
      clean(rec);
      expect(rec.calls.some((c) => c.name === "stroke")).toBe(true);
      /* the click targets it leaves behind are on the screen it drew */
      p.hits.forEach((hit) => expect([hit.x, hit.y, hit.r].every(Number.isFinite)).toBe(true));
    }), { seed: SEED, numRuns: RUNS });
  });

  it("sizes the backing store to the device pixel ratio", () => {
    const { canvas } = recordingCanvas(300, 200);
    drawMap(p8, canvas);
    expect([canvas.width, canvas.height]).toEqual([600, 400]);
  });

  it("draws nothing into a canvas with no size, such as a hidden pane", () => {
    const { canvas, rec } = recordingCanvas(0, 0);
    drawMap(p8, canvas);
    expect(rec.calls).toEqual([]);
  });
});

describe("the timeline", () => {
  it("draws any zoom window, with or without the gaps between laps", () => {
    fc.assert(fc.property(state, fc.boolean(), fc.double({ min: 0, max: 1, noNaN: true }), fc.double({ min: 0, max: 1, noNaN: true }), (s, gaps, a, b) => {
      apply(p8, s);
      p8.showBetweenLaps = gaps;
      p8.setTimelineWindow(Math.min(a, b) * p8.timelineDuration, Math.max(a, b) * p8.timelineDuration);
      p8.hoverT = s.follow ? p8.T : null;
      const { canvas, rec } = recordingCanvas(s.w, Math.min(s.h, 220));
      drawTimeline(p8, canvas);
      clean(rec);
    }), { seed: SEED, numRuns: RUNS });
    p8.showBetweenLaps = false;
    p8.setTimelineWindow(0, p8.timelineDuration);
  });
});

describe("the trace stack", () => {
  it.each(TRACES.map((t) => [t.key, t] as const))("draws %s against every ghost", (_, def) => {
    fc.assert(fc.property(state, (s) => {
      apply(p8, s);
      const { canvas, rec } = recordingCanvas(s.w, def.h);
      drawTrace(p8, canvas, def);
      clean(rec);
      expect(rec.text.some((t) => t.length > 0)).toBe(true);
    }), { seed: SEED, numRuns: 25 });
  });

  it("draws the time delta flat when there is no ghost to compare with", () => {
    apply(p8, { ...fc.sample(state, { seed: SEED, numRuns: 1 })[0], cmp: "none" });
    const { canvas, rec } = recordingCanvas(800, 112);
    drawTrace(p8, canvas, TRACES.find((t) => t.key === "dlt")!);
    clean(rec);
  });
});

describe("the side panel charts", () => {
  it.each(CHARTS.map((c) => [c.id, c] as const))("draws the %s chart at any size and playhead", (_, def) => {
    fc.assert(fc.property(state, fc.boolean(), (s, one) => {
      const p = one ? p1 : p8;
      apply(p, s);
      const { canvas, rec } = recordingCanvas(s.w, Math.min(s.h, 400));
      def.draw(p, canvas);
      clean(rec);
    }), { seed: SEED, numRuns: 25 });
  });

  it("draws the energy projection, with and without a plan", () => {
    for (const setup of [{}, { planLaps: 22, reserve: 20, cap: 6500 }]) {
      const user = { ...emptyUserState(), setup };
      const m = energyModel(p8.session, user);
      const { canvas, rec } = recordingCanvas(600, 220);
      drawEnergy(p8, canvas, m);
      clean(rec);
    }
  });

  it("draws bars that are all missing, all negative, or a single lap", () => {
    for (const vals of [[NaN, NaN, NaN], [-3, -1, -2], [4]]) {
      const { canvas, rec } = recordingCanvas(500, 200);
      barChart(canvas, { n: vals.length, series: [{ col: "blue", vals }], selected: 0, fmt: String });
      clean(rec);
    }
  });

  it("draws the cell grid in both modes, hovered or not", () => {
    const st = cellStats(p8);
    fc.assert(fc.property(state, fc.constantFrom("abs" as const, "rise" as const), fc.integer({ min: -1, max: 91 }), (s, mode, hover) => {
      apply(p8, s);
      const { canvas, rec } = recordingCanvas(Math.max(200, s.w), 160);
      drawCells(p8, canvas, st, mode, hover);
      clean(rec);
    }), { seed: SEED, numRuns: RUNS });
  });
});

describe("colour ramps", () => {
  const RGB = /^rgb\((\d{1,3}),(\d{1,3}),(\d{1,3})\)$/;
  const HSL = /^hsl\((\d+) 78% 46%\)$/;
  it("turn any number, even out of range or not a number, into a real colour", () => {
    fc.assert(fc.property(fc.double(), fc.double({ noNaN: true }), fc.double({ noNaN: true }), (v, lo, hi) => {
      for (const c of [ramp(v), rampSpd(v)]) {
        expect(c).toMatch(RGB);
        c.match(RGB)!.slice(1).forEach((n) => expect(+n).toBeLessThanOrEqual(255));
      }
      /* heat runs from blue (205) to red (5) */
      const hue = +heat(v, Math.min(lo, hi), Math.max(lo, hi)).match(HSL)![1];
      expect(hue).toBeGreaterThanOrEqual(5);
      expect(hue).toBeLessThanOrEqual(205);
    }), { seed: SEED, numRuns: 500 });
  });

  it("show every cell cool when they all read the same", () => {
    expect(heat(31, 31, 31)).toBe(heat(0, 10, 20));
  });

  it("run from the low colour to the high one", () => {
    expect(ramp(-1)).toBe(ramp(0));
    expect(ramp(2)).toBe(ramp(1));
    expect(ramp(0)).not.toBe(ramp(1));
  });

  it("read the page theme once and cache it until the theme changes", () => {
    const spy = vi.fn(() => ({ getPropertyValue: () => "#123456" }));
    vi.stubGlobal("getComputedStyle", spy);
    invalidatePalette();
    palette();
    palette();
    expect(spy).toHaveBeenCalledTimes(1);
    expect(palette().sector).toEqual(["#123456", "#123456", "#123456"]);
    stubBrowser();
  });
});
