import { describe, expect, it } from "vitest";
import { emptyUserState, seedFlags } from "../../src/lib/driveday/types";

describe("Trackside event flags", () => {
  const flags = [{ t: 11_000, label: "Hit Cone", saved: true }, { t: 12_500, label: "Off-track", saved: false }];
  it("become a timed cone and a mark on the session clock", () => {
    const u = seedFlags(emptyUserState(), flags, 10_000);
    expect(u.ce).toEqual([{ id: "flag-11000", t: 1000 }]);
    expect(u.marks).toEqual([{ id: "flag-12500", t: 2500, text: "Off-track" }]);
  });
  it("are not added twice when the same file is imported again", () => {
    const u = seedFlags(seedFlags(emptyUserState(), flags, 10_000), flags, 10_000);
    expect(u.ce.length + u.marks.length).toBe(2);
  });
});
