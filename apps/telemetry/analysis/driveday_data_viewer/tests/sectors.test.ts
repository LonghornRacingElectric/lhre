import { describe, expect, it } from "vitest";
import { sectorsOf } from "../src/lib/sectors";
import { convertSession } from "../src/lib/convert";
import { rebuildRun } from "../src/lib/rebuild";
import { Analysis } from "../src/lib/analysis";
import { Session } from "../src/lib/session";
import { emptyUserState } from "../src/lib/types";
import { synthSession } from "./fixtures/synth";

describe("fixed distance sectors", () => {
  it("uses distance gates with unequal times and interpolates sparse fixes", () => {
    expect(sectorsOf([0, 50, 150, 250, 300], [0, 1000, 5000, 13000, 15000], 300, 15000, true)).toEqual([3000, 6000, 6000]);
  });
  it("never invents equal-time splits when a crossing is missing", () => {
    expect(sectorsOf([0, 50, 150], [0, 1000, 5000], 300, 9000, true)).toEqual([3000, null, null]);
    expect(sectorsOf([150, 250], [0, 4000], 300, 9000, true)).toEqual([null, null, 7000]);
  });
  it("accepts a fix exactly at a gate and leaves an unfinished S3 unavailable", () => {
    expect(sectorsOf([100, 200, 250], [0, 4000, 6000], 300, 6000, false)).toEqual([0, 4000, null]);
  });
  it("recalculates previously estimated splits in stored open runs", () => {
    const D = convertSession(synthSession({ laps: 3 }), "test.json")[0].D;
    D.open = true;
    D.laps[1].ch.u = D.laps[1].ch.u.map(() => 0);
    D.laps[1].sec = [1, 1, 1];
    expect(rebuildRun(D).laps[1].sec).toEqual([null, null, null]);
  });
  it("ignores missing splits for bests and disables incomplete theoretical ghosts", () => {
    const D = convertSession(synthSession({ laps: 3 }), "test.json")[0].D;
    const s = new Session(D);
    s.runs[0].sec = [null, null, null];
    const a = new Analysis(s, emptyUserState());
    expect(a.best.every(Number.isFinite)).toBe(true);
    expect(a.bestSec.every((b) => b.lap !== 1)).toBe(true);
    s.runs.forEach((l) => { l.sec[1] = null; });
    a.recompute();
    expect(Number.isFinite(a.theo)).toBe(false);
    expect(a.theoLap()).toBeNull();
  });
});
