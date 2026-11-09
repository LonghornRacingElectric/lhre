import { z } from "zod";
import { LIMITS, QueryError, type QueryRequest } from "./types";

const timestamp = z.union([
  z.iso.datetime({ offset: true, precision: 0 }),
  z.iso.datetime({ offset: true, precision: 1 }),
  z.iso.datetime({ offset: true, precision: 2 }),
  z.iso.datetime({ offset: true, precision: 3 }),
], { error: "Expected an ISO8601 timestamp with a timezone and at most millisecond precision." }).transform(Date.parse);

const querySchema = z.strictObject({
  car: z.string().regex(/^[a-z][a-z0-9_-]*$/, "car must be a lowercase slug."),
  signals: z.array(z.string().regex(/^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$/))
    .min(1).max(LIMITS.maxSignals)
    .refine(signals => new Set(signals).size === signals.length, "signals must not contain duplicates.")
    .transform(signals => signals.sort()),
  t0: timestamp,
  t1: timestamp,
  rate: z.number().positive().max(1000).nullish().transform(value => value ?? null),
  agg: z.enum(["raw", "mean", "min", "max"]).nullish().transform(value => value ?? "raw"),
  page_size: z.number().int().positive().nullish().transform(value => Math.min(value ?? LIMITS.defaultPage, LIMITS.maxPage)),
  page_token: z.string().min(1).max(LIMITS.tokenBytes).nullish().transform(value => value ?? null),
}).refine(query => query.t0 < query.t1, "t0 must be earlier than t1.")
  .transform(({ page_size, page_token, ...query }) => ({ ...query, pageSize: page_size, pageToken: page_token }));

// Validate and normalize the request, rejecting features this raw-only API lacks.
export function parseQuery(value: unknown): QueryRequest {
  const result = querySchema.safeParse(value);
  if (!result.success) throw new QueryError(400, "invalid_request", result.error.issues[0].message);
  if (result.data.agg !== "raw" || result.data.rate !== null) {
    throw new QueryError(400, "unsupported_downsampling", "Only raw queries are supported; omit rate and use agg: raw.");
  }
  return result.data;
}
