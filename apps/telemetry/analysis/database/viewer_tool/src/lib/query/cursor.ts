import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { z } from "zod";
import { LIMITS, QueryError, type Position, type QueryRequest } from "./types";

const hash = z.string().regex(/^[a-f0-9]{64}$/);
const rowKey = z.string().regex(/^-?\d{1,19}$/).pipe(z.string().refine(value =>
  BigInt(value) >= -(BigInt(2) ** BigInt(63)) && BigInt(value) < BigInt(2) ** BigInt(63)));
const cursorSchema = z.strictObject({
  v: z.literal(3), request: hash, schema: hash, expires: z.number().int(),
  after: z.strictObject({ ts: z.number().int(), session: rowKey, sampleTimeNs: rowKey, signal: z.string() }),
});
type Cursor = z.infer<typeof cursorSchema>;
const globalState = globalThis as typeof globalThis & { queryCursorSecret?: string };
// Use the configured signing secret, or one temporary key for this server process.
function defaultSecret() {
  return process.env.QUERY_CURSOR_SECRET || (globalState.queryCursorSecret ??= randomBytes(32).toString("hex"));
}
// Identify the query filters so a cursor cannot be reused with a different request.
function fingerprint(request: QueryRequest) {
  const { pageToken: _, ...filters } = request;
  return createHash("sha256").update(JSON.stringify(filters)).digest("hex");
}

export class CursorCodec {
  // Accept a signing key and clock so token behavior can also be tested predictably.
  constructor(private secret = defaultSecret(), private now = () => Date.now()) {}

  // Sign the continuation position together with its query, catalog, and expiration.
  encode(request: QueryRequest, schema: string, after: Position, expires = this.now() + 24 * 60 * 60 * 1000) {
    const value: Cursor = { v: 3, request: fingerprint(request), schema, after, expires };
    const payload = Buffer.from(JSON.stringify(value)).toString("base64url");
    const signature = createHmac("sha256", this.secret).update(payload).digest("base64url");
    return `${payload}.${signature}`;
  }

  // Verify the token and recover a valid continuation position for this query.
  decode(token: string, request: QueryRequest): Cursor {
    try {
      if (token.length > LIMITS.tokenBytes || !/^[\w-]+\.[\w-]+$/.test(token)) throw new Error();
      const [payload, signature] = token.split(".");
      const expected = createHmac("sha256", this.secret).update(payload).digest();
      const supplied = Buffer.from(signature, "base64url");
      if (expected.length !== supplied.length || !timingSafeEqual(expected, supplied)) throw new Error();
      const value = cursorSchema.parse(JSON.parse(Buffer.from(payload, "base64url").toString("utf8")));
      const position = value.after;
      if (value.request !== fingerprint(request) || value.expires <= this.now() ||
          BigInt(position.sampleTimeNs) / BigInt(1000000) !== BigInt(position.ts) || position.ts < request.t0 || position.ts >= request.t1 ||
          !request.signals.includes(position.signal)) throw new Error();
      return value;
    } catch {
      throw new QueryError(400, "invalid_page_token", "The page token is invalid, expired, or belongs to a different query.");
    }
  }
}
