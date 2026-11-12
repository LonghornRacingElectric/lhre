import { expect, it, vi } from "vitest";
import { readBuckets } from "../../src/lib/motec/artemisStorage";
import { gatewayRows } from "../../src/lib/query/client";

vi.mock("../../src/lib/query/client", () => ({ gatewayRows: vi.fn() }));
vi.mock("../../src/lib/motec/artemisContract", () => ({
  storageContract: () => ({ pools: [{
    table: "controls_100hz", group: "controls", frequency_hz: 100,
    fields: Array.from({ length: 10 }, (_, i) => ({ name: `value_${i}`, sql_type: "double precision", aggregate: "avg" })),
  }] }),
}));

it("preserves partial edge buckets and peaks across bounded request windows", async () => {
  const start = 1_700_000_000_123;
  const end = start + 200_000;
  const calls: Array<Record<string, unknown>> = [];
  vi.mocked(gatewayRows).mockImplementation(async query => {
    calls.push(query);
    const from = Date.parse(String(query.t0)), to = Date.parse(String(query.t1));
    const rows = [];
    for (let bucket = Math.floor(from / 100) * 100; bucket < to; bucket += 100) {
      for (const signal of query.signals as string[]) {
        rows.push({ ts: new Date(bucket).toISOString(), sample_time_ns: String(BigInt(bucket) * 1_000_000n), session_start_ms: "1", signal, value: bucket === 1_700_000_100_000 ? 80 : 0 });
      }
    }
    return rows;
  });
  const rows = await readBuckets(["controls"], start, end, 100, "max");
  expect(rows).toHaveLength(2001);
  expect(rows[0].time).toBe(1_700_000_000_100n);
  expect(rows.at(-1)?.time).toBe(1_700_000_200_100n);
  expect(rows.find(row => row.time === 1_700_000_100_000n)?.controls).toEqual(Object.fromEntries(Array.from({ length: 10 }, (_, i) => [`value_${i}`, 80])));
  expect(calls.map(query => [Date.parse(String(query.t0)), Date.parse(String(query.t1))])).toEqual([
    [start, 1_700_000_100_100], [1_700_000_100_100, 1_700_000_200_100], [1_700_000_200_100, end],
  ]);
});
