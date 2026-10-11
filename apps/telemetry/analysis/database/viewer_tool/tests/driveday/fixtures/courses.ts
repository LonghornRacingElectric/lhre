import type { LiveLapExport, LiveSample, SessionExport } from "../../../src/lib/driveday/types";
import { pathAt, TRACK_LEN } from "./synth";

/* An autocross-style day on the same made-up track: the start line and the finish line are in different places, and the
   drive back is marked as a lap too.
     leg 1  run, start line to finish line      (forward)
     leg 2  slow drive back to the start line
     leg 3  run, forward again
     leg 4  run, finish line to start line      (the other way)
     leg 5  slow drive back to the finish line
     leg 6  run, the other way again */

const LAT0 = 30.3865;
const LON0 = -97.7242;
const KY = (6378137 * Math.PI) / 180;
const KX = KY * Math.cos((LAT0 * Math.PI) / 180);

export const COURSE_LEN = 150; // metres from the start line to the finish line, round the first U-turn and part way back
export const RUN_SPEED = 10;
export const BACK_SPEED = 3;

interface Leg {
  from: number;
  to: number;
  speed: number;
}

export function synthCourses(): SessionExport {
  const dt = 555;
  const t0 = 1_700_100_000_000;
  const legs: Leg[] = [
    { from: 0, to: COURSE_LEN, speed: RUN_SPEED },
    { from: COURSE_LEN, to: 0, speed: BACK_SPEED },
    { from: 0, to: COURSE_LEN, speed: RUN_SPEED },
    { from: COURSE_LEN, to: 0, speed: RUN_SPEED },
    { from: 0, to: COURSE_LEN, speed: BACK_SPEED },
    { from: COURSE_LEN, to: 0, speed: RUN_SPEED },
  ];
  const samples: LiveSample[] = [];
  const laps: LiveLapExport[] = [];
  let t = t0;
  const mk = (tm: number, s: number, speed: number): LiveSample => {
    const p = pathAt(Math.max(0, Math.min(TRACK_LEN - 1, s)));
    return {
      t: Math.round(tm),
      lat: LAT0 + p.y / KY,
      lon: LON0 + p.x / KX,
      speed: 0,
      hv_pack_v: 500,
      values: { accel_pedal_travel: 0.5, soc_estimate: 80 - (tm - t0) / 60000, dc_bus_v: 500, dc_bus_current: 20 * speed, min_cell_voltage: 3.6 },
    };
  };
  /* parked for a few seconds before the first run */
  for (let k = 0; k < 8; k++, t += dt) samples.push(mk(t, 0, 0));
  legs.forEach((g, i) => {
    const start = t;
    const dir = Math.sign(g.to - g.from);
    const dur = Math.abs(g.to - g.from) / g.speed;
    const steps = Math.round((dur * 1000) / dt);
    for (let k = 0; k < steps; k++) samples.push(mk(start + k * dt, g.from + (dir * g.speed * k * dt) / 1000, g.speed));
    t = start + steps * dt;
    laps.push({
      id: "manual-lap-" + (i + 1), label: "Manual " + (i + 1), kind: "manual",
      startMs: start, endMs: t, durationMs: t - start,
      sectors: [], energyWh: 20 + i, energyOutWh: 20 + i, energyInWh: 0, distanceM: COURSE_LEN, avgSpeedMps: g.speed, samples: [],
    });
  });
  samples.push(mk(t, 0, 0));
  return {
    version: 1,
    savedAt: t + 1,
    source: "orion",
    topic: "synthetic",
    targetLaps: 4,
    targetEnergyKwh: 5,
    soeCutoffCellV: 2.8,
    totalEnergyWh: laps.reduce((a, l) => a + l.energyWh, 0),
    laps,
    selectedLapIds: [],
    sampleTail: samples,
    hasSectors: false,
    sessionInfo: { id: "sess-courses-1", name: "Two ways round", driver: "Test Driver", venue: "Test Lot", eventType: "Autocross" },
  };
}

/* Two laps of the loop, timed by hand: the first press comes 38 m short of a full lap, the second starts 40 m past the line and
   stops 33 m short. Neither run ends anywhere near where it began, but each drove on through where the other was cut. */
export function synthLoopPresses(): SessionExport {
  const dt = 555;
  const t0 = 1_700_200_000_000;
  const legs: Leg[] = [
    { from: 0, to: 170, speed: RUN_SPEED },
    { from: 170, to: TRACK_LEN + 40, speed: RUN_SPEED },
    { from: TRACK_LEN + 40, to: 2 * TRACK_LEN + 7, speed: RUN_SPEED },
  ];
  const samples: LiveSample[] = [];
  const laps: LiveLapExport[] = [];
  let t = t0;
  const mk = (tm: number, s: number, speed: number): LiveSample => {
    const p = pathAt(s);
    return {
      t: Math.round(tm),
      lat: LAT0 + p.y / KY,
      lon: LON0 + p.x / KX,
      speed: 0,
      hv_pack_v: 500,
      values: { accel_pedal_travel: 0.5, soc_estimate: 80 - (tm - t0) / 60000, dc_bus_v: 500, dc_bus_current: 20 * speed, min_cell_voltage: 3.6 },
    };
  };
  for (let k = 0; k < 8; k++, t += dt) samples.push(mk(t, 0, 0));
  legs.forEach((g, i) => {
    const start = t;
    const dur = (g.to - g.from) / g.speed;
    const steps = Math.round((dur * 1000) / dt);
    for (let k = 0; k < steps; k++) samples.push(mk(start + k * dt, g.from + (g.speed * k * dt) / 1000, g.speed));
    t = start + steps * dt;
    /* the middle leg is the drive between the two presses, not a lap */
    if (i === 1) return;
    const n = laps.length + 1;
    laps.push({
      id: "manual-lap-" + n, label: "Manual " + n, kind: "manual",
      startMs: start, endMs: t, durationMs: t - start,
      sectors: [], energyWh: 20 + i, energyOutWh: 20 + i, energyInWh: 0, distanceM: g.to - g.from, avgSpeedMps: g.speed, samples: [],
    });
  });
  samples.push(mk(t, 2 * TRACK_LEN + 7, 0));
  return {
    version: 1,
    savedAt: t + 1,
    source: "orion",
    topic: "synthetic",
    targetLaps: 2,
    targetEnergyKwh: 5,
    soeCutoffCellV: 2.8,
    totalEnergyWh: laps.reduce((a, l) => a + l.energyWh, 0),
    laps,
    selectedLapIds: [],
    sampleTail: samples,
    hasSectors: false,
    sessionInfo: { id: "sess-presses-1", name: "Loop by hand", driver: "Test Driver", venue: "Test Lot", eventType: "Autocross" },
  };
}
