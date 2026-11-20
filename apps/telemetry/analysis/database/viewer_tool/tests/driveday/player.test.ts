import fc from "fast-check";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { convertSession } from "../../src/lib/driveday/convert";
import { Player, RATES, type EndMode } from "../../src/lib/driveday/player";
import { rebuildRun } from "../../src/lib/driveday/rebuild";
import { Session } from "../../src/lib/driveday/session";
import { emptyUserState } from "../../src/lib/driveday/types";
import { defaultUnits } from "../../src/lib/driveday/units";
import { synthSession } from "./fixtures/synth";

/* The Player is the one mutable model behind the screen, so most of these are properties over random sequences of
   what a user can do (seek, play, loop, change rate, step laps, zoom), checked after every step. The browser's frame
   loop and clock are replaced by a hand-cranked one: each frame advances a fake performance.now() and runs the
   requestAnimationFrame callbacks, so playback is deterministic and takes no wall time. */

const SEED = 20261120;
const D = rebuildRun(convertSession(synthSession({ laps: 6, sessionId: "player" }), "player.json")[0].D);

/** A fake frame loop and localStorage, installed for each test. */
let now = 0;
let queue: ((t: number) => void)[] = [];
let store: Map<string, string>;
const frame = (ms = 16) => {
  now += ms;
  const cbs = queue;
  queue = [];
  cbs.forEach((cb) => cb(now));
};
const frames = (n: number, ms = 16) => { for (let i = 0; i < n; i++) frame(ms); };

