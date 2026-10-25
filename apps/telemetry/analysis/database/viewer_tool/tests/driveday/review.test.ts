import { describe, expect, it } from "vitest";
import { sampleQuality, segmentReview, crossing } from "../../src/lib/driveday/review";
import { syncFromCheckpoints, videoTime } from "../../src/lib/driveday/video";
import { Analysis } from "../../src/lib/driveday/analysis";
import { convertSession } from "../../src/lib/driveday/convert";
import { Session } from "../../src/lib/driveday/session";
import { rebuildRun } from "../../src/lib/driveday/rebuild";
import { Player } from "../../src/lib/driveday/player";
import { debriefReport } from "../../src/lib/driveday/report";
import { emptyUserState, type Lap } from "../../src/lib/driveday/types";
import { defaultUnits } from "../../src/lib/driveday/units";
import { workspacePreset } from "../../src/lib/driveday/workspace";
import { synthSession } from "./fixtures/synth";

describe("data and driving review", () => {
  it("reports original sample rate and missing sample intervals", () => {
    const quality = sampleQuality([0, 50, 100, 150, 200, 450, 500]);
    expect(quality.hz).toBe(20);
    expect(quality.gaps).toEqual([{ start: 200, end: 450, duration: 250 }]);
    expect(sampleQuality([]).hz).toBe(0);
  });
  it("uses actual crossings and refuses missing section coverage", () => {
    const lap = { kind: "lap", dur: 10000, ch: { t: [0, 2000, 4000, 6000, 8000, 10000], u: [0, 20, 40, 60, 80, 100], spd: [30, 30, 20, 10, 20, 30], brkp: [0, 10, 40, 0, 0, 0], thr: [70, 0, 0, 0, 60, 80] } } as Lap;
    const ghost = { ...lap, dur: 9000, ch: { ...lap.ch, t: lap.ch.t.map(t => t * 0.9) } };
    const result = segmentReview(lap, ghost, { a: 40, len: 40, type: "c", dir: 1, name: "T1" }, 100);
    expect(result.delta).toBe(400);
    expect(result.brake?.distance).toBe(20);
    expect(result.apex?.distance).toBe(60);
    expect(result.throttle?.distance).toBe(80);
    expect(crossing({ ...lap, ch: { ...lap.ch, u: [0, 20, 40] } }, 60, 100)).toBeNull();
  });
  it("preserves import metadata about original GPS and interpolation", () => {
    const exportData = synthSession({ laps: 3, sampleMs: 50 });
    const patch = (sample: typeof exportData.sampleTail[number]) => Math.round(sample.t / 50) % 2 ? { ...sample, lat: NaN, lon: NaN } : sample;
    exportData.sampleTail = exportData.sampleTail.map(patch);
    exportData.laps = exportData.laps.map(lap => ({ ...lap, samples: lap.samples.map(patch) }));
    const data = convertSession(exportData, "quality.json")[0].D;
    expect(data.quality!.interpolatedTimes.length).toBeGreaterThan(0);
    expect(sampleQuality(data.quality!.sampleTimes).hz).toBe(20);
    expect(sampleQuality(data.quality!.gpsTimes).hz).toBe(10);
  });
});

describe("video checkpoints", () => {
  it("corrects both offset and drift using two corresponding events", () => {
    const model = syncFromCheckpoints([{ telemetry: 10000, video: 5 }, { telemetry: 110000, video: 105.2 }]);
    expect(model.rate).toBeCloseTo(1.002);
    expect(videoTime(10000, model.offset, 200, model.rate).time).toBeCloseTo(5);
    expect(videoTime(110000, model.offset, 200, model.rate).time).toBeCloseTo(105.2);
    expect(syncFromCheckpoints([{ telemetry: 10000, video: 5 }])).toEqual({ rate: 1, offset: -5 });
  });
  it("rejects coincident, backward or implausible sync events", () => {
    expect(() => syncFromCheckpoints([{ telemetry: 0, video: 1 }, { telemetry: 0, video: 2 }])).toThrow();
    expect(() => syncFromCheckpoints([{ telemetry: 0, video: 10 }, { telemetry: 10000, video: 5 }])).toThrow();
    expect(() => syncFromCheckpoints([{ telemetry: 0, video: 0 }, { telemetry: 10000, video: 100 }])).toThrow();
  });
});

describe("eligibility, bookmarks and reports", () => {
  const data = convertSession(synthSession({ laps: 4 }), "review.json")[0].D;
  it("applies rules and overrides without inventing an eligible best", () => {
    const session = new Session(rebuildRun(data)), user = emptyUserState();
    const a = new Analysis(session, user), first = session.runs[0], no = session.lapNo(first);
    user.flags[no] = { f: "offcourse" }; a.recompute(user);
    expect(a.valid(first)).toBe(false);
    user.flags[no].include = true; a.recompute(user); expect(a.valid(first)).toBe(true);
    session.runs.forEach(lap => { user.flags[session.lapNo(lap)] = { include: false }; });
    a.recompute(user); expect(Number.isNaN(a.bestLap)).toBe(true); expect(a.theoLap()).toBeNull();
  });
  it("captures video bookmarks and escapes report content", () => {
    const p = new Player(new Session(rebuildRun(data)), emptyUserState(), defaultUnits());
    p.videoUrl = "local"; p.videoKey = "camera"; p.videoName = "onboard.mp4"; p.videoCurrentTime = 12.3;
    p.addMark('<script>alert("x")</script>', "braking");
    expect(p.user.marks[0].video?.time).toBe(12.3); expect(p.user.marks[0].category).toBe("braking");
    p.user.setup.susp = "<img src=x onerror=alert(1)>";
    const html = debriefReport(p, p.runs.map(lap => p.laps.indexOf(lap)));
    expect(html).toContain("&lt;script&gt;"); expect(html).not.toContain("<script>"); expect(html).not.toContain("<img src=x"); expect(html).toContain("<svg");
    p.videoUrl = ""; p.dispose();
  });
  it("offers task-specific workspace defaults", () => {
    expect(workspacePreset("Battery analysis").layout.panes.video.visible).toBe(false);
    expect(workspacePreset("Driving review").panel).toBe("driving");
    expect(workspacePreset("Video comparison").layout.panes.video.weight).toBe(2);
  });
});
