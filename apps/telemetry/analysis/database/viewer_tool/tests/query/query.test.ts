import { describe, expect, it, vi } from "vitest";
import { CursorCodec } from "../../src/lib/query/cursor";
import { createQueryHandler } from "../../src/lib/query/http";
import { parseQuery } from "../../src/lib/query/request";
import { createQueryService } from "../../src/lib/query/service";
import { ReadOnlyDatabase } from "../../src/lib/motec/db";
import { PostgresSource } from "../../src/lib/query/postgres";
import { LIMITS, QueryError, type Observation, type Position, type QueryPlan, type TelemetrySource } from "../../src/lib/query/types";
import type { Signal } from "../../src/lib/signals/catalog";

const version = "a".repeat(64), newer = "b".repeat(64);
const start = Date.parse("2026-11-08T12:00:00Z");
const body = { car: "orion", signals: ["controls.speed"], t0: "2026-11-08T12:00:00Z", t1: "2026-11-08T12:00:01Z" };
const signal: Signal = { signal: "controls.speed", group: "controls", value_type: "float", repeated: false,
  module: "vcu", unit: "m/s", rate_hz: 100, channel_type: "telemetry" };
const definitions = [signal,
  { ...signal, signal: "controls.flag", value_type: "bool" },
  { ...signal, signal: "controls.vector", repeated: true },
  { ...signal, signal: "controls.event", value_type: "bool", channel_type: "event" as const }];
const catalogs = vi.fn(async (car: string, selected: string | null) => ({ car, schema_version: selected || version, signals: definitions }));
const observations: Observation[] = [1, 2, 10, 11].map(packet => ({
  position: { ts: start + 100, packet: String(packet), signal: signal.signal }, value: packet,
}));
const compare = (a: Position, b: Position) => a.ts - b.ts || (BigInt(a.packet) < BigInt(b.packet) ? -1 : BigInt(a.packet) > BigInt(b.packet) ? 1 : a.signal.localeCompare(b.signal));
function sourceFor(rows = observations): TelemetrySource {
  return { read: vi.fn(async (_plan, after, limit) => rows.filter(row => !after || compare(row.position, after) > 0).slice(0, limit)) };
}
const service = (source = sourceFor()) => createQueryService(source, catalogs, new CursorCodec("test-key"));
const post = (value: unknown, accept?: string) => new Request("http://localhost/api/query", {
  method: "POST", headers: { "Content-Type": "application/json", ...(accept ? { Accept: accept } : {}) }, body: JSON.stringify(value),
});

describe("query validation", () => {
  it("normalizes timezones and caps page sizes", () => {
    const result = parseQuery({ ...body, t0: "2026-11-08T06:00:00-06:00", page_size: 999999 });
    expect(result.t0).toBe(start);
    expect(result.pageSize).toBe(LIMITS.maxPage);
    expect(result.agg).toBe("raw");
    for (const fraction of ["", ".1", ".12", ".123"]) {
      expect(parseQuery({ ...body, t0: `2026-11-08T12:00:00${fraction}Z` }).t0).toBe(Date.parse(`2026-11-08T12:00:00${fraction}Z`));
    }
    expect(() => parseQuery({ ...body, t0: "2026-11-08T12:00:00.1234Z" })).toThrow(QueryError);
  });
  it("rejects malformed dates, unsupported rates, duplicate names, and ambiguous aggregation", () => {
    for (const changes of [
      { t0: "2026-11-08T12:00:00" }, { t0: "2026-02-30T12:00:00Z" }, { t0: body.t1 },
      { t0: "2026-11-08T24:00:00Z" }, { signals: [] }, { signals: ["controls.speed", "controls.speed"] },
      { signals: ["controls.speed;drop"] }, { car: "../orion" }, { rate: 10 }, { agg: "mean" },
      { agg: {} }, { agg: "sum", rate: 10 }, { agg: "mean", rate: -1 }, { agg: "mean", rate: 1001 },
      { page_size: 1.5 }, { page_token: "" }, { extra: true },
    ]) expect(() => parseQuery({ ...body, ...changes })).toThrow(QueryError);
  });
});

