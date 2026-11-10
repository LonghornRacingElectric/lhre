import { QueryError, type JsonValue, type Position } from "./types";

export function comparePositions(a: Position, b: Position): number {
  const left = BigInt(a.session);
  const right = BigInt(b.session);
  const fineLeft = BigInt(a.sampleTimeNs);
  const fineRight = BigInt(b.sampleTimeNs);
  return a.ts - b.ts || (left < right ? -1 : left > right ? 1 :
    fineLeft < fineRight ? -1 : fineLeft > fineRight ? 1 : a.signal < b.signal ? -1 : a.signal > b.signal ? 1 : 0);
}

export function jsonValue(value: unknown): JsonValue {
  if (typeof value === "bigint") return value.toString();
  if (value instanceof Uint8Array) return Buffer.from(value).toString("base64");
  if (Array.isArray(value)) return value.map(jsonValue);
  if (typeof value === "number" && !Number.isFinite(value)) return String(value);
  if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") return value;
  throw new QueryError(503, "invalid_history", "A stored value has an unsupported type.");
}

export function databaseError(error: unknown): never {
  if (error instanceof QueryError) throw error;
  const code = (error as { code?: string })?.code;
  if (code === "57014") throw new QueryError(503, "query_timeout", "The database query exceeded its time limit.");
  if (code === "42P01" || code === "42703") throw new QueryError(503, "schema_unavailable", "The database does not contain the requested signal schema.");
  throw new QueryError(503, "database_unavailable", "Unable to read telemetry from PostgreSQL.");
}
