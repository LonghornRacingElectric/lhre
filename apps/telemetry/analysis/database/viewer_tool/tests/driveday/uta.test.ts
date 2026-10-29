import { readFileSync } from "fs";
import { join } from "path";
import { describe, expect, it } from "vitest";
import { convertSession } from "../../src/lib/driveday/convert";
import type { SessionExport } from "../../src/lib/driveday/types";

const directory = process.env.DRIVEDAY_UTA_DIR;
describe.skipIf(!directory)("UTA October 18 GPS regressions", () => {
  const load = (suffix: string) => JSON.parse(readFileSync(join(directory!, `UTA_Autox_-_10_18_session${suffix}.json`), "utf8")) as SessionExport;
  it("keeps Oliver's four driving laps on one real course", () => {
    const courses = convertSession(load(""), "Oliver.json");
    expect(courses).toHaveLength(1);
    expect(courses[0].D.RL).toBeGreaterThan(800);
    expect(courses[0].D.laps.filter(l => l.kind === "lap").map(l => l.name)).toEqual(["Manual 1", "Manual 3", "Manual 5", "Manual 7"]);
  });
  it("keeps Andrew's usable first lap and excludes frozen/missing GPS", () => {
    const courses = convertSession(load(" (1)"), "Andrew.json");
    expect(courses).toHaveLength(1);
    expect(courses[0].D.RL).toBeGreaterThan(800);
    expect(courses[0].D.laps.filter(l => l.kind === "lap").map(l => l.name)).toEqual(["Manual 1"]);
    expect(courses[0].D.warnings.join(" ")).toContain("incomplete GPS coverage");
  });
  it("explains that Shreyas's frozen coordinates cannot supply a map", () => {
    expect(() => convertSession(load(" (2)"), "Shreyas.json")).toThrow(/missing, frozen, stationary/);
  });
});
