import { gatewayRows } from "../query/client";
import { storageContract } from "./artemisContract";
export { storageContract } from "./artemisContract";

export type BucketAggregate = "avg" | "min" | "max" | "last";
export type BucketRow = Record<string, unknown> & { session_start_ms: bigint; time: bigint };

export function bucketWidth(groups: string[], requestedMs = 100, fields?: string[]): number {
  const periods = storageContract().pools
    .filter(p => groups.includes(p.group) && p.frequency_hz > 0 && (!fields || p.fields.some(f => fields.includes(f.name))))
    .map(p => Math.ceil(1000 / p.frequency_hz));
  return Math.max(1, Math.ceil(requestedMs), ...periods);
}

export async function readBuckets(groups: string[], startMs: number, endMs: number, requestedMs = 100, aggregate: BucketAggregate = "avg", fields?: string[]) {
  if (!Number.isSafeInteger(startMs) || !Number.isSafeInteger(endMs) || endMs <= startMs || !Number.isFinite(requestedMs) || requestedMs <= 0) throw new Error("Invalid telemetry bounds");
  const pools = storageContract().pools.filter(p => groups.includes(p.group));
  if (groups.some(g => !pools.some(p => p.group === g))) throw new Error("Unknown Artemis group");
  const width = bucketWidth(groups, requestedMs, fields);
  const selections = pools.flatMap(p => p.fields.filter(f => !fields || fields.includes(f.name)).map(f => ({ signal: `${p.group}.${f.name}`, numeric: ["double precision", "integer", "bigint", "smallint", "numeric"].includes(f.sql_type) && (aggregate !== "avg" || f.aggregate === "avg") })));
  const merged = new Map<string, BucketRow>();
  for (const numeric of [true, false]) {
    const signals = selections.filter(s => s.numeric === numeric).map(s => s.signal);
    for (let i = 0; i < signals.length; i += 50) {
      for (let from = startMs; from < endMs;) {
        const to = Math.min(endMs, Math.floor((from + width * 10000) / width) * width);
        const rows = await gatewayRows({ signals: signals.slice(i, i + 50), t0: new Date(from).toISOString(), t1: new Date(to).toISOString(), bucket_ms: width, max_points: 10000, agg: !numeric ? "last" : aggregate === "avg" ? "mean" : aggregate });
        for (const value of rows) {
          const time = BigInt(Date.parse(value.ts));
          const key = `${value.session_start_ms}:${time}`;
          const row = merged.get(key) || { session_start_ms: BigInt(value.session_start_ms), time };
          const [group, field] = value.signal.split(".");
          const target = (row[group] ||= {}) as Record<string, unknown>;
          target[field] = value.value;
          merged.set(key, row);
        }
        from = to;
      }
    }
  }
  return [...merged.values()].sort((a, b) => Number(a.time - b.time));
}
