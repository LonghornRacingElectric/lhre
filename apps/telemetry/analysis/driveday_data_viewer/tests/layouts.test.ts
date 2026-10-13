import { describe, expect, it } from "vitest";
import { TRACK_WIDTH_M } from "../src/lib/geometry";
import { builtinTracks } from "../src/lib/layouts";
import { convertSession } from "../src/lib/convert";
import pickle from "../src/lib/layouts/pickle-lot.json";
import type { LiveLapExport, LiveSample, SessionExport } from "../src/lib/types";

const tracks = builtinTracks();

/** Signed area: positive when the loop runs counter-clockwise. */
const area = (p: [number, number][]) => {
  let a = 0;
  for (let i = 0; i < p.length - 1; i++) a += p[i][0] * p[i + 1][1] - p[i + 1][0] * p[i][1];
  return a / 2;
};

describe("built-in base layouts", () => {
  it("makes one layout for each direction", () => {
    expect(tracks.map((t) => t.name)).toEqual(["Pickle Lot · counter-clockwise", "Pickle Lot · clockwise"]);
    expect(area(tracks[0].ref)).toBeGreaterThan(0);
    expect(area(tracks[1].ref)).toBeLessThan(0);
    tracks.forEach((t) => {
      expect(t.builtin).toBe(true);
      expect(t.num).toBeGreaterThan(0); // never renamed on import
      expect(t.ref[0]).toEqual(t.ref[t.ref.length - 1]); // a closed loop
    });
  });

  it("is the same road both ways round", () => {
    const [a, b] = tracks;
    expect(b.RL).toBeCloseTo(a.RL, 6);
    expect(b.ref).toEqual(a.ref.slice().reverse());
    expect(b.edges?.left).toEqual(a.edges?.right.slice().reverse());
    expect(b.edges?.right).toEqual(a.edges?.left.slice().reverse());
  });

  it("puts the edges about a track width apart", () => {
    const e = tracks[0].edges!;
    const gaps = e.left.map((p, i) => Math.hypot(p[0] - e.right[i][0], p[1] - e.right[i][1]));
    const sorted = gaps.slice().sort((x, y) => x - y);
    expect(sorted[Math.floor(sorted.length / 2)]).toBeCloseTo(TRACK_WIDTH_M, 2);
    gaps.forEach((g) => expect(g).toBeLessThanOrEqual(TRACK_WIDTH_M + 1e-6));
  });
});

/* A made-up session driven round the Pickle Lot layout line, 2 m east of where it was recorded (GPS drift between days). */

function driveLayout(reverse: boolean, eastM: number): SessionExport {
  const line = (pickle.line as [number, number][]).slice();
  if (reverse) line.reverse().unshift(line.pop()!); // same start point, the other way round
  const lat0 = line[0][0];
  const KX = ((6378137 * Math.PI) / 180) * Math.cos((lat0 * Math.PI) / 180);
  const loop = line.concat([line[0]]);
  const t0 = 1_760_000_000_000;
  const dt = 500; // 5 m between fixes every 0.5 s: 10 m/s
  const samples: LiveSample[] = [];
  const marks: number[] = [t0];
  let t = t0;
  for (let lap = 0; lap < 3; lap++) {
    for (let i = 0; i < loop.length - 1; i += 5) {
      const [la, lo] = loop[i];
      samples.push({ t, lat: la, lon: lo + eastM / KX, speed: 0, hv_pack_v: 500, values: {}, cellTemps: [] } as LiveSample);
      t += dt;
    }
    marks.push(t);
  }
  samples.push({ t, lat: loop[0][0], lon: loop[0][1] + eastM / KX, speed: 0, hv_pack_v: 500, values: {}, cellTemps: [] } as LiveSample);
  const laps = marks.slice(1).map((m, i) => ({
    id: "lap-" + (i + 1), label: "Lap " + (i + 1), kind: "manual", startMs: marks[i], endMs: m, durationMs: m - marks[i],
    sectors: [], energyWh: 1, energyOutWh: 1, energyInWh: 0, distanceM: 0, avgSpeedMps: 0, samples: [],
  })) as LiveLapExport[];
  return {
    version: 1, savedAt: t0, source: "orion", topic: "synthetic", targetLaps: 3, targetEnergyKwh: 0, soeCutoffCellV: 2.8,
    totalEnergyWh: 3, laps, selectedLapIds: [], sampleTail: samples, hasSectors: false,
    sessionInfo: { id: "sess-pickle-" + (reverse ? "cw" : "ccw"), name: "Pickle test", driver: "Test", venue: "Pickle", eventType: "Autocross" },
  } as SessionExport;
}

describe("matching runs to a base layout", () => {
  it("gives each direction its own layout and slides it onto the run's GPS", () => {
    const ccw = convertSession(driveLayout(false, 2), "ccw.json", tracks)[0];
    const cw = convertSession(driveLayout(true, 2), "cw.json", tracks)[0];
    expect(ccw.track.name).toBe("Pickle Lot · counter-clockwise");
    expect(cw.track.name).toBe("Pickle Lot · clockwise");
    [ccw, cw].forEach((c) => {
      expect(c.isNew).toBe(false);
      expect(c.D.edges).toBeDefined();
      expect(c.D.warnings.join(" ")).toMatch(/moved 2\.\d m/);
    });
  });
});
