import fs from "node:fs";
import path from "node:path";
import { Prisma } from "../../../.prisma/artemis-client";
import prisma from "@/lib/prisma/artemis";

type Field = { name: string; sql_type: string; aggregate: "avg" | "last" };
type Pool = { table: string; group: string; frequency_hz: number; fields: Field[] };
type Contract = { storage_id: string; pools: Pool[] };
export type BucketAggregate = "avg" | "min" | "max" | "last";
export type BucketRow = Record<string, unknown> & { session_start_ms: bigint; time: bigint };
let cached: Contract | undefined;

export function storageContract(): Contract {
  if (!cached) {
    const filename = process.env.ARTEMIS_STORAGE_CONTRACT_PATH || path.resolve(
      process.cwd(), "../../../../../bazel-bin/lib/codegen/telemetry/storage_contract.json",
    );
    cached = JSON.parse(fs.readFileSync(filename, "utf8")) as Contract;
  }
  return cached;
}

export function bucketWidth(groups: string[], requestedMs = 100, fields?: string[]): number {
  const periods = storageContract().pools
    .filter(p => groups.includes(p.group) && p.frequency_hz > 0 && (!fields || p.fields.some(f => fields.includes(f.name))))
    .map(p => Math.ceil(1000 / p.frequency_hz));
  return Math.max(1, Math.ceil(requestedMs), ...periods);
}

export async function readBuckets(groups: string[], startMs: number, endMs: number, requestedMs = 100, aggregate: BucketAggregate = "avg", fields?: string[]) {
  if (!Number.isSafeInteger(startMs) || !Number.isSafeInteger(endMs) || endMs <= startMs) {
    throw new Error("Invalid telemetry time range");
  }
  if (!Number.isFinite(requestedMs) || requestedMs <= 0) throw new Error("Invalid bucket width");
  if (!["avg", "min", "max", "last"].includes(aggregate)) throw new Error("Invalid bucket aggregate");
  const allowed = new Set(storageContract().pools.map(p => p.group));
  const width = bucketWidth(groups, requestedMs, fields);
  const rows = await Promise.all(groups.map(async group => {
    if (!allowed.has(group)) throw new Error(`Unknown telemetry group: ${group}`);
    // Prisma cannot express a table-valued Timescale function. The identifier
    // comes only from the generated contract; all values remain parameters.
    const identifier = Prisma.raw(`public.${group}_buckets`);
    return prisma.$queryRaw<BucketRow[]>(Prisma.sql`
      SELECT * FROM ${identifier}(${BigInt(width)}, ${BigInt(startMs)}, ${BigInt(endMs)}, ${aggregate})
    `);
  }));
  const merged = new Map<string, BucketRow>();
  rows.forEach((values, index) => {
    for (const value of values) {
      const key = `${value.session_start_ms}:${value.time}`;
      const row = merged.get(key) || { session_start_ms: value.session_start_ms, time: value.time };
      row[groups[index]] = value;
      merged.set(key, row);
    }
  });
  return [...merged.values()].sort((a, b) => Number(a.time - b.time));
}
