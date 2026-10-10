import { readFileSync } from "fs";
import { beforeAll, describe, expect, it } from "vitest";
import { Analysis } from "../src/lib/analysis";
import { convertSession } from "../src/lib/convert";
import { rebuildRun } from "../src/lib/rebuild";
import { Session } from "../src/lib/session";
import { emptyUserState, type SessionExport } from "../src/lib/types";

/* Runs against a real export when DRIVEDAY_SAMPLE points at one (the files are too big to commit):
     DRIVEDAY_SAMPLE=path/to/session.json npm test */
const path = process.env.DRIVEDAY_SAMPLE;

describe.skipIf(!path)("a real session export", () => {
  let J: SessionExport;
  let s: Session;
  let a: Analysis;
  beforeAll(() => {
    J = JSON.parse(readFileSync(path as string, "utf8")) as SessionExport;
    const { D } = convertSession(J, "real.json", []);
    s = new Session(rebuildRun(D, 0));
    a = new Analysis(s, emptyUserState());
  });

  it("builds every logged lap", () => {
    expect(s.runs.length).toBe(J.laps.length);
    s.runs.forEach((l, i) => expect(l.dur).toBe(J.laps[i].durationMs));
  });
  it("has no jump between laps", () => {
    for (let i = 0; i < s.runs.length - 1; i++) {
      const p = s.runs[i].pts;
      const e = p[p.length - 1];
      const b = s.runs[i + 1].pts[0];
      expect(Math.hypot(e[0] - b[0], e[1] - b[1])).toBeLessThan(0.05);
    }
  });
  it("keeps the logged energy", () => {
    const logged = J.laps.reduce((x, l) => x + l.energyWh, 0);
    const now = s.runs.reduce((x, l) => x + (l.wh ?? 0), 0);
    expect(Math.abs(now - logged) / logged).toBeLessThan(0.02);
  });
  it("prints a summary", () => {
    console.log(`laps ${s.runs.length}, best ${a.bestLap}, theo ${a.theo.toFixed(0)}, RL ${s.RL.toFixed(1)}, segs ${s.segs.map((g) => g.name).join(" ")}, zones ${s.zones.map((z) => z.name + "@" + z.s0.toFixed(0)).join(" ")}`);
  });
});
