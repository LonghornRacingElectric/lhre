import { CursorCodec } from "./cursor";
import { parseQuery } from "./request";
import { LIMITS, QueryError, type QueryPage, type QueryRow, type TelemetrySource } from "./types";
import type { readSignalCatalog } from "../signals/catalog";

// Coordinate catalog resolution, source reads, page limits, and continuation tokens.
export function createQueryService(source: TelemetrySource, catalogs: typeof readSignalCatalog, cursors = new CursorCodec()) {
  return async (body: unknown): Promise<QueryPage> => {
    const request = parseQuery(body);
    const cursor = request.pageToken ? cursors.decode(request.pageToken, request) : null;
    const catalog = await catalogs(request.car, cursor?.schema ?? null);
    if (catalog.car !== request.car || (cursor && catalog.schema_version !== cursor.schema)) {
      throw new QueryError(503, "schema_unavailable", "The requested catalog is unavailable.");
    }
    const definitions = new Map(catalog.signals.map(signal => [signal.signal, signal]));
    const signals = request.signals.map(name => {
      const definition = definitions.get(name);
      if (!definition) throw new QueryError(404, "unknown_signal", `Unknown signal: ${name}`);
      return definition;
    });
    const target = Math.min(request.pageSize, LIMITS.fetchRows);
    const rows = await source.read({ car: request.car, signals, t0: request.t0, t1: request.t1,
      rate: request.rate, agg: request.agg }, cursor?.after ?? null, target + 1);
    const data: QueryRow[] = [];
    let bytes = Buffer.byteLength("[]");
    for (const observation of rows.slice(0, target)) {
      if (observation.oversized) throw new QueryError(413, "sample_too_large", "A stored observation exceeds the per-value size limit.");
      const row = { ts: new Date(observation.position.ts).toISOString(), signal: observation.position.signal, value: observation.value };
      const size = Buffer.byteLength(JSON.stringify(row));
      if (size > LIMITS.sampleBytes + 1024) throw new QueryError(413, "sample_too_large", "A stored observation exceeds the per-value size limit.");
      const separatorBytes = data.length ? Buffer.byteLength(",") : 0;
      if (bytes + separatorBytes + size > LIMITS.pageBytes) break;
      data.push(row);
      bytes += separatorBytes + size;
    }
    // Include the actual cursor and metadata, trimming rows until the complete JSON fits.
    while (true) {
      const page: QueryPage = { schema_version: catalog.schema_version, data,
        next_page_token: rows.length > data.length && data.length > 0
          ? cursors.encode(request, catalog.schema_version, rows[data.length - 1].position, cursor?.expires) : null };
      if (Buffer.byteLength(JSON.stringify(page)) <= LIMITS.pageBytes) return page;
      if (data.length <= 1) throw new QueryError(413, "sample_too_large", "An observation and its metadata exceed the page size limit.");
      data.pop();
    }
  };
}
