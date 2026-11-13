import { describe, expect, it } from "vitest";
import { emptyUserState, seedFlags } from "../../src/lib/driveday/types";

describe("Trackside event flags", () => {
  const flags = [{ t: 11_000, label: "Hit Cone", saved: true }, { t: 12_500, label: "Off-track", saved: false }];
  it("become marks on the session clock; cone flags are left to the run's coneEvents", () => {
    const u = seedFlags(emptyUserState(), flags, 10_000);
    expect(u.ce).toEqual([]);
    expect(u.marks).toEqual([{ id: "flag-12500", t: 2500, text: "Off-track" }]);
  });
  it("are not added twice when the same file is imported again", () => {
    const u = seedFlags(seedFlags(emptyUserState(), flags, 10_000), flags, 10_000);
    expect(u.marks.length).toBe(1);
  });
});
