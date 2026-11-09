import { CatalogError, readSignalCatalog } from "../../../../../lib/signals/catalog";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request, context: { params: Promise<{ car: string }> }) {
  try {
    const { car } = await context.params;
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
