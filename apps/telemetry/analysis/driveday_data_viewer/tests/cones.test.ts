import { describe, expect, it } from "vitest";
import { Analysis } from "../src/lib/analysis";
import { convertSession } from "../src/lib/convert";
import { Player } from "../src/lib/player";
import { rebuildRun } from "../src/lib/rebuild";
import { Session } from "../src/lib/session";
import { seedCones } from "../src/lib/storage";
import { emptyUserState } from "../src/lib/types";
import { defaultUnits } from "../src/lib/units";
import { synthSession } from "./fixtures/synth";

describe("exported cone hits", () => {
  const j = synthSession({ laps: 3 });
  const t = j.laps[0].startMs + 5000;
  j.flags = [{ t, label: "Hit Cone", saved: false }, { t, label: "Hit Cone" },
    { t: t + 500, label: "Off-track" }, { t: j.laps[0].endMs, label: "Cone" },
    { t: j.laps[2].endMs + 1000, label: "Hit Cone" }];
  const D = rebuildRun(convertSession(j, "cones.json")[0].D);
  it("assigns cone flags to completed laps, without counting other flags or duplicates", () => {
    const s = new Session(D), user = seedCones(D, emptyUserState());
    const a = new Analysis(s, user);
    expect(user.ce).toHaveLength(2);
    expect(a.conesOf(s.runs[0])).toBe(1);
    expect(a.conesOf(s.runs[1])).toBe(1);
    expect(a.conesOf(s.runs[2])).toBe(0);
    expect(a.adj(s.runs[0])).toBe(s.runs[0].dur + 2000);
    expect(user.ce[0].t).toBe(t - s.T0);
  });
  it("keeps manually added hits and does not duplicate imported hits on reopening", () => {
    const user = emptyUserState();
    user.ce = [{ id: "manual-cone", t: 1234 }];
    const first = seedCones(D, user), again = seedCones(D, first);
    expect(again.ce).toEqual(first.ce);
    expect(again.ce).toHaveLength(3);
    expect(again.ce.some((event) => event.id === "manual-cone")).toBe(true);
  });
  it("keeps an imported hit deleted after reload", () => {
    const p = new Player(new Session(D), seedCones(D, emptyUserState()), defaultUnits());
    const removed = p.user.ce[0].id;
    p.deleteCone(0);
    const again = seedCones(D, p.user);
    expect(again.ce.some((event) => event.id === removed)).toBe(false);
    expect(again.dismissedCones).toContain(removed);
  });
  it("refreshes imported timing if an earlier out lap changes the timeline origin", () => {
    const user = seedCones(D, emptyUserState());
    const earlier = { ...D, laps: D.laps.map((lap) => ({ ...lap, t0: lap.t0 - 1000 })) };
    const refreshed = seedCones(earlier, user);
    expect(refreshed.ce[0].t).toBe(user.ce[0].t + 1000);
    expect(refreshed.ce).toHaveLength(user.ce.length);
  });
});