describe("query service and cursors", () => {
  it("pages tied timestamps using numeric packet IDs with no repeats or skips", async () => {
    const query = service();
    const first = await query({ ...body, page_size: 2 });
    expect(first.data.map(row => row.value)).toEqual([1, 2]);
    expect(first.next_page_token).toBeTruthy();
    const second = await query({ ...body, page_size: 2, page_token: first.next_page_token });
    expect(second.data.map(row => row.value)).toEqual([10, 11]);
    expect(second.next_page_token).toBeNull();
  });
  it("pins a resumed page to the original catalog despite a new current version", async () => {
    const reader = vi.fn(async (car: string, selected: string | null) => ({ car, schema_version: selected || newer, signals: definitions }));
    const query = createQueryService(sourceFor(), reader, new CursorCodec("key"));
    const request = parseQuery({ ...body, page_size: 1 });
    const token = new CursorCodec("key").encode(request, version, observations[0].position);
    expect((await query({ ...body, page_size: 1, page_token: token })).schema_version).toBe(version);
    expect(reader).toHaveBeenCalledWith("orion", version);
  });
  it("rejects tampered, changed-query, and expired tokens before reading a source", async () => {
    const source = sourceFor();
    const query = service(source);
    const first = await query({ ...body, page_size: 1 });
    const token = first.next_page_token!;
    for (const input of [
      { ...body, page_size: 1, page_token: token + "a" },
      { ...body, car: "artemis", page_size: 1, page_token: token },
      { ...body, t1: "2026-11-08T12:00:02Z", page_size: 1, page_token: token },
    ]) await expect(query(input)).rejects.toMatchObject({ code: "invalid_page_token" });
    const expired = new CursorCodec("test-key", () => 1).encode(parseQuery(body), version, observations[0].position, 2);
    await expect(query({ ...body, page_token: expired })).rejects.toMatchObject({ code: "invalid_page_token" });
    expect(source.read).toHaveBeenCalledTimes(1);
  });
  it("validates cursor positions even when their signatures are valid", () => {
    const codec = new CursorCodec("test-key");
    const request = parseQuery(body);
    for (const position of [
      { ...observations[0].position, ts: start + 0.5 },
      { ...observations[0].position, ts: start - 1 },
      { ...observations[0].position, packet: "invalid" },
      { ...observations[0].position, packet: "9223372036854775808" },
      { ...observations[0].position, signal: "controls.other" },
    ]) expect(() => codec.decode(codec.encode(request, version, position), request)).toThrow(QueryError);
  });
  it("validates selected signals and aggregations before SQL", async () => {
    const source = sourceFor();
    const query = service(source);
    await expect(query({ ...body, signals: ["controls.missing"] })).rejects.toMatchObject({ status: 404 });
    for (const name of ["controls.flag", "controls.vector"]) {
      await expect(query({ ...body, signals: [name], agg: "mean", rate: 10 })).rejects.toMatchObject({ status: 400 });
    }
    expect(source.read).not.toHaveBeenCalled();
    await query({ ...body, signals: ["controls.event"] });
    expect(source.read).toHaveBeenCalledOnce();
  });
  it("measures UTF-8 response bytes without reserving an arbitrary metadata allowance", async () => {
    const rows = Array.from({ length: 33 }, (_, i) => ({ ...observations[0],
      position: { ...observations[0].position, packet: String(i) }, value: "😀".repeat(16250) }));
    const query = service(sourceFor(rows));
    const first = await query(body);
    expect(first.data).toHaveLength(32);
    expect(Buffer.byteLength(JSON.stringify(first))).toBeLessThanOrEqual(LIMITS.pageBytes);
    const second = await query({ ...body, page_token: first.next_page_token });
    expect(second.data).toHaveLength(1);
    expect(second.next_page_token).toBeNull();
  });
  it("bounds page bytes and returns a continuation rather than truncating history", async () => {
    const rows = Array.from({ length: 40 }, (_, i) => ({ ...observations[0],
      position: { ...observations[0].position, packet: String(i) }, value: "x".repeat(60000) }));
    const query = service(sourceFor(rows));
    const first = await query(body);
    expect(Buffer.byteLength(JSON.stringify(first))).toBeLessThan(LIMITS.pageBytes);
    expect(first.data.length).toBeLessThan(40);
    const second = await query({ ...body, page_token: first.next_page_token });
    expect(first.data.length + second.data.length).toBe(40);
    expect(second.next_page_token).toBeNull();
  });
});

