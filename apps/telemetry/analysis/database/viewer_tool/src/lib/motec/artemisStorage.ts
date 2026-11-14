import { gatewayRows, gatewayJSON, type GatewayCatalog } from "../query/client";
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
  const catalog = await gatewayJSON<GatewayCatalog>("/v1/signals");
  if (groups.some(group => !catalog.signals.some(s => s.group === group))) throw new Error("Unknown Artemis group");
  const selected = catalog.signals.filter(s => groups.includes(s.group) && (!fields || fields.includes(s.signal.split(".")[1])));
  const width = Math.max(1, Math.ceil(requestedMs), ...selected.filter(s => (s.rate_hz || 0) > 0).map(s => Math.ceil(1000 / s.rate_hz!)));
  const selections = selected.map(s => ({ signal:s.signal, numeric:s.aggregates.includes(aggregate === "avg" ? "mean" : aggregate) }));
  const merged = new Map<string, BucketRow>();
  for (const numeric of [true, false]) {
    const signals = selections.filter(s => s.numeric === numeric).map(s => s.signal);
    for (let i = 0; i < signals.length; i += 10) {
      const batch = signals.slice(i, i + 10);
      // Bound observations as well as buckets to avoid reaggregating large pages.
      const points = Math.floor(10000 / batch.length);
      for (let from = startMs; from < endMs;) {
        const to = Math.min(endMs, Math.floor((from + width * points) / width) * width);
        const rows = await gatewayRows({ signals: batch, t0: new Date(from).toISOString(), t1: new Date(to).toISOString(), bucket_ms: width, max_points: points, agg: !numeric ? "last" : aggregate === "avg" ? "mean" : aggregate });
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
