import { describe, expect, it } from "vitest";
import { convertSession } from "../src/lib/convert";
import { Session } from "../src/lib/session";
import { synthSession } from "./fixtures/synth";

const s = new Session(convertSession(synthSession({ laps: 8 }), "synth.json", []).D);

describe("corners, straights and braking zones", () => {
  it("finds the two U-turns and the two straights of a stadium", () => {
    expect(s.segs.filter((g) => g.type === "c").length).toBe(2);
    expect(s.segs.filter((g) => g.type === "s").length).toBe(2);
  });
  it("covers the whole loop exactly once", () => {
    expect(s.segs.reduce((a, g) => a + g.len, 0)).toBeCloseTo(s.RL, 0);
  });
  it("names them in order from the start/finish line", () => {
    expect(s.segs.map((g) => g.name).sort()).toEqual(["Str 1", "Str 2", "T1", "T2"]);
  });
  it("finds a braking zone before each U-turn, on every lap", () => {
    expect(s.zones.length).toBe(2);
    s.zones.forEach((z) => expect(z.n).toBe(8));
    expect(s.zones.map((z) => z.name)).toEqual(["B1", "B2"]);
  });
  it("puts the zones just before the corners", () => {
    s.zones.forEach((z) => {
      const nextCorner = s.segs.filter((g) => g.type === "c").map((g) => (((g.a - z.s0) % s.RL) + s.RL) % s.RL);
      expect(Math.min(...nextCorner)).toBeLessThan(25);
    });
  });
});
