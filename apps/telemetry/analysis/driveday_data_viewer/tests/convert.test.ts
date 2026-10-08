import { describe, expect, it } from "vitest";
import { convertSession } from "../src/lib/convert";
import { synthSession, TRACK_LEN } from "./fixtures/synth";

describe("convertSession", () => {
  const J = synthSession({ laps: 8 });
  const { D, track, isNew } = convertSession(J, "synth.json", []);
  const timed = D.laps.filter((l) => l.kind === "lap");

  it("builds one lap per manual mark, plus an out lap and a tail", () => {
    expect(timed.length).toBe(8);
    expect(D.laps.some((l) => l.kind === "out")).toBe(true);
    expect(D.laps.some((l) => l.kind === "partial")).toBe(true);
    expect(D.warnings).toEqual([]);
  });

  it("keeps the logger's lap times", () => {
    timed.forEach((l, i) => expect(l.dur).toBe(J.laps[i].durationMs));
  });

  it("splits each lap into three sectors that add up to the lap time", () => {
    timed.forEach((l) => {
      expect(l.sec.length).toBe(3);
      expect((l.sec as number[]).reduce((a, b) => a + b, 0)).toBeCloseTo(l.dur, 0);
    });
  });

  it("draws a closed loop about as long as the real track", () => {
    expect(D.ref[0]).toEqual(D.ref[D.ref.length - 1]);
    expect(Math.abs(D.RL - TRACK_LEN)).toBeLessThan(TRACK_LEN * 0.05);
    expect(isNew).toBe(true);
  });

  it("finds the same track again for another run on it", () => {
    const again = convertSession(synthSession({ laps: 6, sessionId: "sess-synth-2" }), "again.json", [track]);
    expect(again.isNew).toBe(false);
    expect(again.D.meta.trackId).toBe(track.id);
  });

  it("keeps the energy figures the logger gave", () => {
    expect(timed.map((l) => l.wh)).toEqual(J.laps.map((l) => l.energyWh));
  });

  it("rejects a file that is not a session export", () => {
    expect(() => convertSession({ nope: true } as never, "x.json", [])).toThrow(/session export/);
  });

  it("records which cell temperature sensors report", () => {
    expect(D.meta.cellIdx).toEqual([0, 1, 3, 4, 6, 7]);
  });
});
