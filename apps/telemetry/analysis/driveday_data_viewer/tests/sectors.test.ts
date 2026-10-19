import { describe, expect, it } from "vitest";
import { Analysis } from "../src/lib/analysis";
import { convertSession } from "../src/lib/convert";
import { posAt } from "../src/lib/geometry";
import { Player } from "../src/lib/player";
import { rebuildRun } from "../src/lib/rebuild";
import { elapsedAtDistance, sectorTimes, suggestGates, validGates } from "../src/lib/sectors";
import { Session } from "../src/lib/session";
import { emptyUserState, type Lap } from "../src/lib/types";
import { defaultUnits } from "../src/lib/units";
import { synthSession } from "./fixtures/synth";

describe("useful sector boundaries", () => {
  const lap = { ch: { u: [0, 10, 30, 50, 70, 90, 100], t: [0, 1000, 2000, 3000, 4000, 5000, 6000] }, dur: 6000, kind: "lap" } as Lap;
  it("snaps balanced-time targets to nearby straight boundaries", () => {
    expect(suggestGates(lap, [{ a: 32, len: 36, type: "s", dir: 0, name: "Straight" }], 100)).toEqual([32, 68]);
    expect(suggestGates(lap, [], 100)).toEqual([30, 70]);
  });
  it("interpolates custom crossings and preserves the logged lap time", () => {
    expect(sectorTimes(lap, [32, 68])).toEqual([2100, 1800, 2100]);
    expect(validGates([68, 32], 100)).toBe(false);
    expect(validGates([0, 68], 100)).toBe(false);
  });
  it("does not invent timings for missing crossings or an unfinished final sector", () => {
    const short = { ...lap, ch: { u: [0, 40, 60], t: [0, 2000, 3000] } };
    expect(sectorTimes(short, [20, 80])).toEqual([1000, null, null]);
    expect(sectorTimes({ ...lap, kind: "partial" }, [32, 68])).toEqual([2100, 1800, null]);
  });
  it("updates every lap and the stitched best sectors with the same gates", () => {
    const s = new Session(rebuildRun(convertSession(synthSession({ laps: 4 }), "sectors.json")[0].D));
    expect(s.setGates([s.RL * 0.2, s.RL * 0.75])).toBe(true);
    s.runs.forEach((l) => {
      expect(l.sec).toEqual(sectorTimes(l, s.gates));
      expect(l.sec.reduce<number>((sum, v) => sum + (v ?? 0), 0)).toBeCloseTo(l.dur, 5);
    });
    const a = new Analysis(s, emptyUserState());
    expect(a.theoLap()!.dur).toBeCloseTo(a.theo, 5);
  });
  it("excludes missing crossings from sector bests", () => {
    const s = new Session(rebuildRun(convertSession(synthSession({ laps: 4 }), "missing.json")[0].D));
    s.runs[0].sec = [null, null, null];
    const a = new Analysis(s, emptyUserState());
    expect(a.best.every((v) => Number.isFinite(v) && v > 0)).toBe(true);
    expect(a.bestSec.every((v) => v.lap !== 1)).toBe(true);
  });
});

describe("logged ghost timing", () => {
  const s = new Session(rebuildRun(convertSession(synthSession({ laps: 4 }), "ghost.json")[0].D));
  const p = new Player(s, emptyUserState(), defaultUnits());
  const best = p.analysis.bestRun;
  const slower = s.runs.find((l) => l.dur > best.dur + 100)!;
  it("starts both laps together and finishes the faster lap first", () => {
    expect(p.replayPosition(best, 0)).toEqual(posAt(best, 0));
    expect(p.replayPosition(slower, 0)).toEqual(posAt(slower, 0));
    const finish = posAt(best, best.dur);
    expect(p.replayPosition(best, best.dur)).toEqual(finish);
    expect(p.replayPosition(best, slower.dur)).toEqual(finish);
    expect(p.replayPosition(slower, best.dur)).toEqual(posAt(slower, best.dur));
  });
  it("uses elapsed logged time directly despite a shifted GPS zero crossing", () => {
    const shifted = { ...best, ch: { ...best.ch, u: best.ch.u.map((u) => u - 10) } };
    expect(p.replayPosition(shifted, 1000)).toEqual(posAt(shifted, 1000));
    expect(elapsedAtDistance(shifted, 0, s.RL)).toBe(0);
    expect(elapsedAtDistance(shifted, s.RL, s.RL)).toBe(best.dur);
  });
  it("keeps out-lap and unfinished-lap endpoints at their recorded positions", () => {
    const out = s.laps.find((l) => l.kind === "out")!;
    const partial = s.laps.find((l) => l.kind === "partial")!;
    expect(p.replayPosition(out, 0)).toEqual(posAt(out, 0));
    expect(p.replayPosition(partial, partial.dur)).toEqual(posAt(partial, partial.dur));
  });
  it("never teleports a completed lap from an off-track manual endpoint to the reference finish", () => {
    const pts = best.pts.map((point) => [...point] as typeof point);
    pts[pts.length - 1] = [999, 999, ...pts[pts.length - 1].slice(2)] as typeof pts[number];
    const offTrack = { ...best, pts, playbackPts: pts };
    expect(p.replayPosition(offTrack, offTrack.dur)).toEqual([999, 999]);
  });
});
