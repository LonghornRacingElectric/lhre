import { CatalogError, readSignalCatalog } from "../../../../../lib/signals/catalog";
import { NextRequest } from "next/server";
import { gatewayRequest } from "../../../../../lib/query/gateway";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request, context: { params: Promise<{ car: string }> }) {
  try {
    const { car } = await context.params;
    const version = new URL(request.url).searchParams.get("schema_version");
    if (version !== null && !/^[a-f0-9]{64}$/.test(version)) throw new CatalogError(400, "invalid_schema_version", "schema_version must be a lowercase SHA-256 hash.");
    if (car === "artemis") {
      const upstream = await gatewayRequest(new NextRequest(request), "/v1/signals");
      if (!upstream.ok) return upstream;
      const catalog = await upstream.json();
      if (version && version !== catalog.schema_version) throw new CatalogError(404, "unknown_schema_version", "The gateway serves its deployed schema version.");
      return Response.json(catalog.signals, { headers: { "Cache-Control": "no-store", "X-Schema-Version": catalog.schema_version } });
    }
    const catalog = await readSignalCatalog(car, new URL(request.url).searchParams.get("schema_version"));
    return Response.json(catalog.signals, {
      headers: { "Cache-Control": "no-store", "X-Schema-Version": catalog.schema_version },
    });
  } catch (error) {
    if (error instanceof CatalogError) {
      return Response.json({ error: { code: error.code, message: error.message } }, { status: error.status });
    }
    console.error("Failed to read signal catalog", error);
    return Response.json({ error: { code: "internal_error", message: "Unable to read the signal catalog." } }, { status: 500 });
  }
}
