// LAPDOG's database source. GET lists the drive days that have laps; GET ?day=<day_id> returns that day as a
// Trackside session export (laps and flags from `classifier`, samples from the packet tables), so the viewer
// imports it the same way as a JSON file.
import { NextRequest, NextResponse } from "next/server";
import { getToken } from "next-auth/jwt";
import { gzipSync } from "zlib";
import pool from "@/lib/db-telemetry";
import { DB_CHANNELS, dbSessionExport, isTimedLap, type DbDay, type DbMark, type DbRow } from "@/lib/driveday/db";

export const dynamic = "force-dynamic";

const DAYS_SQL = `
  select d.day_id::int, d.date::text, d.track_name, e.event_type, dr.driver_name,
    (select count(*) from classifier c where c.day_id = d.day_id and c.type = 'lap')::int as laps,
    (select count(*) from classifier c where c.day_id = d.day_id and c.type = 'event_flag')::int as flags
  from drive_day d
  left join lut_event_type e on e.type_id = d.event_type
  left join lut_driver dr on dr.driver_id = d.driver_id
  where exists (select 1 from classifier c where c.day_id = d.day_id and c.type = 'lap')
  order by d.day_id desc limit 200`;

const DAY_SQL = `
  select d.day_id::int, d.date::text, d.track_name, e.event_type, dr.driver_name
  from drive_day d
  left join lut_event_type e on e.type_id = d.event_type
  left join lut_driver dr on dr.driver_id = d.driver_id
  where d.day_id = $1`;

const MARKS_SQL = `
  select type, start_time::float8 as start_time, end_time::float8 as end_time, notes
  from classifier where day_id = $1 and type in ('lap', 'event_flag') order by start_time`;

// One packet per 100 ms (10 Hz), inside the lap windows only. GPS prefers the double-precision fix.
const COLUMNS: Record<(typeof DB_CHANNELS)[number], string> = {
  accel_pedal_travel: "d", steer_col_angle: "d", gps_speed: "d",
  brake_pressure_f: "c", brake_pressure_rbll: "c", motor_speed: "c", torque_feedback: "c", net_energy: "c", regen_energy: "c",
  dc_bus_v: "k", dc_bus_current: "k", hv_pack_v: "k", soc_estimate: "k",
  motor_temp: "th", inverter_hotspot_temp: "th", gate_driver_temp: "th", coolant_temp: "th",
  min_cell_voltage: "th", max_cell_voltage: "th", module_a_temp: "th", module_b_temp: "th", module_c_temp: "th",
};
const SAMPLES_SQL = `
  select distinct on (p.time / 100) p.time::float8 as t,
    coalesce(d.gps_precise[1], d.gps[1]) as lat, coalesce(d.gps_precise[2], d.gps[2]) as lon, k.cells_temps,
    ${DB_CHANNELS.map((k) => `${COLUMNS[k]}.${k}`).join(", ")}
  from packet p
  left join dynamics d on d.packet_id = p.packet_id
  left join controls c on c.packet_id = p.packet_id
  left join pack k on k.packet_id = p.packet_id
  left join thermal th on th.packet_id = p.packet_id
  where p.time between $1 and $2
    and exists (select 1 from unnest($3::bigint[], $4::bigint[]) w(a, b) where p.time between w.a and w.b)
  order by p.time / 100, p.time
  limit 200000`;

export async function GET(req: NextRequest) {
  // Same sign-in as the pages: the middleware skips /api (see src/app/api/logsync).
  if (!(await getToken({ req }))) return NextResponse.json({ error: "sign in required" }, { status: 401 });
  const day = req.nextUrl.searchParams.get("day");
  try {
    if (day === null) {
      const { rows } = await pool.query(DAYS_SQL);
      return NextResponse.json({ days: rows });
    }
    if (!/^\d{1,5}$/.test(day)) return NextResponse.json({ error: "day must be a drive day id" }, { status: 400 });
    const [{ rows: [info] }, { rows: marks }] = await Promise.all([
      pool.query<DbDay>(DAY_SQL, [day]),
      pool.query<DbMark>(MARKS_SQL, [day]),
    ]);
    if (!info) return NextResponse.json({ error: "No drive day " + day + "." }, { status: 404 });
    const laps = marks.filter(isTimedLap);
    if (!laps.length) return NextResponse.json({ error: "Drive day " + day + " has no timed laps." }, { status: 404 });
    const { rows } = await pool.query<DbRow>(SAMPLES_SQL, [
      Math.min(...laps.map((l) => l.start_time)), Math.max(...laps.map((l) => l.end_time)),
      laps.map((l) => l.start_time), laps.map((l) => l.end_time),
    ]);
    const body = JSON.stringify(dbSessionExport(info, marks, rows));
    // A day is tens of MB of JSON and next start does not compress (compress: false keeps SSE unbuffered).
    const gzip = /\bgzip\b/.test(req.headers.get("accept-encoding") || "");
    return new NextResponse(gzip ? new Uint8Array(gzipSync(body)) : body, {
      headers: { "content-type": "application/json", ...(gzip && { "content-encoding": "gzip" }) },
    });
  } catch (e) {
    return NextResponse.json({ error: "Database error: " + (e as Error).message }, { status: 500 });
  }
}
