import type { LiveLapExport, LiveSample, SessionExport } from "../../src/lib/types";

/* A small made-up drive day on a stadium-shaped track (two straights, two U-turns), so the tests do not need a real export.
   The car parks, pulls out, does a full out lap, then N timed laps, and keeps going for a few seconds after the last lap press.
   It brakes before each U-turn on every lap. */

const LAT0 = 30.3865;
const LON0 = -97.7242;
const KY = (6378137 * Math.PI) / 180;
const KX = KY * Math.cos((LAT0 * Math.PI) / 180);

export const STRAIGHT = 60;
export const RADIUS = 14;
export const TRACK_LEN = 2 * STRAIGHT + 2 * Math.PI * RADIUS;

/** Position and heading at arc length s along the stadium, counter-clockwise, starting at the beginning of the first straight. */
function pathAt(sIn: number): { x: number; y: number; turning: boolean } {
  const s = ((sIn % TRACK_LEN) + TRACK_LEN) % TRACK_LEN;
  const arc = Math.PI * RADIUS;
  if (s < STRAIGHT) return { x: s, y: 0, turning: false };
  if (s < STRAIGHT + arc) {
    const a = (s - STRAIGHT) / RADIUS - Math.PI / 2;
    return { x: STRAIGHT + RADIUS * Math.cos(a), y: RADIUS + RADIUS * Math.sin(a), turning: true };
  }
  if (s < 2 * STRAIGHT + arc) return { x: STRAIGHT - (s - STRAIGHT - arc), y: 2 * RADIUS, turning: false };
  const a = (s - 2 * STRAIGHT - arc) / RADIUS + Math.PI / 2;
  return { x: RADIUS * Math.cos(a), y: RADIUS + RADIUS * Math.sin(a), turning: true };
}

const speedAt = (s: number, lapIdx: number) => {
  const p = pathAt(s);
  const base = p.turning ? 7 : 12;
  return base + (lapIdx % 3) * 0.2; // each lap a touch different
};

export interface SynthOptions {
  laps?: number;
  sessionId?: string;
  startMs?: number;
}

export function synthSession(opts: SynthOptions = {}): SessionExport {
  const N = opts.laps ?? 8;
  const t0 = opts.startMs ?? 1_700_000_000_000;
  const dtSample = 555;
  const samples: LiveSample[] = [];
  const marks: number[] = []; // crossing times of the start/finish line
  const mk = (t: number, s: number, speed: number, braking: boolean, throttle: number, soc: number, energy: number): LiveSample => {
    const p = pathAt(s);
    return {
      t,
      lat: LAT0 + p.y / KY,
      lon: LON0 + p.x / KX,
      speed: 0,
      hv_pack_v: 500 - speed,
      values: {
        accel_pedal_travel: throttle,
        brake_pressure_f: braking ? 600 : 0,
        brake_pressure_rbll: braking ? 300 : 0,
        steer_col_angle: p.turning ? 90 : 0,
        motor_speed: speed * 200,
        torque_feedback: throttle * 120,
        dc_bus_v: 500 - speed,
        dc_bus_current: throttle * 80,
        soc_estimate: soc,
        motor_temp: 40 + t / 1e7 % 5,
        inverter_hotspot_temp: 50,
        gate_driver_temp: 45,
        coolant_temp: 38,
        max_cell_temp: 30,
        module_a_temp: 35,
        module_b_temp: 36,
        module_c_temp: 37,
        min_cell_voltage: 3.6,
        max_cell_voltage: 3.9,
        avg_cell_v_stat: 3.75,
        net_energy: energy,
      },
      cellTemps: [30, 31, 0, 32, 33, 0, 34, 35],
    };
  };

  /* parked for 6 s, 30 m before the line */
  let t = t0;
  let s = TRACK_LEN - 30;
  let e = 0;
  for (; t < t0 + 6000; t += dtSample) samples.push(mk(t, s, 0, false, 0, 80, e));
  /* out lap, then N timed laps, then 10 s more */
  let lap = -1; // -1 = out lap
  let crossings = 0;
  const end = () => crossings >= N + 1;
  let tailStart = 0;
  while (true) {
    const v = speedAt(s, Math.max(0, lap));
    const braking = (() => {
      const m = ((s % TRACK_LEN) + TRACK_LEN) % TRACK_LEN;
      return (m > STRAIGHT - 8 && m <= STRAIGHT) || (m > STRAIGHT + Math.PI * RADIUS + STRAIGHT - 8 && m <= STRAIGHT + Math.PI * RADIUS + STRAIGHT);
    })();
    const throttle = braking ? 0 : pathAt(s).turning ? 0.3 : 0.9;
    const soc = 80 - (t - t0) / 6000;
    e += throttle * 0.9;
    samples.push(mk(t, s, v, braking, throttle, soc, e));
    const sNext = s + (v * dtSample) / 1000;
    if (Math.floor(sNext / TRACK_LEN) > Math.floor(s / TRACK_LEN)) {
      const tc = t + ((TRACK_LEN * Math.floor(sNext / TRACK_LEN) - s) / (sNext - s)) * dtSample;
      crossings++;
      lap++;
      /* the first crossing ends the out lap; each later one is a lap press */
      if (crossings >= 1) marks.push(Math.round(tc));
      if (end() && tailStart === 0) tailStart = Math.round(tc);
    }
    s = sNext;
    t += dtSample;
    if (tailStart && t > tailStart + 10000) break;
    if (t > t0 + 10 * 60 * 1000) throw new Error("synth session ran away");
  }

  const laps: LiveLapExport[] = [];
  for (let i = 0; i < N; i++) {
    laps.push({
      id: "manual-lap-" + (i + 1), label: "Manual " + (i + 1), kind: "manual",
      startMs: marks[i], endMs: marks[i + 1], durationMs: marks[i + 1] - marks[i],
      sectors: [], energyWh: 30 + i, energyOutWh: 30 + i, energyInWh: 0, distanceM: TRACK_LEN, avgSpeedMps: 10, samples: [],
    });
  }
  return {
    version: 1,
    savedAt: t0 + 123,
    source: "orion",
    topic: "synthetic",
    targetLaps: N,
    targetEnergyKwh: 5.7,
    soeCutoffCellV: 2.8,
    totalEnergyWh: laps.reduce((a, l) => a + l.energyWh, 0),
    laps,
    selectedLapIds: [],
    sampleTail: samples,
    hasSectors: false,
    sessionInfo: { id: opts.sessionId ?? "sess-synth-1", name: "Synthetic day", driver: "Test Driver", venue: "Test Lot", eventType: "Autocross" },
  };
}