describe("HTTP formats and errors", () => {
  it("returns the version and pagination metadata in JSON", async () => {
    const response = await createQueryHandler(service())(post({ ...body, page_size: 1 }));
    expect(response.status).toBe(200);
    expect(response.headers.get("X-Schema-Version")).toBe(version);
    expect(await response.json()).toMatchObject({ schema_version: version, data: [{ signal: signal.signal, value: 1 }] });
  });
  it("escapes CSV arrays, quotes, commas, and newlines and supplies its next token", async () => {
    const handler = createQueryHandler(async () => ({ schema_version: version, next_page_token: "next", data: [
      { ts: body.t0, signal: signal.signal, value: [1, 2] },
      { ts: body.t0, signal: signal.signal, value: 'a,"b"\nc' },
    ] }));
    const response = await handler(post(body, "text/csv"));
    expect(response.headers.get("X-Next-Page-Token")).toBe("next");
    expect(response.headers.get("Content-Type")).toContain("text/csv");
    const csv = await response.text();
    expect(csv).toContain('"[1,2]"\r\n');
    expect(csv).toContain('"a,""b""\nc"');
    const excludedJson = await handler(post(body, "application/json;q=0, text/csv;q=0.5"));
    expect(excludedJson.headers.get("Content-Type")).toContain("text/csv");
    const wildcard = await handler(post(body, "application/json;q=0, */*;q=0.5"));
    expect(wildcard.headers.get("Content-Type")).toContain("text/csv");
  });
  it("rejects unsupported formats, malformed JSON, and oversized request bodies", async () => {
    const run = vi.fn(service());
    const handler = createQueryHandler(run);
    expect((await handler(post(body, "application/vnd.apache.parquet"))).status).toBe(406);
    expect((await handler(new Request("http://localhost/api/query", { method: "POST", body: "{" }))).status).toBe(400);
    expect((await handler(post({ value: "x".repeat(LIMITS.requestBytes) }))).status).toBe(413);
    expect(run).not.toHaveBeenCalled();
  });
  it("bounds chunked multibyte bodies without relying on Content-Length", async () => {
    const run = vi.fn(service());
    const handler = createQueryHandler(run);
    const chunk = new TextEncoder().encode("😀".repeat(20000));
    const request = new Request("http://localhost/api/query", { method: "POST", duplex: "half",
      body: new ReadableStream({ start(controller) {
        for (let i = 0; i < 4; i++) controller.enqueue(chunk);
        controller.close();
      } }),
    } as RequestInit);
    expect((await handler(request)).status).toBe(413);
    expect(run).not.toHaveBeenCalled();
  });
});

describe("shared PostgreSQL boundary", () => {
  const mapped = { ...signal, signal: "controls.brake_pressure_f" };
  const plan: QueryPlan = { car: "orion", signals: [mapped], t0: start, t1: start + 1000, rate: null, agg: "raw" };
  it("refuses malformed catalog identifiers before querying", async () => {
    const read = vi.fn(async () => []);
    const source = new PostgresSource(() => ({ query: read }));
    await expect(source.read({ ...plan, signals: [{ ...mapped, signal: 'controls.x";drop table packet;--' }] }, null, 10)).rejects.toMatchObject({ code: "schema_unavailable" });
    await expect(source.read({ ...plan, signals: [{ ...mapped, group: "other" }] }, null, 10)).rejects.toMatchObject({ code: "schema_unavailable" });
    expect(read).not.toHaveBeenCalled();
  });
  it("uses parameterized reads and reports PostgreSQL timeouts", async () => {
    const query = vi.fn(async () => { throw Object.assign(new Error("private details"), { code: "57014" }); });
    const connect = vi.fn(() => ({ query }));
    await expect(new PostgresSource(connect).read(plan, null, 10))
      .rejects.toMatchObject({ status: 503, code: "query_timeout" });
    expect(connect).toHaveBeenCalledWith("orion");
    expect(query).toHaveBeenCalledWith(
      expect.stringContaining('LEFT JOIN "controls" ON "controls"."packet_id" = packet."packet_id"'),
      [start, start + 1000, LIMITS.packetBatch]);
  });
  it("continues within a packet when a page ends between its signals", async () => {
    const read = vi.fn(async <T>() => [{ packet_id: BigInt(1), time: BigInt(start), "controls.brake_pressure_f": 0, "controls.enable": false }] as unknown as T[]);
    const source = new PostgresSource(() => ({ query: read }));
    const selected = { ...plan, signals: [mapped, { ...mapped, signal: "controls.enable", value_type: "bool" }] };
    const first = await source.read(selected, null, 1);
    expect(first[0].value).toBe(0);
    const second = await source.read(selected, first[0].position, 1);
    expect(second[0].value).toBe(false);
  });
  it("preserves 64-bit values without converting them to numbers", async () => {
    const read = vi.fn(async <T>() => [{ packet_id: BigInt("9007199254740993"), time: BigInt(start), "controls.brake_pressure_f": BigInt("9007199254740993") }] as unknown as T[]);
    const row = (await new PostgresSource(() => ({ query: read })).read(plan, null, 1))[0];
    expect(row.position.packet).toBe("9007199254740993");
    expect(row.value).toBe("9007199254740993");
  });
  it("uses the shared per-car settings without separate query configuration", async () => {
    vi.stubEnv("ORION_DB_NAME", "orion_history");
    vi.stubEnv("ANGELIQUE_DB_NAME", "angelique_history");
    const query = vi.spyOn(ReadOnlyDatabase.prototype, "query").mockResolvedValue([]);
    try {
      await new PostgresSource().read(plan, null, 1);
      await new PostgresSource().read({ ...plan, car: "angelique" }, null, 1);
      const names = query.mock.contexts.map(db =>
        (db as unknown as { settings: { orionDbName: string } }).settings.orionDbName);
      expect(names).toEqual(["orion_history", "angelique_history"]);
    } finally { query.mockRestore(); vi.unstubAllEnvs(); }
  });
  it("rejects cars without shared database settings", async () => {
    await expect(new PostgresSource().read({ ...plan, car: "artemis" }, null, 1))
      .rejects.toMatchObject({ status: 503, code: "database_unavailable" });
  });
});
