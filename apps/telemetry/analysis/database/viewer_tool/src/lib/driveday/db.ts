/* Builds the same session export Trackside writes, from the orion database, so a drive day opens without a JSON file. */

import type { LiveLapExport, LiveSample, SessionExport } from "./types";

/** Sample columns read from the database, named as convert.ts reads them from `values`. */
export const DB_CHANNELS = [
  "accel_pedal_travel", "steer_col_angle", "gps_speed",
  "brake_pressure_f", "brake_pressure_rbll", "motor_speed", "torque_feedback", "net_energy", "regen_energy",
  "dc_bus_v", "dc_bus_current", "hv_pack_v", "soc_estimate",
  "motor_temp", "inverter_hotspot_temp", "gate_driver_temp", "coolant_temp",
  "min_cell_voltage", "max_cell_voltage", "module_a_temp", "module_b_temp", "module_c_temp",
] as const;

/** Laps longer than this are the gaps between runs or a lap left open, not timed laps, so their samples are not fetched. */
export const MAX_LAP_MS = 15 * 60_000;

/** Faster than this, a step in the VCU energy counters is a glitch (a frame read as 0, then the real count again), not driving. */
const MAX_COUNTER_KW = 200;

export interface DbDay {
  day_id: number;
  date: string;
  track_name: string | null;
  event_type: string | null;
  driver_name: string | null;
}

/** A classifier row: a lap (start to end) or an event flag (start only). */
export interface DbMark {
  type: string;
  start_time: number;
  end_time: number | null;
  notes: string | null;
}

export type DbRow = { t: number; lat: number | null; lon: number | null; cells_temps: number[] | null } & Partial<Record<(typeof DB_CHANNELS)[number], number | null>>;

export const isTimedLap = (m: DbMark): m is DbMark & { end_time: number } =>
  m.type === "lap" && m.end_time != null && m.end_time > m.start_time && m.end_time - m.start_time <= MAX_LAP_MS;

const r3 = (v: number) => Math.round(v * 1000) / 1000;

export function dbSessionExport(day: DbDay, marks: DbMark[], rows: DbRow[]): SessionExport {
  const sampleTail: LiveSample[] = rows.map((r) => {
    const values: Record<string, number> = {};
    for (const k of DB_CHANNELS) {
      const v = r[k];
      if (typeof v === "number" && Number.isFinite(v)) values[k] = r3(v);
    }
    const cells = r.cells_temps?.map((v) => (Number.isFinite(v) ? Math.round(v * 10) / 10 : 0));
    if (cells?.some((v) => v > 0)) values.max_cell_temp = Math.max(...cells);
    return {
      t: r.t,
      lat: r.lat ?? NaN,
      lon: r.lon ?? NaN,
      ...(values.gps_speed != null && { speed: values.gps_speed }),
      ...(values.hv_pack_v != null && { hv_pack_v: values.hv_pack_v }),
      values,
      ...(cells?.length && { cellTemps: cells }),
    };
  });

  /* lap energy from the VCU's cumulative counters, the way Trackside counts it */
  const energy = (t0: number, t1: number) => {
    let out = 0, inn = 0;
    let prev: LiveSample | null = null;
    for (const s of sampleTail) {
      if (s.t < t0 || s.t > t1) continue;
      const a = prev?.values, b = s.values;
      if (prev && a?.net_energy != null && a.regen_energy != null && b?.net_energy != null && b.regen_energy != null) {
        const dOut = Math.max(0, b.net_energy - a.net_energy + (b.regen_energy - a.regen_energy));
        const dIn = Math.max(0, b.regen_energy - a.regen_energy);
        const cap = (MAX_COUNTER_KW * 1000 * Math.max(500, s.t - prev.t)) / 3_600_000;
        if (dOut <= cap && dIn <= cap) { out += dOut; inn += dIn; }
      }
      prev = s;
    }
    return { energyWh: r3(out - inn), energyOutWh: r3(out), energyInWh: r3(inn) };
  };

  /* A GPS that stopped updating repeats one position for the whole lap. Drop it, so the lap reads as having no GPS
     instead of drawing a track of zero length. */
  for (const m of marks.filter(isTimedLap)) {
    const inLap = sampleTail.filter((s) => s.t >= m.start_time && s.t <= m.end_time && Number.isFinite(s.lat));
    if (new Set(inLap.map((s) => s.lat + "," + s.lon)).size < 3) inLap.forEach((s) => { s.lat = NaN; s.lon = NaN; });
  }

  const laps: LiveLapExport[] = marks.filter(isTimedLap).map((m) => ({
    id: "db:" + day.day_id + ":" + m.start_time,
    label: (m.notes || "").trim(),
    kind: "lap",
    startMs: m.start_time,
    endMs: m.end_time,
    durationMs: m.end_time - m.start_time,
    sectors: [],
    ...energy(m.start_time, m.end_time),
    distanceM: 0,
    avgSpeedMps: null,
    samples: [],
  }));

  const venue = (day.track_name || "").trim();
  return {
    version: 1,
    source: "orion-db",
    name: day.date + (venue ? " · " + venue : "") + " · day " + day.day_id,
    laps,
    sampleTail,
    sessionInfo: {
      id: "db-orion-day-" + day.day_id,
      driver: day.driver_name || "",
      venue,
      eventType: day.event_type || "",
    },
    flags: marks
      .filter((m) => m.type === "event_flag" && (m.notes || "").trim())
      .map((m) => ({ id: "db:" + m.start_time, t: m.start_time, label: (m.notes || "").trim(), saved: true })),
  };
}
