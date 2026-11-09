import { getSettings } from "../motec/config";
import { ReadOnlyDatabase } from "../motec/db";
import { LIMITS, QueryError, type JsonValue, type Observation, type Position, type QueryPlan, type TelemetrySource } from "./types";

type PacketRow = { packet_id: string; time: string; [signal: string]: unknown };
type Database = Pick<ReadOnlyDatabase, "query">;
type DatabaseForCar = (car: string) => Database;

// Select catalog-approved fields and join their group tables to packet timestamps.
function selection(plan: QueryPlan): string {
  const groups = new Set<string>();
  const columns = plan.signals.map(signal => {
    const [group, column, extra] = signal.signal.split(".");
    if (extra || group !== signal.group || !/^[a-z_][a-z0-9_]*$/.test(group) ||
        !column || !/^[a-z_][a-z0-9_]*$/.test(column)) {
      throw new QueryError(503, "schema_unavailable", "The catalog contains an unsupported signal mapping.");
    }
    groups.add(group);
    return '"' + group + '"."' + column + '" AS "' + signal.signal + '"';
  });
  const joins = [...groups].map(group =>
    'LEFT JOIN "' + group + '" ON "' + group + '"."packet_id" = packet."packet_id"');
  return 'SELECT packet."packet_id", packet."time", ' + columns.join(", ") +
    ' FROM "packet" ' + joins.join(" ");
}

// Convert database values to JSON without losing large integers or decimal precision.
function json(value: unknown): JsonValue {
  if (typeof value === "bigint") return value.toString();
  if (value instanceof Uint8Array) return Buffer.from(value).toString("base64");
  if (Array.isArray(value)) return value.map(json);
  if (typeof value === "number" && !Number.isFinite(value)) return String(value);
  if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") return value;
  throw new QueryError(503, "invalid_history", "A stored value has an unsupported type.");
}

// Decide whether an observation comes after the last delivered cursor position.
function follows(position: Position, cursor: Position | null) {
  return !cursor || position.ts > cursor.ts || (position.ts === cursor.ts &&
    (BigInt(position.packet) > BigInt(cursor.packet) ||
      (BigInt(position.packet) === BigInt(cursor.packet) && position.signal > cursor.signal)));
}

// Turn database failures into API errors without exposing connection details.
function databaseError(error: unknown): never {
  if (error instanceof QueryError) throw error;
  const code = (error as { code?: string })?.code;
  if (code === "57014") throw new QueryError(503, "query_timeout", "The database query exceeded its time limit.");
  if (code === "42P01" || code === "42703") throw new QueryError(503, "schema_unavailable", "The database does not contain the requested signal schema.");
  throw new QueryError(503, "database_unavailable", "Unable to read telemetry from PostgreSQL.");
}

export class PostgresSource implements TelemetrySource {
  // Allow tests to supply a database while production uses the existing viewer helpers.
  constructor(private connect: DatabaseForCar = car => new ReadOnlyDatabase(getSettings(car))) {}

  // Read bounded packet batches and expand their fields into one page of observations.
  async read(plan: QueryPlan, after: Position | null, limit: number): Promise<Observation[]> {
    if (plan.agg !== "raw" || plan.rate !== null) throw new QueryError(400, "unsupported_downsampling", "Only raw queries are supported.");
    // Artemis has time-keyed frequency pools. Its adapter is a separate change.
    if (plan.car === "artemis") {
      throw new QueryError(503, "unsupported_storage", "Artemis history uses frequency pools; use its existing time-bucket interfaces.");
    }
    const select = selection(plan);
    const signals = [...plan.signals].sort((a, b) => a.signal < b.signal ? -1 : a.signal > b.signal ? 1 : 0);
    const observations: Observation[] = [];
    const deadline = Date.now() + LIMITS.queryTimeoutMs;
    let scanned = 0;
    let boundary = after ? { time: String(after.ts), packet_id: after.packet } : null;
    let inclusive = true;
    const target = Math.min(limit, LIMITS.fetchRows + 1);
    try {
      const db = this.connect(plan.car);
      while (observations.length < target) {
        const remaining = deadline - Date.now();
        if (remaining <= 0) throw new QueryError(503, "query_timeout", "The database query exceeded its time limit.");
        if (scanned >= LIMITS.maxScannedPackets) throw new QueryError(413, "query_scan_limit", "Too many packets scanned; narrow the time range or select a denser signal.");
        const take = Math.min(LIMITS.packetBatch, LIMITS.maxScannedPackets - scanned);
        const params: unknown[] = [plan.t0, plan.t1, take];
        let predicate = "";
        if (boundary) {
          params.push(boundary.time, boundary.packet_id);
          predicate = ' AND (packet."time" > $4 OR (packet."time" = $4 AND packet."packet_id" ' +
            (inclusive ? ">=" : ">") + ' $5))';
        }
        const packets = await db.query<PacketRow>(select +
          ' WHERE packet."time" >= $1 AND packet."time" < $2' + predicate +
          ' ORDER BY packet."time", packet."packet_id" LIMIT $3', params);
        scanned += packets.length;
        for (const packet of packets) {
          const ts = Number(packet.time);
          if (!Number.isSafeInteger(ts) || ts < plan.t0 || ts >= plan.t1) {
            throw new QueryError(503, "invalid_history", "Stored telemetry has an invalid timestamp.");
          }
          for (const signal of signals) {
            const value = packet[signal.signal];
            if (value === null || value === undefined) continue;
            const position = { ts, packet: packet.packet_id.toString(), signal: signal.signal };
            if (!follows(position, after)) continue;
            const normalized = json(value);
            const oversized = Buffer.byteLength(JSON.stringify(normalized)) > LIMITS.sampleBytes;
            observations.push({ position, value: oversized ? null : normalized, oversized });
            if (observations.length === target) return observations;
          }
        }
        if (packets.length < take) break;
        const last: PacketRow = packets[packets.length - 1];
        boundary = { time: last.time, packet_id: last.packet_id };
        inclusive = false;
      }
      return observations;
    } catch (error) { return databaseError(error); }
  }
}
