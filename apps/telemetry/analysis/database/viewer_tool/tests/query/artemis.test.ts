import { describe, expect, it, vi } from "vitest";
import { ArtemisSource } from "../../src/lib/query/artemis";
import { LIMITS, type QueryPlan } from "../../src/lib/query/types";
import type { Signal } from "../../src/lib/signals/catalog";

const signal: Signal = { signal: "controls.apps1_travel", group: "controls", value_type: "float",
  repeated: false, module: "vcu", unit: "%", rate_hz: 50, channel_type: "telemetry" };
const plan: QueryPlan = { car: "artemis", signals: [signal], t0: 1000, t1: 2000, agg: "raw", rate: null };
const contract = { storage_id: "fixture", pools: [
  { table: "controls_333hz", group: "controls", frequency_hz: 333, fields: [
    { name: "apps1_travel", sql_type: "double precision", aggregate: "avg" as const },
    { name: "enable", sql_type: "boolean", aggregate: "last" as const },
  ] },
  { table: "pack_1hz", group: "pack", frequency_hz: 1, fields: [
    { name: "cells_v", sql_type: "double precision[]", aggregate: "last" as const },
  ] },
] };

describe("Artemis pool reader", () => {
  it("uses native pool placement rather than uplink rate and parameterizes bounds", async () => {
    const query = vi.fn(async () => []);
    await new ArtemisSource(() => ({ query }), () => contract).read(plan, null, 10);
    expect(query).toHaveBeenCalledWith(expect.stringContaining('FROM public."controls_333hz"'), [1000, 2000, LIMITS.sampleBatch]);
    expect(query.mock.calls[0][0]).not.toMatch(/packet|JOIN/);
  });

  it("rejects catalog names outside the generated pool contract before connecting", async () => {
    const connect = vi.fn(() => ({ query: vi.fn(async () => []) }));
    const source = new ArtemisSource(connect, () => contract);
    for (const changes of [{ signal: "controls.missing" }, { signal: 'controls.x";drop table controls;--' }, { group: "other" }]) {
      await expect(source.read({ ...plan, signals: [{ ...signal, ...changes }] }, null, 10))
        .rejects.toMatchObject({ code: "schema_unavailable" });
    }
    expect(connect).not.toHaveBeenCalled();
  });

  it("merges unequal sample rates and resumes within a session/time sample", async () => {
    const query = vi.fn(async <T>(sql: string) => (sql.includes('"pack_1hz"') ? [
      { time: "1005", session_start_ms: "2", "pack.cells_v": [3.7, 3.8] },
    ] : [
      { time: "1000", session_start_ms: "2", "controls.apps1_travel": 0, "controls.enable": false },
      { time: "1010", session_start_ms: "2", "controls.apps1_travel": 10 },
      { time: "1010", session_start_ms: "10", "controls.apps1_travel": 20 },
    ]) as T[]);
    const source = new ArtemisSource(() => ({ query }), () => contract);
    const selected = { ...plan, signals: [signal, { ...signal, signal: "controls.enable", value_type: "bool" },
      { ...signal, signal: "pack.cells_v", group: "pack", repeated: true }] };
    const all = await source.read(selected, null, 100);
    expect(all.map(row => row.value)).toEqual([0, false, [3.7, 3.8], 10, 20]);
    const page = await source.read(selected, null, 1);
    expect([...page, ...await source.read(selected, page[0].position, 100)]).toEqual(all);
    expect(all.every(row => !("packet" in row.position))).toBe(true);
  });

  it("preserves empty arrays and skips SQL NULL arrays", async () => {
    const query = vi.fn(async <T>() => [
      { time: "1000", session_start_ms: "2", "pack.cells_v": null },
      { time: "1001", session_start_ms: "2", "pack.cells_v": [] },
    ] as T[]);
    const rows = await new ArtemisSource(() => ({ query }), () => contract)
      .read({ ...plan, signals: [{ ...signal, signal: "pack.cells_v", group: "pack", repeated: true }] }, null, 100);
    expect(rows.map(row => row.value)).toEqual([[]]);
  });

  it("rejects legacy cars and maps database timeouts without exposing details", async () => {
    const connect = vi.fn(() => ({ query: vi.fn(async () => { throw Object.assign(new Error("private"), { code: "57014" }); }) }));
    const source = new ArtemisSource(connect, () => contract);
    await expect(source.read({ ...plan, car: "orion" }, null, 10)).rejects.toMatchObject({ code: "unsupported_car" });
    expect(connect).not.toHaveBeenCalled();
    await expect(source.read(plan, null, 10)).rejects.toMatchObject({ code: "query_timeout", status: 503 });
  });
});
