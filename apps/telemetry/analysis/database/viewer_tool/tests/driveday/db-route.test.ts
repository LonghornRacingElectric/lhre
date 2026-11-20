import fc from "fast-check";
import { NextRequest } from "next/server";
import { gunzipSync } from "zlib";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { convertSession } from "../../src/lib/driveday/convert";
import type { DbMark, DbRow } from "../../src/lib/driveday/db";
import { Session } from "../../src/lib/driveday/session";
import { rebuildRun } from "../../src/lib/driveday/rebuild";
import { synthSession } from "./fixtures/synth";

/* The database route, with the sign-in and the Postgres pool replaced: what it lets through, what it asks the
   database, and that what it sends back opens in the viewer like an exported JSON file. */

const auth = vi.hoisted(() => ({ token: { sub: "driver" } as object | null }));
const db = vi.hoisted(() => ({ query: vi.fn() }));
vi.mock("next-auth/jwt", () => ({ getToken: async () => auth.token }));
vi.mock("@/lib/db-telemetry", () => ({ default: db }));
const { GET } = await import("../../src/app/api/driveday/db/route");

const J = synthSession({ laps: 4, sessionId: "route" });
const t0 = J.laps[0].startMs, t1 = J.laps[J.laps.length - 1].endMs;
const ROWS: DbRow[] = J.sampleTail.filter((s) => s.t >= t0 && s.t <= t1).map((s) => ({
  t: s.t, lat: s.lat, lon: s.lon, cells_temps: s.cellTemps ?? null,
  ...s.values, gps_speed: s.speed ?? null, hv_pack_v: s.hv_pack_v ?? null, regen_energy: 0,
}));
const MARKS: DbMark[] = [
  ...J.laps.map((l) => ({ type: "lap", start_time: l.startMs, end_time: l.endMs, notes: null })),
  { type: "event_flag", start_time: J.laps[1].startMs + 1000, end_time: null, notes: "Hit Cone" },
];
const DAY = { day_id: 12, date: "2026-11-01", track_name: "UTA", event_type: "Autocross", driver_name: "Test Driver" };

/** Answer each query by which table it reads. */
function database({ day = DAY as typeof DAY | null, marks = MARKS, rows = ROWS } = {}) {
  db.query.mockImplementation(async (sql: string) => {
    if (sql.includes("distinct on")) return { rows };
    if (sql.includes("from classifier where")) return { rows: marks };
    if (sql.includes("limit 200")) return { rows: [{ ...DAY, laps: 4, flags: 1 }] };
    return { rows: day ? [day] : [] };
  });
}
const get = (q = "", headers: Record<string, string> = {}) => GET(new NextRequest("http://lapdog.test/api/driveday/db" + q, { headers }));

beforeEach(() => {
  auth.token = { sub: "driver" };
  db.query.mockReset();
  database();
});

describe("GET /api/driveday/db", () => {
  it("needs a sign-in, and asks the database nothing without one", async () => {
    auth.token = null;
    const r = await get("?day=12");
    expect(r.status).toBe(401);
    expect(db.query).not.toHaveBeenCalled();
  });

  it("lists the drive days that have laps", async () => {
    const r = await get();
    expect(r.status).toBe(200);
    expect((await r.json()).days[0]).toMatchObject({ day_id: 12, laps: 4 });
  });

  it("turns away any day that is not a drive day id before it reaches SQL", async () => {
    await fc.assert(fc.asyncProperty(fc.oneof(fc.string(), fc.constantFrom("1; drop table packet", "0x1f", "-3", "1e3", "123456", " 7", "7 ", "", "١٢")), async (day) => {
      fc.pre(!/^\d{1,5}$/.test(day));
      db.query.mockClear();
      const r = await get("?day=" + encodeURIComponent(day));
      expect(r.status).toBe(400);
      expect(db.query).not.toHaveBeenCalled();
    }), { seed: 20261120, numRuns: 200 });
  });

  it("passes the day id as a parameter, never as SQL text", async () => {
    await get("?day=12");
    for (const [sql, params] of db.query.mock.calls.slice(0, 2)) {
      expect(sql).not.toContain("12");
      expect(params).toEqual(["12"]);
    }
  });

  it("reads samples only between the first lap start and the last lap end, lap by lap", async () => {
    await get("?day=12");
    const [, params] = db.query.mock.calls.find(([sql]) => sql.includes("distinct on"))!;
    expect(params).toEqual([t0, t1, J.laps.map((l) => l.startMs), J.laps.map((l) => l.endMs)]);
  });

  it("says which day is missing, or that it has no timed laps", async () => {
    database({ day: null });
    let r = await get("?day=99");
    expect(r.status).toBe(404);
    expect((await r.json()).error).toBe("No drive day 99.");
    database({ marks: MARKS.filter((m) => m.type !== "lap") });
    r = await get("?day=12");
    expect(r.status).toBe(404);
    expect((await r.json()).error).toMatch(/no timed laps/);
  });

  it("reports a database failure as a 500 with the reason", async () => {
    db.query.mockRejectedValue(new Error("connection refused"));
    const r = await get("?day=12");
    expect(r.status).toBe(500);
    expect((await r.json()).error).toBe("Database error: connection refused");
  });

  it("gzips only for a client that accepts it, and either way the day opens in the viewer", async () => {
    const plain = await get("?day=12");
    expect(plain.headers.get("content-encoding")).toBeNull();
    const zipped = await get("?day=12", { "accept-encoding": "gzip, deflate, br" });
    expect(zipped.headers.get("content-encoding")).toBe("gzip");
    const a = JSON.parse(await plain.text());
    const b = JSON.parse(gunzipSync(Buffer.from(await zipped.arrayBuffer())).toString());
    expect(b).toEqual(a);
    const s = new Session(rebuildRun(convertSession(b, "orion-day-12.json", [])[0].D));
    expect(s.runs.map((l) => l.dur)).toEqual(J.laps.map((l) => l.durationMs));
  });
});
