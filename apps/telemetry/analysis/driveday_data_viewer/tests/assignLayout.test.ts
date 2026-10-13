import { describe, expect, it } from "vitest";
import { assignStoredLayout } from "../src/lib/assignLayout";
import { convertSession } from "../src/lib/convert";
import { testLayouts } from "../src/lib/layouts";
import { cumulative, matchTrack, reproject } from "../src/lib/trackMatching";
import { synthSession } from "./fixtures/synth";
import type { XY } from "../src/lib/types";

const source = convertSession(synthSession(), "run.json", [])[0];
const translated = {
  ...source.track, id: "shared-layout", name: "Shared test layout", lat0: source.track.lat0 + 0.0001, lon0: source.track.lon0 - 0.0001,
};
translated.ref = source.track.ref.map(p => reproject(p, source.track, translated));
translated.cum = cumulative(translated.ref);
translated.RL = translated.cum[translated.cum.length - 1];

describe("saved layout assignment", () => {
  it("upgrades existing runs into the same map frame without changing GPS or telemetry", () => {
    const old = { ...source.D, origin: undefined, layoutVersion: undefined };
    const result = assignStoredLayout(old, source.track, [translated]);
    expect(result.track.id).toBe(translated.id);
    expect(result.data.meta.trackId).toBe(translated.id);
    expect(result.data.meta.sessId).toBe(old.meta.sessId);
    expect(result.data.layoutVersion).toBe(3);
    result.data.laps.forEach((lap, i) => {
      const previous = old.laps[i];
      expect(lap.dur).toBe(previous.dur);
      expect(lap.wh).toBe(previous.wh);
      expect(lap.ch.t).toEqual(previous.ch.t);
      expect(lap.ch.thr).toEqual(previous.ch.thr);
      expect(lap.ch.cells).toEqual(previous.ch.cells);
      lap.pts.forEach((p, j) => {
        const xy = reproject([p[0], p[1]], translated, source.track);
        expect(xy[0]).toBeCloseTo(previous.pts[j][0], 5);
        expect(xy[1]).toBeCloseTo(previous.pts[j][1], 5);
        expect(p[2]).toBe(previous.pts[j][2]);
      });
    });
  });
  it("cannot put opposite directions in one comparison group", () => {
    const lap = source.D.laps.find(l => l.kind === "lap")!.pts.map(p => [p[0], p[1]] as XY);
    expect(matchTrack([lap], false, source.track, [source.track])?.id).toBe(source.track.id);
    expect(matchTrack([[...lap].reverse()], false, source.track, [source.track])).toBeUndefined();
    expect(matchTrack([lap, [...lap].reverse()], false, source.track, [source.track])).toBeUndefined();
  });
  it("the reverse test layout reuses the same corrected physical boundaries", () => {
    const [forward, reverse] = testLayouts;
    expect(reverse.id).not.toBe(forward.id);
    expect(reverse.ref).toEqual([...forward.ref].reverse());
    expect(reverse.edges!.left).toEqual([...forward.edges!.right].reverse());
    expect(reverse.edges!.right).toEqual([...forward.edges!.left].reverse());
    expect(forward.edges!.widthM).toBeCloseTo(11.5 * 0.3048);
  });
});
