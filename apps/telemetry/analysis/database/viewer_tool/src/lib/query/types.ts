import type { Signal } from "../signals/catalog";

export class QueryError extends Error {
  // Carry the HTTP status and public error code alongside the readable message.
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

export const LIMITS = {
  defaultPage: 1000, maxPage: 10000, maxSignals: 50,
  requestBytes: 256 * 1024, pageBytes: 2 * 1024 * 1024,
  sampleBytes: 64 * 1024, fetchRows: 1000, tokenBytes: 8192,
  sampleBatch: 32, maxScannedRows: 10000, queryTimeoutMs: 30000,
} as const;

export type Aggregation = "raw" | "mean" | "min" | "max";
export type QueryRequest = {
  car: string; signals: string[]; t0: number; t1: number;
  rate: number | null; agg: Aggregation; pageSize: number; pageToken: string | null;
};
export type QueryPlan = Omit<QueryRequest, "signals" | "pageToken" | "pageSize"> & { signals: Signal[] };
export type Position = { ts: number; session: string; signal: string };
export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
export type Observation = { position: Position; value: JsonValue; oversized?: boolean };

// Sources supply ordered observations; HTTP, catalogs, and tokens belong to the service.
export interface TelemetrySource {
  read(plan: QueryPlan, after: Position | null, limit: number): Promise<Observation[]>;
}
export type QueryRow = { ts: string; signal: string; value: JsonValue };
export type QueryPage = { schema_version: string; data: QueryRow[]; next_page_token: string | null };
