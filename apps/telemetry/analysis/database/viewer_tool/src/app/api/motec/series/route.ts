import { NextRequest, NextResponse } from "next/server";
import type { BucketAggregate } from "@/lib/motec/artemisStorage";
import { num, services } from "@/lib/motec/service";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  try {
    const p = req.nextUrl.searchParams;
    const aggregate = p.get("aggregate") || "avg";
    if (!["avg", "min", "max", "last"].includes(aggregate)) {
      return NextResponse.json({ error: "aggregate must be avg, min, max, or last" }, { status: 400 });
    }
    const { telemetry } = services(p.get("source"));
    const res = await telemetry.series(
      p.get("channel") || "",
      num(p.get("startMs"), 0),
      num(p.get("endMs"), 0),
      num(p.get("maxPoints"), 5000),
      aggregate as BucketAggregate,
    );
    return NextResponse.json(res);
  } catch (err) {
    return NextResponse.json({ error: String(err instanceof Error ? err.message : err) }, { status: 500 });
  }
}
