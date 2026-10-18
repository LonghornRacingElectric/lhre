import { describe, expect, it } from "vitest";
import { directionOf, driveDayGroups, driverOf, filterOptions, filterRuns } from "../src/lib/run-picker";
import type { RunListEntry } from "../src/lib/types";
import { convertSession } from "../src/lib/convert";
import { metaOf } from "../src/lib/storage";
import { synthSession } from "./fixtures/synth";

const run = (id: string, values: Partial<RunListEntry> = {}): RunListEntry => ({
  id, direction: "cw", name: `Session ${id}`, driver: "Ben", venue: "Pickle Lot", event: "Practice", track: "Pickle Lot · clockwise", trackId: "layout-pickle-lot-cw",
  ts: new Date(2026, 9, 12, 12).getTime(), added: 1, laps: 99, best: 1234, ...values,
});

describe("run browsing", () => {
  it("groups the first import, and regroups later imports by recorded date and location", () => {
    expect(driveDayGroups([])).toEqual([]);
    const a = run("a");
    expect(driveDayGroups([a])).toHaveLength(1);
    const b = run("b", { driver: "Shreyas", venue: "  pickle   lot  ", ts: a.ts + 3600000 });
    const old = run("old", { ts: a.ts - 86400000, added: 999 });
    const elsewhere = run("elsewhere", { venue: "COTA" });
    const groups = driveDayGroups([old, a, b, elsewhere]);
    expect(groups).toHaveLength(3);
    expect(groups.map(g => g.date)).toEqual(["2026-10-12", "2026-10-12", "2026-10-11"]);
    expect(groups.find(g => g.runs.some(r => r.id === "a"))!.runs.map(r => r.id)).toEqual(["b", "a"]);
    expect([a, b, old, elsewhere].map(r => r.id)).toEqual(["a", "b", "old", "elsewhere"]);
  });
  it("uses built-in location labels and keeps missing metadata visible", () => {
    expect(driveDayGroups([run("a", { venue: "" })])[0].location).toBe("Pickle Lot");
    const missing = run("b", { ts: 0, direction: undefined, driver: "", venue: "", track: "", trackId: "" });
    expect(driveDayGroups([missing])[0].label).toBe("Date unspecified · Location unspecified");
    expect(driverOf(missing)).toBe("Driver unspecified");
    expect(directionOf(missing)).toBe("Direction unclear");
  });
  it("uses computed direction metadata instead of course numbering", () => {
    expect(directionOf(run("a", { track: "Pickle Lot · counter-clockwise", trackId: "layout-pickle-lot-ccw", direction: "ccw" }))).toBe("Counter-clockwise");
    expect(directionOf(run("b"))).toBe("Clockwise");
    expect(directionOf(run("c", { track: "", trackId: "generic", direction: "ccw", name: "Session 2 · Course 2 (reverse of 1)" }))).toBe("Counter-clockwise");
  });
  it("combines optional filters and search without relying on times or lap counts", () => {
    const runs = [run("1"), run("2", { driver: "Shreyas" }), run("3", { driver: "Shreyas", track: "Counter-clockwise", trackId: "ccw", direction: "ccw" })];
    expect(filterRuns(runs, "", "", "")).toEqual(runs);
    expect(filterRuns(runs, "Shreyas", "Clockwise", " session 2 ").map(r => r.id)).toEqual(["2"]);
    expect(filterRuns(runs, "Ben", "Counter-clockwise", "")).toEqual([]);
    expect(filterOptions(runs, driverOf)).toEqual(["Ben", "Shreyas"]);
  });
  it("uses natural session-name ordering when timestamps tie", () => {
    expect(driveDayGroups([run("10"), run("2")])[0].runs.map(r => r.id)).toEqual(["2", "10"]);
  });
  it("organizes metadata produced by the JSON importer without any migration", () => {
    const first = convertSession(synthSession({ sessionId: "one", laps: 3 }), "first.json")[0];
    const second = convertSession(synthSession({ sessionId: "two", laps: 3 }), "second.json", [first.track])[0];
    const groups = driveDayGroups([metaOf(first.D), metaOf(second.D)]);
    expect(groups).toHaveLength(1);
    expect(groups[0].runs).toHaveLength(2);
  });
});
