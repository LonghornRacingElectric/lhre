import Negotiator from "negotiator";
import getRawBody from "raw-body";
import { Readable } from "node:stream";
import type { ReadableStream } from "node:stream/web";
import { stringify } from "csv-stringify/sync";
import { CatalogError } from "../signals/catalog";
import { LIMITS, QueryError, type QueryPage } from "./types";

// Read the incoming JSON while enforcing the request's byte limit.
async function body(request: Request) {
  if (!request.body) throw new QueryError(400, "invalid_json", "Expected a JSON request body.");
  const stream = Readable.fromWeb(request.body as ReadableStream);
  try {
    return JSON.parse(await getRawBody(stream, {
      length: request.headers.get("content-length"), limit: LIMITS.requestBytes, encoding: "utf8",
    }));
  } catch (error) {
    if ((error as { type?: string }).type === "entity.too.large") {
      throw new QueryError(413, "request_too_large", "Query body is too large.");
    }
    throw new QueryError(400, "invalid_json", "Malformed JSON request body.");
  } finally { stream.destroy(); }
}

// Wrap the query service with HTTP parsing, JSON/CSV formatting, and error responses.
export function createQueryHandler(query: (body: unknown) => Promise<QueryPage>) {
  return async (request: Request): Promise<Response> => {
    try {
      const selected = new Negotiator({ headers: { accept: request.headers.get("accept") ?? undefined } })
        .mediaType(["application/json", "text/csv"]);
      if (!selected) throw new QueryError(406, "unsupported_format", "Supported response formats are application/json and text/csv.");
      const page = await query(await body(request));
      const headers: Record<string, string> = { "Cache-Control": "no-store", "X-Schema-Version": page.schema_version,
        "Vary": "Accept" };
      if (selected === "application/json") return Response.json(page, { headers });
      headers["Content-Type"] = "text/csv; charset=utf-8";
      if (page.next_page_token) headers["X-Next-Page-Token"] = page.next_page_token;
      const records = page.data.map(row => ({ ...row,
        value: typeof row.value === "string" ? row.value : JSON.stringify(row.value) }));
      return new Response(stringify(records, { header: true, columns: ["ts", "signal", "value"], record_delimiter: "\r\n" }), { headers });
    } catch (error) {
      if (error instanceof QueryError || error instanceof CatalogError) {
        return Response.json({ error: { code: error.code, message: error.message } }, { status: error.status });
      }
      console.error("Telemetry query failed", { type: error instanceof Error ? error.name : "unknown" });
      return Response.json({ error: { code: "internal_error", message: "Unable to query telemetry." } }, { status: 500 });
    }
  };
}
