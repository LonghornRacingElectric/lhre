import { NextRequest } from "next/server";
import { gatewayRequest } from "@/lib/query/gateway";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const GET = (req: NextRequest) => gatewayRequest(req, `/v1/stream?${req.nextUrl.searchParams}`);
