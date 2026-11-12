import { describe, expect, it } from "vitest";
import { convertSession } from "../../src/lib/driveday/convert";
import type { SessionExport } from "../../src/lib/driveday/types";
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

  it("rejects frozen GPS even when telemetry timestamps continue", () => {
    const j = synthSession({ laps: 3 });
    j.sampleTail = j.sampleTail.map(s => ({ ...s, lat: j.sampleTail[0].lat, lon: j.sampleTail[0].lon }));
    expect(() => convertSession(j, "frozen.json")).toThrow(/missing, frozen, stationary/);
  });

  it("keeps healthy laps and does not manufacture a course from GPS dropouts", () => {
    const j = synthSession({ laps: 3 });
    const lost = j.laps[1];
    j.sampleTail = j.sampleTail.map(s => s.t >= lost.startMs && s.t <= lost.endMs ? { ...s, lat: 0, lon: 0 } : s);
    const imported = convertSession(j, "dropout.json");
    expect(imported).toHaveLength(1);
    expect(imported[0].D.laps.filter(l => l.kind === "lap").map(l => l.dur)).toEqual([j.laps[0].durationMs, j.laps[2].durationMs]);
    expect(imported[0].D.warnings.join(" ")).toMatch(/incomplete GPS coverage/);
    expect(imported[0].D.RL).toBeGreaterThan(TRACK_LEN * 0.9);
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

  it("retains every timestamp of a 50 Hz recording through playback rebuild", async () => {
    const { rebuildRun } = await import("../../src/lib/driveday/rebuild");
    const j = synthSession({ laps: 3, sampleMs: 20 });
    j.laps.forEach(lap => { lap.samples = j.sampleTail.filter(s => s.t >= lap.startMs && s.t <= lap.endMs); });
    const run = rebuildRun(convertSession({ ...j, sampleTail: j.sampleTail.slice(-100) }, "50hz.json")[0].D);
    const timed = run.laps.filter(l => l.kind === "lap");
    timed.forEach((lap, i) => {
      const times = lap.ch.t.map(t => t + lap.t0);
      expect(times).toEqual(expect.arrayContaining(j.laps[i].samples.map(s => s.t)));
      // Rebuild can add exact logger-boundary endpoints between recorded samples.
      expect(times.length).toBeLessThanOrEqual(j.laps[i].samples.length + 2);
    });
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

describe("a session with one course that has no clean lap", () => {
  /* the same loop driven the other way round, as a mirror image */
  const mirror = (J: SessionExport) => { J.sampleTail.forEach((s) => { s.lon = 2 * -97.7242 - s.lon; }); return J; };
  const reverse = convertSession(mirror(synthSession({ laps: 4, sessionId: "sess-reverse" })), "reverse.json", [])[0].track;
  /* forward laps, then the reverse loop with every second press missed: each of its laps covers the known track twice */
  const fwd = synthSession({ laps: 8 });
  const back = mirror(synthSession({ laps: 6, startMs: fwd.sampleTail.at(-1)!.t + 60_000, sessionId: "sess-back" }));
  const missed = [0, 2, 4].map((i) => ({ ...back.laps[i], id: "double-" + i, endMs: back.laps[i + 1].endMs, durationMs: back.laps[i + 1].endMs - back.laps[i].startMs }));
  const J = { ...fwd, laps: [...fwd.laps, ...missed], sampleTail: [...fwd.sampleTail, ...back.sampleTail] };

  it("opens the other course and says which one it left out", () => {
    const rs = convertSession(J, "two-courses.json", [reverse]);
    expect(rs.length).toBe(1);
    expect(rs[0].D.laps.filter((l) => l.kind === "lap").length).toBe(8);
    expect(rs[0].D.warnings.join(" ")).toMatch(/was left out: none of its laps was a clean single circuit/);
  });

  it("fails only when no course has a clean lap", () => {
    expect(() => convertSession({ ...back, laps: missed }, "back.json", [reverse])).toThrow(/No timed lap was a clean single circuit/);
  });
});

describe("the drive after the last lap press", () => {
  it("is kept as a lap in progress at run pace, and dropped when it is a crawl back to the pits", () => {
    const J = synthSession({ laps: 4 });
    const last = J.laps.at(-1)!.endMs;
    /* the same ten seconds of driving, stretched to fifty: a fifth of the speed */
    J.sampleTail.forEach((s) => { if (s.t > last) s.t = last + (s.t - last) * 5; });
    expect(convertSession(J, "crawl.json", [])[0].D.laps.some((l) => l.kind === "partial")).toBe(false);
  });
});
