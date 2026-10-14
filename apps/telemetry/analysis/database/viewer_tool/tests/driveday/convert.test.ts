import { describe, expect, it } from "vitest";
import { convertSession } from "../../src/lib/driveday/convert";
import { synthSession, TRACK_LEN } from "./fixtures/synth";

describe("convertSession", () => {
  const J = synthSession({ laps: 8 });
  const { D, track, isNew } = convertSession(J, "synth.json", [])[0];
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
    const again = convertSession(synthSession({ laps: 6, sessionId: "sess-synth-2" }), "again.json", [track])[0];
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

  it("recovers all 20 Hz lap samples when the session tail is truncated", () => {
    const full = synthSession({ laps: 2, sampleMs: 50 });
    full.laps.forEach((l) => { l.samples = full.sampleTail.filter((s) => s.t >= l.startMs && s.t <= l.endMs); });
    const tailSamples = full.sampleTail.filter((s) => s.t >= full.laps[1].endMs);
    const exported = { ...full, currentLap: { lapSamples: tailSamples }, sampleTail: full.sampleTail.slice(-100) };
    const recovered = convertSession(exported, "20hz.json")[0].D.laps.filter((l) => l.kind === "lap");
    expect(recovered).toHaveLength(2);
    recovered.forEach((l, i) => {
      expect(l.ch.t.map((t) => t + l.t0)).toEqual(full.laps[i].samples.map((s) => s.t));
      expect(l.dur).toBe(full.laps[i].durationMs);
    });
    const partial = convertSession(exported, "20hz.json")[0].D.laps.find((l) => l.kind === "partial")!;
    expect(partial.ch.t.map((t) => t + partial.t0)).toEqual(tailSamples.map((s) => s.t));
  });

  it("keeps telemetry timestamps between GPS fixes and merges overlap", () => {
    const full = synthSession({ laps: 2, sampleMs: 50 });
    const expected = full.laps.map((l) => full.sampleTail.filter((s) => s.t >= l.startMs && s.t <= l.endMs));
    const sparse = full.sampleTail.map((s, i) => i % 2 ? { ...s, lat: 0, lon: 0 } : s);
    const exported = { ...full, sampleTail: sparse };
    exported.laps.forEach((l) => { l.samples = sparse.filter((s) => s.t >= l.startMs && s.t <= l.endMs); });
    const converted = convertSession(exported, "sparse-gps.json")[0].D.laps.filter((l) => l.kind === "lap");
    converted.forEach((l, i) => {
      expect(l.ch.t.map((t) => t + l.t0)).toEqual(expected[i].map((s) => s.t));
      expect(l.ch.thr).toEqual(expected[i].map((s) => Math.round(s.values!.accel_pedal_travel * 1000) / 10));
    });
  });
});
