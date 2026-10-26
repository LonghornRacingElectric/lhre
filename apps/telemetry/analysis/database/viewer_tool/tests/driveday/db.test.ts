import { describe, expect, it } from "vitest";
import { convertSession } from "../../src/lib/driveday/convert";
import { dbSessionExport, isTimedLap, MAX_LAP_MS, type DbMark, type DbRow } from "../../src/lib/driveday/db";
import { synthSession } from "./fixtures/synth";

/* The synthetic day as the database holds it: packet rows inside the lap windows, classifier laps and flags. */
describe("drive day from the database", () => {
  const J = synthSession({ laps: 6 });
  const t0 = J.laps[0].startMs, t1 = J.laps[J.laps.length - 1].endMs;
  const rows: DbRow[] = J.sampleTail.filter((s) => s.t >= t0 && s.t <= t1).map((s) => ({
    t: s.t, lat: s.lat, lon: s.lon, cells_temps: s.cellTemps ?? null,
    ...s.values, gps_speed: s.speed ?? null, hv_pack_v: s.hv_pack_v ?? null, regen_energy: 0,
  }));
  const marks: DbMark[] = [
    ...J.laps.map((l) => ({ type: "lap", start_time: l.startMs, end_time: l.endMs, notes: null })),
    { type: "lap", start_time: t1, end_time: t1 + MAX_LAP_MS + 1, notes: null }, // the gap before the next run
    { type: "lap", start_time: t1 + MAX_LAP_MS + 1, end_time: null, notes: null }, // left open
    { type: "event_flag", start_time: J.laps[2].startMs + 1000, end_time: null, notes: "Hit Cone" },
    { type: "event_flag", start_time: J.laps[3].startMs + 1000, end_time: null, notes: "Off-track" },
  ];
  const E = dbSessionExport({ day_id: 7, date: "2026-10-18", track_name: "UTA", event_type: "Autocross", driver_name: "Test Driver" }, marks, rows);
  const { D } = convertSession(E, "orion-day-7.json", [])[0];
  const timed = D.laps.filter((l) => l.kind === "lap");

  it("keeps the timed laps and drops the gaps and open laps", () => {
    expect(marks.filter(isTimedLap).length).toBe(J.laps.length);
    expect(timed.map((l) => l.dur)).toEqual(J.laps.map((l) => l.durationMs));
  });

  it("counts lap energy from the VCU counters", () => {
    const at = (t: number) => rows.filter((r) => r.t >= J.laps[0].startMs && r.t <= t).map((r) => r.net_energy!);
    const e = at(J.laps[0].endMs);
    expect(timed[0].wh).toBeCloseTo(e[e.length - 1] - e[0], 1);
  });

  it("skips a glitch in the energy counters", () => {
    const glitch = rows.map((r) => ({ ...r }));
    const last = J.laps.length - 1; // by then the count is well above what one step can add
    const i = glitch.findIndex((r) => r.t > J.laps[last].startMs + 5000);
    glitch[i].net_energy = 0; // one frame read as zero, then the real count again
    const G = dbSessionExport({ day_id: 7, date: "", track_name: null, event_type: null, driver_name: null }, marks, glitch);
    // the two steps around the zero frame are lost with it; counting the jump back would add the whole count
    expect(Math.abs(G.laps[last].energyWh - E.laps[last].energyWh)).toBeLessThan(2);
  });

  it("drops a GPS that repeats one position for a whole lap", () => {
    const frozen = rows.map((r) => (r.t >= J.laps[1].startMs && r.t <= J.laps[1].endMs ? { ...r, lat: rows[0].lat, lon: rows[0].lon } : r));
    const F = dbSessionExport({ day_id: 7, date: "", track_name: null, event_type: null, driver_name: null }, marks, frozen);
    const inLap = F.sampleTail.filter((s) => s.t > J.laps[1].startMs && s.t < J.laps[1].endMs);
    expect(inLap.every((s) => Number.isNaN(s.lat))).toBe(true);
    expect(F.sampleTail.filter((s) => s.t > J.laps[0].startMs && s.t < J.laps[0].endMs).every((s) => Number.isFinite(s.lat))).toBe(true);
  });

  it("turns Hit Cone flags into cones and names the run after the day", () => {
    expect(D.coneEvents?.length).toBe(1);
    expect(D.meta.sessId).toBe("db-orion-day-7");
    expect(D.meta.name).toBe("2026-10-18 · UTA · day 7");
    expect(E.flags?.map((f) => f.label)).toEqual(["Hit Cone", "Off-track"]);
  });
});
