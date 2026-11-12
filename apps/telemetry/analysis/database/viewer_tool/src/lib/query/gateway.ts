import { timingSafeEqual } from "node:crypto";
import { getToken } from "next-auth/jwt";
import { NextRequest, NextResponse } from "next/server";

// Browser sessions and script tokens share the same gateway; the service token
// is attached here so it is never exposed to browser code.
export async function gatewayRequest(req: NextRequest, path: string) {
  const token = process.env.QUERY_API_TOKEN;
  if (!token) return NextResponse.json({ error: { code: "gateway_unavailable", message: "QUERY_API_TOKEN is not configured." } }, { status: 503 });
  const supplied = req.headers.get("authorization") || "";
  const expected = `Bearer ${token}`;
  const suppliedBytes = Buffer.from(supplied), expectedBytes = Buffer.from(expected);
  const authorizedToken = suppliedBytes.length === expectedBytes.length && timingSafeEqual(suppliedBytes, expectedBytes);
  if (!authorizedToken && !(await getToken({ req }))) {
    return NextResponse.json({ error: { code: "unauthorized", message: "Sign in or supply a query API token." } }, { status: 401 });
  }
  try {
    const upstream = await fetch(`${process.env.QUERY_GATEWAY_URL || "http://127.0.0.1:8090"}${path}`, {
      method: req.method,
      headers: { Authorization: expected, "Content-Type": "application/json", Accept: req.headers.get("accept") || "application/json" },
      body: req.method === "POST" ? await req.text() : undefined,
      signal: req.signal,
      cache: "no-store",
    });
    const headers = new Headers();
    for (const name of ["content-type", "x-next-page-token", "x-schema-version", "cache-control", "x-accel-buffering"]) {
      const value = upstream.headers.get(name);
      if (value) headers.set(name, value);
    }
    return new NextResponse(upstream.body, { status: upstream.status, headers });
  } catch {
    return NextResponse.json({ error: { code: "gateway_unavailable", message: "Telemetry gateway is unavailable." } }, { status: 503 });
  }
}