beforeEach(() => {
  now = 1000;
  queue = [];
  store = new Map();
  vi.stubGlobal("requestAnimationFrame", (cb: (t: number) => void) => { queue.push(cb); return queue.length; });
  vi.spyOn(performance, "now").mockImplementation(() => now);
  vi.stubGlobal("localStorage", {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => { store.set(k, v); },
    removeItem: (k: string) => { store.delete(k); },
  });
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const fresh = () => new Player(new Session(D), emptyUserState(), defaultUnits());
const timed = (p: Player) => p.laps.filter((l) => l.kind === "lap");
const lapWindow = (p: Player, i: number) => [p.laps[i].t0 - p.T0, p.laps[i].t1 - p.T0] as const;

/** One thing a user can do to the player. */
type Op =
  | { k: "seek"; at: number } | { k: "play"; on: boolean } | { k: "run"; frames: number; ms: number }
  | { k: "loopA" } | { k: "loopB" } | { k: "clearLoop" } | { k: "end"; mode: EndMode } | { k: "rate"; d: 1 | -1 }
  | { k: "stepLap"; d: number } | { k: "stepFix"; d: 1 | -1 } | { k: "seekLap"; i: number } | { k: "gaps"; on: boolean }
  | { k: "window"; a: number; b: number } | { k: "zoomTimeline"; f: number };
const op: fc.Arbitrary<Op> = fc.oneof(
  fc.record({ k: fc.constant("seek" as const), at: fc.double({ min: -0.2, max: 1.2, noNaN: true }) }),
  fc.record({ k: fc.constant("play" as const), on: fc.boolean() }),
  fc.record({ k: fc.constant("run" as const), frames: fc.integer({ min: 1, max: 400 }), ms: fc.integer({ min: 1, max: 250 }) }),
  fc.constant({ k: "loopA" as const }), fc.constant({ k: "loopB" as const }), fc.constant({ k: "clearLoop" as const }),
  fc.record({ k: fc.constant("end" as const), mode: fc.constantFrom<EndMode>("go", "stop", "loop") }),
  fc.record({ k: fc.constant("rate" as const), d: fc.constantFrom<1 | -1>(1, -1) }),
  fc.record({ k: fc.constant("stepLap" as const), d: fc.integer({ min: -3, max: 3 }) }),
  fc.record({ k: fc.constant("stepFix" as const), d: fc.constantFrom<1 | -1>(1, -1) }),
  fc.record({ k: fc.constant("seekLap" as const), i: fc.nat(20) }),
  fc.record({ k: fc.constant("gaps" as const), on: fc.boolean() }),
  fc.record({ k: fc.constant("window" as const), a: fc.double({ min: 0, max: 1, noNaN: true }), b: fc.double({ min: 0, max: 1, noNaN: true }) }),
  fc.record({ k: fc.constant("zoomTimeline" as const), f: fc.double({ min: 0.1, max: 10, noNaN: true }) }),
);

function doOp(p: Player, o: Op) {
  switch (o.k) {
    case "seek": return p.setT(o.at * p.TD);
    case "play": return p.play(o.on);
    case "run": return frames(o.frames, o.ms);
    case "loopA": return p.setLoopA();
    case "loopB": return p.setLoopB();
    case "clearLoop": return p.clearLoop();
    case "end": return p.set("endMode", o.mode);
    case "rate": return p.cycleRate(o.d);
    case "stepLap": return p.stepLap(o.d);
    case "stepFix": return p.stepFix(o.d);
    case "seekLap": return p.seekLap(o.i % p.laps.length);
    case "gaps": return p.setShowBetweenLaps(o.on);
    case "window": return p.setTimelineWindow(Math.min(o.a, o.b) * p.timelineDuration, Math.max(o.a, o.b) * p.timelineDuration);
    case "zoomTimeline": return p.zoomTimeline(o.f);
  }
}

describe("the playhead under any sequence of user actions", () => {
  it("stays on the session, on a real lap, at a listed rate, with a loop that runs forwards", () => {
    fc.assert(fc.property(fc.array(op, { minLength: 1, maxLength: 40 }), (ops) => {
      const p = fresh();
      for (const o of ops) {
        doOp(p, o);
        expect(p.T).toBeGreaterThanOrEqual(0);
        expect(p.T).toBeLessThanOrEqual(p.TD);
        expect(Number.isFinite(p.T)).toBe(true);
        expect(p.laps[p.sel]).toBeDefined();
        expect(RATES).toContain(p.rate);
        if (p.loopA != null && p.loopB != null) expect(p.loopA).toBeLessThan(p.loopB);
        const r = p.timelineRange;
        expect(r.start).toBeGreaterThanOrEqual(0);
        expect(r.end).toBeLessThanOrEqual(p.timelineDuration + 1e-6);
        expect(r.end).toBeGreaterThan(r.start);
      }
      p.dispose();
    }), { seed: SEED, numRuns: 150 });
  });

  it("never runs past the end of the lap in stop mode, and keeps looping it in loop mode", () => {
    fc.assert(fc.property(fc.integer({ min: 0, max: 5 }), fc.constantFrom(...RATES), fc.integer({ min: 4, max: 120 }), (k, rate, ms) => {
      const p = fresh();
      const i = p.laps.indexOf(timed(p)[k % timed(p).length]);
      const [a, b] = lapWindow(p, i);
      p.set("rate", rate);
      p.set("endMode", "loop");
      p.seekLap(i);
      frame();
      p.play(true);
      for (let n = 0; n < 600; n++) {
        frame(ms);
        expect(p.T).toBeGreaterThanOrEqual(a);
        expect(p.T).toBeLessThanOrEqual(b);
      }
      expect(p.playing).toBe(true);
      p.set("endMode", "stop");
      frames(Math.ceil((b - a) / (rate * ms)) + 10, ms);
      expect(p.playing).toBe(false);
      expect(p.T).toBe(b - 1);
      p.dispose();
    }), { seed: SEED, numRuns: 20 });
  });

  it("advances by wall time multiplied by the rate", () => {
    for (const rate of RATES) {
      const p = fresh();
      p.setShowBetweenLaps(true);
      p.set("rate", rate);
      const [a] = lapWindow(p, p.laps.indexOf(timed(p)[2]));
      p.setT(a);
      p.play(true);
      frames(10, 20);
      /* the first frame after play() starts the clock, so 10 frames of 20 ms are 200 ms of wall time */
      expect(p.T - a).toBeCloseTo(200 * rate, 6);
      p.dispose();
    }
  });

  it("plays an A to B loop round and round", () => {
    const p = fresh();
    const [a] = lapWindow(p, p.laps.indexOf(timed(p)[1]));
    p.setShowBetweenLaps(true);
    p.setT(a + 2000);
    p.setLoopA();
    p.setT(a + 6000);
    p.setLoopB();
    p.setT(a + 2000);
    p.play(true);
    let wraps = 0, last = p.T;
    for (let n = 0; n < 2000; n++) {
      frame(16);
      if (p.T < last) wraps++;
      last = p.T;
      expect(p.T).toBeGreaterThanOrEqual(a + 2000);
      expect(p.T).toBeLessThanOrEqual(a + 6000);
    }
    expect(wraps).toBeGreaterThanOrEqual(7);
    p.dispose();
  });

  it("stops at the last lap in go mode instead of playing the parking at the end", () => {
    const p = fresh();
    p.seekLap(p.laps.indexOf(timed(p)[timed(p).length - 1]));
    p.play(true);
    frames(5000, 50);
    expect(p.playing).toBe(false);
    expect(p.T).toBeLessThan(p.TD);
    p.dispose();
  });

  it("drops a loop end that would come before its start", () => {
    const p = fresh();
    p.setT(5000); p.setLoopB();
    p.setT(9000); p.setLoopA();
    expect([p.loopA, p.loopB]).toEqual([9000, null]);
    p.setT(3000); p.setLoopB();
    expect([p.loopA, p.loopB]).toEqual([null, 3000]);
    p.dispose();
  });

  it("saturates the rate at both ends", () => {
    const p = fresh();
    for (let i = 0; i < 10; i++) p.cycleRate(1);
    expect(p.rate).toBe(RATES[RATES.length - 1]);
    for (let i = 0; i < 10; i++) p.cycleRate(-1);
    expect(p.rate).toBe(RATES[0]);
    p.dispose();
  });

  it("steps back to the start of this lap first, then to the lap before", () => {
    const p = fresh();
    const i = p.laps.indexOf(timed(p)[3]);
    const [a] = lapWindow(p, i);
    p.setT(a + 5000); frame();
    p.stepLap(-1);
    expect(p.T).toBe(a);
    frame();
    p.stepLap(-1);
    expect(p.T).toBe(lapWindow(p, p.laps.indexOf(timed(p)[2]))[0]);
    p.dispose();
  });

  it("steps one GPS fix at a time, onto a sample", () => {
    const p = fresh();
    const i = p.laps.indexOf(timed(p)[1]);
    const L = p.laps[i];
    p.setT(L.t0 - p.T0 + L.ch.t[5] + 3); frame();
    p.stepFix(1);
    expect(p.T).toBe(L.t0 - p.T0 + L.ch.t[6]);
    p.stepFix(-1); p.stepFix(-1);
    expect(p.T).toBe(L.t0 - p.T0 + L.ch.t[4]);
    p.dispose();
  });
});

describe("the timeline axis", () => {
  it("maps any lap time to the display and back", () => {
    const p = fresh();
    fc.assert(fc.property(fc.nat(5), fc.double({ min: 0, max: 1, noNaN: true }), fc.boolean(), (k, f, gaps) => {
      p.setShowBetweenLaps(gaps);
      const [a, b] = lapWindow(p, p.laps.indexOf(timed(p)[k]));
      const t = a + f * (b - a);
      expect(p.timelineSessionTime(p.timelineTime(t))).toBeCloseTo(t, 6);
    }), { seed: SEED, numRuns: 300 });
    p.dispose();
  });

  it("zooms around the playhead and keeps the playhead in view", () => {
    const p = fresh();
    fc.assert(fc.property(fc.double({ min: 0, max: 1, noNaN: true }), fc.array(fc.double({ min: 0.2, max: 5, noNaN: true }), { maxLength: 12 }), (at, zooms) => {
      p.setTimelineWindow(0, p.timelineDuration);
      p.setT(timed(p)[0].t0 - p.T0 + at * (timed(p)[timed(p).length - 1].t1 - timed(p)[0].t0));
      zooms.forEach((z) => p.zoomTimeline(z));
      const x = p.timelineTime(p.T), r = p.timelineRange;
      expect(x).toBeGreaterThanOrEqual(r.start - 1e-6);
      expect(x).toBeLessThanOrEqual(r.end + 1e-6);
    }), { seed: SEED, numRuns: 200 });
    p.dispose();
  });
});

describe("the camera", () => {
  it("fits the whole track inside the padded canvas at any size", () => {
    const p = fresh();
    fc.assert(fc.property(fc.integer({ min: 2 * Player.PADX + 20, max: 4000 }), fc.integer({ min: Player.PADT + Player.PADB + 20, max: 3000 }), (W, H) => {
      p.view = p.centre;
      p.fit(W, H);
      const { x, y } = p.session.bbox;
      for (const X of [p.X(x[0]), p.X(x[1])]) { expect(X).toBeGreaterThanOrEqual(Player.PADX - 1e-6); expect(X).toBeLessThanOrEqual(W - Player.PADX + 1e-6); }
      for (const Y of [p.Y(y[0]), p.Y(y[1])]) { expect(Y).toBeGreaterThanOrEqual(Player.PADT - 1e-6); expect(Y).toBeLessThanOrEqual(H - Player.PADB + 1e-6); }
    }), { seed: SEED, numRuns: 300 });
    p.dispose();
  });

  it("keeps a positive scale when the pane is smaller than its padding", () => {
    const p = fresh();
    for (const [W, H] of [[40, 40], [1, 1], [80, 500], [500, 60]]) {
      p.fit(W, H);
      expect(p.sc).toBeGreaterThan(0);
      /* north stays up and east stays right */
      expect(p.X(p.session.bbox.x[1])).toBeGreaterThan(p.X(p.session.bbox.x[0]));
      expect(p.Y(p.session.bbox.y[1])).toBeLessThan(p.Y(p.session.bbox.y[0]));
    }
    p.dispose();
  });

  it("flies to any target and lands on it exactly, with the zoom held between 1 and 14", () => {
    const p = fresh();
    p.fit(900, 600);
    fc.assert(fc.property(fc.double({ min: -500, max: 500, noNaN: true }), fc.double({ min: -500, max: 500, noNaN: true }), fc.double({ min: -5, max: 50, noNaN: true }), fc.integer({ min: 50, max: 2000 }), (cx, cy, z, dur) => {
      p.flyTo(cx, cy, z, dur);
      for (let n = 0; p.anim && n < 1000; n++) {
        frame(16);
        expect(p.view.z).toBeGreaterThanOrEqual(1);
        expect([p.view.cx, p.view.cy, p.view.z].every(Number.isFinite)).toBe(true);
      }
      expect(p.anim).toBeNull();
      expect(p.view).toEqual({ cx, cy, z: Math.max(1, Math.min(14, z)) });
    }), { seed: SEED, numRuns: 100 });
    p.dispose();
  });

  it("follows the car until the user takes the camera", () => {
    const p = fresh();
    p.fit(900, 600);
    p.setT(lapWindow(p, p.laps.indexOf(timed(p)[2]))[0] + 3000);
    p.setFollow(true);
    frames(200);
    const { L, rel } = p.curState();
    const car = p.replayPosition(L, rel);
    expect(Math.hypot(p.view.cx - car[0], p.view.cy - car[1])).toBeLessThan(0.5);
    expect(p.view.z).toBeCloseTo(p.followZ, 1);
    const v = p.ui.getVersion();
    p.takeCamera();
    expect(p.follow).toBe(false);
    expect(p.ui.getVersion()).toBe(v + 1);
    p.dispose();
  });

  it("zooms onto a corner and seeks the playhead to just before it", () => {
    const p = fresh();
    p.fit(900, 600);
    frame();
    const i = p.session.segs.findIndex((s) => s.type === "c");
    p.focusSeg(i);
    frames(100);
    expect(p.focus?.name).toBe(p.session.segs[i].name);
    expect(p.view.z).toBeGreaterThan(1);
    p.flyFit();
    frames(100);
    expect(p.view).toEqual(p.centre);
    p.dispose();
  });
});

describe("what the user changes survives a reload", () => {
  it("keeps the playhead, preferences, base lap and sector gates", () => {
    const p = fresh();
    p.setT(12345.4);
    vi.advanceTimersByTime(600);
    p.set("mode", "pedal");
    p.set("rate", 2);
    p.set("endMode", "loop");
    p.set("baseIdx", 3);
    const L = p.laps[p.laps.indexOf(timed(p)[1])];
    p.setT(L.t0 - p.T0 + L.dur * 0.3); frame();
    p.setSectorAtPlayhead(0);
    const gates = [...p.session.gates];
    p.dispose();

    const q = new Player(new Session(D), emptyUserState(), defaultUnits(), JSON.parse(store.get("driveday:prefs")!));
    expect(q.mode).toBe("pedal");
    expect(q.rate).toBe(2);
    expect(q.endMode).toBe("loop");
    expect(q.baseIdx).toBe(3);
    expect(q.sectorMethod).toBe("manual");
    q.session.gates.forEach((g, i) => expect(g).toBeCloseTo(gates[i], 6));
    q.dispose();
  });

  it("saves the playhead half a second after the last seek, not on every frame", () => {
    const p = fresh();
    for (let i = 0; i < 20; i++) p.setT(1000 + i * 100);
    expect(store.has("driveday:pos:player")).toBe(false);
    vi.advanceTimersByTime(499);
    expect(store.has("driveday:pos:player")).toBe(false);
    vi.advanceTimersByTime(1);
    expect(store.get("driveday:pos:player")).toBe("2900");
    p.dispose();
  });

  it("ignores a saved playhead that is off the session, and saved gates that are out of order", () => {
    store.set("driveday:pos:player", "999999999");
    store.set("driveday:sectors:" + D.meta.trackId, JSON.stringify({ gates: [0.9, 0.2], method: "manual" }));
    const p = fresh();
    expect(p.T).toBe(p.session.base.t0 - p.T0);
    expect(p.sectorMethod).toBe("auto");
    p.dispose();
  });

  it("still works when the browser refuses storage", () => {
    vi.stubGlobal("localStorage", { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("full"); } });
    const p = fresh();
    expect(() => { p.set("mode", "acc"); p.setT(100); vi.advanceTimersByTime(600); }).not.toThrow();
    p.dispose();
  });

  it("refuses a sector gate too close to the line or to the other gate", () => {
    const p = fresh();
    const toasts: string[] = [];
    p.toast = (m) => toasts.push(m);
    const L = p.laps[p.laps.indexOf(timed(p)[1])];
    const before = [...p.session.gates];
    p.setT(L.t0 - p.T0 + 1); frame();
    p.setSectorAtPlayhead(0);
    expect(p.session.gates).toEqual(before);
    expect(toasts.at(-1)).toMatch(/at least 5 m apart/);
    p.dispose();
  });
});

describe("change notification", () => {
  it("bumps the UI feed once per change and the frame feed once per painted frame", () => {
    const p = fresh();
    const ui = vi.fn(), fr = vi.fn();
    p.ui.subscribe(ui);
    const off = p.frame.subscribe(fr);
    frame();
    ui.mockClear(); fr.mockClear();
    p.set("showSeg", true);
    expect(ui).toHaveBeenCalledTimes(1);
    frame();
    expect(fr).toHaveBeenCalledTimes(1);
    off();
    frame(); p.setT(5000); frame();
    expect(fr).toHaveBeenCalledTimes(1);
    p.dispose();
  });

  it("repaints static painters only when something other than the clock changes", () => {
    const p = fresh();
    const live = vi.fn(), still = vi.fn();
    p.addPainter("live", live, true);
    p.addPainter("still", still, false);
    const [a] = lapWindow(p, p.laps.indexOf(timed(p)[2]));
    p.setT(a + 1000);
    frame();
    live.mockClear(); still.mockClear();
    /* the clock moves within the same lap: only live painters follow it (a new lap repaints everything) */
    p.setT(a + 2000);
    frame();
    expect(live).toHaveBeenCalledTimes(1);
    expect(still).not.toHaveBeenCalled();
    p.set("mode", "acc");
    frame();
    expect(still).toHaveBeenCalledTimes(1);
    p.dispose();
  });

  it("paints nothing after it is disposed", () => {
    const p = fresh();
    const fn = vi.fn();
    p.addPainter("map", fn, true);
    p.dispose();
    fn.mockClear();
    p.setT(100);
    frames(5);
    p.runFrame();
    expect(fn).not.toHaveBeenCalled();
  });
});
