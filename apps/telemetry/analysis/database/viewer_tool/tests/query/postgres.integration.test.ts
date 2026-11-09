import { Pool } from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { ReadOnlyDatabase } from "../../src/lib/motec/db";
import { getSettings, type Settings } from "../../src/lib/motec/config";
import { CursorCodec } from "../../src/lib/query/cursor";
import { createQueryHandler } from "../../src/lib/query/http";
import { PostgresSource } from "../../src/lib/query/postgres";
import { createQueryService } from "../../src/lib/query/service";
import type { QueryPlan } from "../../src/lib/query/types";
import type { Signal } from "../../src/lib/signals/catalog";

const url = process.env.QUERY_TEST_DATABASE_URL;
const start = Date.parse("2026-11-08T12:00:00Z");
const version = "a".repeat(64);
const speed: Signal = { signal: "controls.brake_pressure_f", group: "controls", value_type: "float", repeated: false,
  module: "vcu", unit: "psi", rate_hz: 100, channel_type: "telemetry" };
const age: Signal = { ...speed, signal: "board_status.hvc_last_seen_s", group: "board_status", channel_type: "health", unit: "s" };
const flag: Signal = { ...speed, signal: "controls.enable", value_type: "bool" };
const vector: Signal = { ...speed, signal: "dynamics.gps", group: "dynamics", repeated: true };
const allSignals = [speed, age, flag, vector];
const plan: QueryPlan = { car: "orion", signals: [speed], t0: start, t1: start + 1000, rate: null, agg: "raw" };

// Fixture writes are isolated to an explicitly disposable database.
describe.runIf(Boolean(url))("shared read-only PostgreSQL integration", () => {
  let source: PostgresSource;
  let fixtures: Pool;
  let settings: Settings;
  beforeAll(async () => {
    const name = decodeURIComponent(new URL(url!).pathname.slice(1));
    if (!name.startsWith("telemetry_query_test")) throw new Error("Integration tests require a disposable telemetry_query_test database.");
    fixtures = new Pool({ connectionString: url! });
    await fixtures.query(`
      CREATE TABLE packet (packet_id bigint PRIMARY KEY, time bigint NOT NULL);
      CREATE TABLE controls (packet_id bigint PRIMARY KEY REFERENCES packet, brake_pressure_f real, enable boolean);
      CREATE TABLE board_status (packet_id bigint PRIMARY KEY REFERENCES packet, hvc_last_seen_s real);
      CREATE TABLE dynamics (packet_id bigint PRIMARY KEY REFERENCES packet, gps double precision[]);
    `);
    for (const [packet, offset, value] of [["1", 0, 1], ["2", 100, 3], ["10", 100, 5], ["3", 300, 7], ["4", 700, 9], ["9007199254740993", 700, 11], ["5", 1000, 100], ["6", -1, -100]] as const) {
      await fixtures.query("INSERT INTO packet VALUES ($1, $2)", [packet, start + offset]);
      await fixtures.query("INSERT INTO controls VALUES ($1, $2, $3)", [packet, value, BigInt(packet) % BigInt(2) === BigInt(0)]);
      await fixtures.query("INSERT INTO board_status VALUES ($1, $2)", [packet, value / 10]);
      if (packet !== "4") await fixtures.query("INSERT INTO dynamics VALUES ($1, $2)", [packet, [30.1, -97.2]]);
    }
    const connection = new URL(url!);
    settings = {
      ...getSettings("orion"), dbHost: connection.hostname,
      dbPort: Number(connection.port || 5432), dbName: name,
      dbUser: decodeURIComponent(connection.username || process.env.USER || ""),
      dbPassword: decodeURIComponent(connection.password || "test"), dbSslmode: "disable",
      usePostgres: true,
    };
    source = new PostgresSource(() => new ReadOnlyDatabase(settings));
  }, 35000);
  afterAll(async () => { await fixtures?.end(); });

  it("uses half-open ranges and numeric ordering for tied timestamps", async () => {
    const rows = await source.read(plan, null, 100);
    expect(rows.map(row => row.value)).toEqual([1, 3, 5, 7, 9, 11]);
    expect(rows.map(row => row.position.packet)).toEqual(["1", "2", "10", "3", "4", "9007199254740993"]);
    expect((await source.read(plan, rows[1].position, 100)).map(row => row.value)).toEqual([5, 7, 9, 11]);
  });
  it("preserves arrays, zero/false values, large packet IDs, and missing groups", async () => {
    const rows = await source.read({ ...plan, signals: [flag, vector, age] }, null, 100);
    expect(rows.find(row => row.position.signal === flag.signal)?.value).toBe(false);
    expect(rows.find(row => row.position.signal === vector.signal)?.value).toEqual([30.1, -97.2]);
    expect(rows.filter(row => row.position.signal === vector.signal)).toHaveLength(5);
    expect(rows.some(row => row.position.packet === "9007199254740993")).toBe(true);
    expect(rows.filter(row => row.position.signal === age.signal)).toHaveLength(6);
  });
  it("does not skip a signal when a page ends within one packet", async () => {
    const selected = { ...plan, signals: [speed, flag, vector] };
    const complete = await source.read(selected, null, 100);
    const page = await source.read(selected, null, 2);
    const remaining = await source.read(selected, page[1].position, 100);
    expect([...page, ...remaining]).toEqual(complete);
  });
  it("rejects time buckets rather than silently returning undownsampled data", async () => {
    await expect(source.read({ ...plan, agg: "mean", rate: 2 }, null, 100))
      .rejects.toMatchObject({ code: "unsupported_downsampling" });
  });
  it("pages the HTTP endpoint through the shared database helper without missing observations", async () => {
    const handler = createQueryHandler(createQueryService(source,
      async car => ({ car, schema_version: version, signals: allSignals }), new CursorCodec("integration-key")));
    const values: unknown[] = [];
    let token: string | null = null;
    do {
      const response = await handler(new Request("http://localhost/api/query", { method: "POST", body: JSON.stringify({
        car: "orion", signals: [speed.signal], t0: new Date(start).toISOString(), t1: new Date(start + 1000).toISOString(),
        page_size: 2, page_token: token,
      }) }));
      expect(response.status).toBe(200);
      const page = await response.json();
      values.push(...page.data.map((row: { value: unknown }) => row.value));
      token = page.next_page_token;
    } while (token);
    expect(values).toEqual([1, 3, 5, 7, 9, 11]);
  });
});
