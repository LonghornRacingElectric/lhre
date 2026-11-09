import { getSettings } from "../motec/config";
import { ReadOnlyDatabase } from "../motec/db";
import { storageContract } from "../motec/artemisContract";
import { comparePositions, databaseError, jsonValue } from "./values";
import { LIMITS, QueryError, type Observation, type Position, type QueryPlan, type TelemetrySource } from "./types";

type Database = Pick<ReadOnlyDatabase, "query">;
type Contract = ReturnType<typeof storageContract>;
type Selection = { table: string; signals: QueryPlan["signals"] };
type PoolRow = { time: string; session_start_ms: string; [signal: string]: unknown };

function selections(plan: QueryPlan, contract: Contract): Selection[] {
  const selected = new Map<string, Selection>();
  for (const signal of plan.signals) {
    const [group, column, extra] = signal.signal.split(".");
    const pool = contract.pools.find(pool => pool.group === group && pool.fields.some(field => field.name === column));
    if (extra || group !== signal.group || !/^[a-z][a-z0-9_]*$/.test(group) ||
        !column || !/^[a-z][a-z0-9_]*$/.test(column) || !pool || !/^[a-z][a-z0-9_]*$/.test(pool.table)) {
      throw new QueryError(503, "schema_unavailable", "The catalog signal is not mapped to an Artemis frequency pool.");
    }
    const entry = selected.get(pool.table) ?? { table: pool.table, signals: [] };
    entry.signals.push(signal);
    selected.set(pool.table, entry);
  }
  for (const entry of selected.values()) entry.signals.sort((a, b) => a.signal < b.signal ? -1 : a.signal > b.signal ? 1 : 0);
  return [...selected.values()];
}

export class ArtemisSource implements TelemetrySource {
  constructor(private connect: () => Database = () => new ReadOnlyDatabase(getSettings("artemis")),
    private contract: () => Contract = storageContract) {}

  async read(plan: QueryPlan, after: Position | null, limit: number): Promise<Observation[]> {
    if (plan.agg !== "raw" || plan.rate !== null) throw new QueryError(400, "unsupported_downsampling", "Only raw queries are supported.");
    if (plan.car !== "artemis") throw new QueryError(400, "unsupported_car", "The query gateway reads Artemis history.");
    try {
      const pools = selections(plan, this.contract());
      const db = this.connect();
      const budget = { scanned: 0, deadline: Date.now() + LIMITS.queryTimeoutMs };
      const streams = pools.map(pool => this.observations(db, pool, plan, after, budget));
      const heads = await Promise.all(streams.map(stream => stream.next()));
      const result: Observation[] = [];
      const target = Math.min(limit, LIMITS.fetchRows + 1);
      // Merge ordered observations; raw pools are never joined to one another.
      while (result.length < target) {
        let first = -1;
        for (let i = 0; i < heads.length; i++) {
          if (!heads[i].done && (first < 0 || comparePositions(heads[i].value!.position, heads[first].value!.position) < 0)) first = i;
        }
        if (first < 0) break;
        result.push(heads[first].value!);
        if (result.length < target) heads[first] = await streams[first].next();
      }
      return result;
    } catch (error) { return databaseError(error); }
  }

  private async *observations(db: Database, pool: Selection, plan: QueryPlan,
    after: Position | null, budget: { scanned: number; deadline: number }): AsyncGenerator<Observation> {
    const columns = pool.signals.map(signal => `"${signal.signal.split(".")[1]}" AS "${signal.signal}"`).join(", ");
    let boundary = after ? { time: String(after.ts), session: after.session } : null;
    let inclusive = true;
    while (true) {
      if (Date.now() >= budget.deadline) throw new QueryError(503, "query_timeout", "The database query exceeded its time limit.");
      if (budget.scanned >= LIMITS.maxScannedRows) throw new QueryError(413, "query_scan_limit", "Too many samples scanned; narrow the time range or select a denser signal.");
      const take = Math.min(LIMITS.sampleBatch, LIMITS.maxScannedRows - budget.scanned);
      const params: unknown[] = [plan.t0, plan.t1, take];
      let predicate = "";
      if (boundary) {
        params.push(boundary.time, boundary.session);
        predicate = ' AND ("time" > $4 OR ("time" = $4 AND "session_start_ms" ' + (inclusive ? ">=" : ">") + ' $5))';
      }
      // Prisma converts SQL NULL lists into []; raw history must preserve the
      // difference from a recorded empty array. Identifiers are contract-only.
      const rows = await db.query<PoolRow>(`SELECT "time", "session_start_ms", ${columns} FROM public."${pool.table}"` +
        ' WHERE "time" >= $1 AND "time" < $2' + predicate + ' ORDER BY "time", "session_start_ms" LIMIT $3', params);
      for (const row of rows) {
        if (++budget.scanned > LIMITS.maxScannedRows) throw new QueryError(413, "query_scan_limit", "Too many samples scanned; narrow the time range or select a denser signal.");
        const ts = Number(row.time);
        if (!Number.isSafeInteger(ts) || ts < plan.t0 || ts >= plan.t1) throw new QueryError(503, "invalid_history", "Stored telemetry has an invalid timestamp.");
        for (const signal of pool.signals) {
          const value = row[signal.signal];
          if (value == null) continue;
          const position = { ts, session: row.session_start_ms.toString(), signal: signal.signal };
          if (after && comparePositions(position, after) <= 0) continue;
          const normalized = jsonValue(value);
          const oversized = Buffer.byteLength(JSON.stringify(normalized)) > LIMITS.sampleBytes;
          yield { position, value: oversized ? null : normalized, oversized };
        }
      }
      if (rows.length < take) return;
      const last = rows[rows.length - 1];
      boundary = { time: last.time, session: last.session_start_ms };
      inclusive = false;
    }
  }
}
