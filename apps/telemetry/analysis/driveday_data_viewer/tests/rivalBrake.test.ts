import { afterEach, expect, it, vi } from "vitest";
import { convertSession } from "../src/lib/convert";
import { Player } from "../src/lib/player";
import { rebuildRun } from "../src/lib/rebuild";
import { Session } from "../src/lib/session";
import { runStore } from "../src/lib/storage";
import { emptyUserState } from "../src/lib/types";
import { defaultUnits } from "../src/lib/units";
import { synthSession } from "./fixtures/synth";

afterEach(() => vi.restoreAllMocks());

it("keeps a harder-braking rival within 100% and identical to when they are the main driver", async () => {
  const main = convertSession(synthSession(), "main.json", [])[0].D;
  const rival = {
    ...main, meta: { ...main.meta, sessId: "rival", driver: "Rival" },
    laps: main.laps.map(l => ({ ...l, ch: { ...l.ch, brk: l.ch.brk.map(v => v * 3), brkr: l.ch.brkr.map(v => v * 2) } })),
  };
  vi.spyOn(runStore, "get").mockResolvedValue(rival);
  const player = new Player(new Session(rebuildRun(main)), emptyUserState(), defaultUnits());
  vi.spyOn(player, "changed").mockImplementation(() => {});
  expect(await player.loadRival("rival")).toBe(true);
  const asMain = new Session(rebuildRun(rival));
  expect(asMain.brkMax).toBeGreaterThan(player.session.brkMax);
  player.rival!.laps.forEach((lap, i) => {
    expect(Math.max(...lap.ch.brkp)).toBeLessThanOrEqual(100);
    expect(Math.max(...lap.ch.brkrp)).toBeLessThanOrEqual(100);
    expect(lap.ch.brkp).toEqual(asMain.runs[i].ch.brkp);
    expect(lap.ch.brkrp).toEqual(asMain.runs[i].ch.brkrp);
    expect(lap.ch.brk).toEqual(asMain.runs[i].ch.brk);
  });
  expect(Math.max(...player.rival!.laps.flatMap(l => l.ch.brkp))).toBe(100);
});
