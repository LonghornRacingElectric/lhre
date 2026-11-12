// Server-side consumers share the gateway and never expose its token.
export async function gatewayJSON<T>(path: string, body?: unknown): Promise<T> {
  const token = process.env.QUERY_API_TOKEN;
  if (!token) throw new Error("QUERY_API_TOKEN is not configured");
  const result = await fetch(`${process.env.QUERY_GATEWAY_URL || "http://127.0.0.1:8090"}${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(35000), cache: "no-store",
  });
  if (!result.ok) throw new Error(`Gateway HTTP ${result.status}: ${(await result.text()).slice(0, 1000)}`);
  return result.json() as Promise<T>;
}

export type GatewayRow = { ts: string; sample_time_ns: string; session_start_ms: string; signal: string; value: unknown };
export async function gatewayRows(body: Record<string, unknown>): Promise<GatewayRow[]> {
  const rows: GatewayRow[] = [];
  let token: string | null = null;
  do {
    const page = await gatewayJSON<{ data: GatewayRow[]; next_page_token: string | null }>("/v1/query", { car: "artemis", page_size: 10000, ...body, page_token: token });
    rows.push(...page.data);
    if (rows.length > 1_000_000) throw new Error("Gateway result exceeds viewer limit; narrow the range");
    token = page.next_page_token;
  } while (token);
  return rows;
}
export async function gatewaySessions() {
  const result = await gatewayJSON<{ sessions: Array<{ start_time: number; end_time: number; session_start_ms: string }> }>("/v1/sessions");
  return result.sessions.map(s => ({ start_ms: String(s.start_time), end_ms: String(s.end_time) }));
}
