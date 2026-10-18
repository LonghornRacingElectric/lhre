import { describe, expect, it } from "vitest";
import { comparisonLap, sameRoute } from "../src/lib/comparison";
import { convertSession } from "../src/lib/convert";
import { rebuildRun } from "../src/lib/rebuild";
import { Session } from "../src/lib/session";
import { sectorTimes } from "../src/lib/sectors";
import { videoCodec, videoTime } from "../src/lib/video";
import { synthSession } from "./fixtures/synth";
import { synthCourses } from "./fixtures/courses";

describe("cross-file comparison", () => {
  it("matches separately imported laps, retaining logged time and shared gates", () => {
    const first = convertSession(synthSession({ sessionId: "one", laps: 3 }), "one.json")[0];
    const other = convertSession(synthSession({ sessionId: "two", laps: 4 }), "two.json", [first.track])[0];
    const s = new Session(rebuildRun(first.D));
    s.setGates([s.RL * 0.2, s.RL * 0.7]);
    const i = other.D.laps.findIndex(l => l.kind === "lap");
    const lap = comparisonLap(s, rebuildRun(other.D), i);
    expect(sameRoute(first.D, other.D)).toBe(true);
    expect(lap.dur).toBe(other.D.laps[i].dur);
    expect(lap.sec).toEqual(sectorTimes(lap, s.gates));
    expect(sameRoute(first.D, { ...other.D, meta: { ...other.D.meta, trackId: "reverse" } })).toBe(false);
    expect(() => comparisonLap(s, { ...other.D, open: !first.D.open }, i)).toThrow();
  });
  it("aligns both layouts and uses each driver's own peak brake pressure", () => {
    const D = convertSession(synthSession({ laps: 3 }), "main.json")[0].D;
    const s = new Session(rebuildRun({ ...D, layoutShift: [3, 4] }));
    const other = rebuildRun({ ...D, layoutShift: [1, 2], laps: D.laps.map(l => ({ ...l,
      ch: { ...l.ch, brk: l.ch.brk.map(v => v * 3), brkr: l.ch.brkr.map(v => v * 2) } })) });
    const i = other.laps.findIndex(l => l.kind === "lap");
    const lap = comparisonLap(s, other, i);
    const asMain = new Session(other).laps[i];
    expect(lap.ch.brkp).toEqual(asMain.ch.brkp);
    expect(lap.ch.brkrp).toEqual(asMain.ch.brkrp);
    expect(Math.max(...lap.ch.brkp)).toBeLessThanOrEqual(100);
    expect(lap.pts[0].slice(0, 2)).toEqual([other.laps[i].pts[0][0] + 2, other.laps[i].pts[0][1] + 2]);
  });
  it("reuses open routes across exports without mixing opposite directions", () => {
    const first = convertSession(synthCourses(), "first.json");
    const next = convertSession(synthCourses(), "next.json", first.map(c => c.track));
    expect(next[0].D.meta.trackId).toBe(first[0].D.meta.trackId);
    expect(next[1].D.meta.trackId).toBe(first[1].D.meta.trackId);
    expect(sameRoute(next[0].D, next[1].D)).toBe(false);
  });
});

describe("video synchronisation", () => {
  it("identifies GoPro HEVC and H.264 sample entries without trusting the extension", async () => {
    for (const [tag, name] of [["hvc1", "HEVC / H.265"], ["avc1", "H.264"]]) {
      const data = new Uint8Array(100);
      new DataView(data.buffer).setUint32(8, 86);
      data.set(new TextEncoder().encode(tag), 12);
      expect(await videoCodec(new Blob([data]))).toBe(name);
    }
    expect(await videoCodec(new Blob(["not a video hvc1"]))).toBe("");
  });
  it("supports delayed recordings and holds endpoints outside the clip", () => {
    expect(videoTime(4000, -5, 20)).toEqual({ time: 0, inRange: false });
    expect(videoTime(7000, -5, 20)).toEqual({ time: 2, inRange: true });
    expect(videoTime(30000, -5, 20)).toEqual({ time: 20, inRange: false });
    expect(videoTime(0, 3, 20)).toEqual({ time: 3, inRange: true });
  });
});
