import { readFile } from "node:fs/promises";
import path from "node:path";

export type Signal = {
  signal: string;
  group: string;
  value_type: string;
  repeated: boolean;
  schema_version: string;
  module: string | null;
  unit: string | null;
  rate_hz: number | null;
  channel_type: "telemetry" | "event" | "health";
};

export class CatalogError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

const HASH = /^[a-f0-9]{64}$/;

function isSignal(value: unknown, version: string): value is Signal {
  if (!value || typeof value !== "object") return false;
  const row = value as Record<string, unknown>;
  const nullableString = (v: unknown) => v === null || typeof v === "string";
  const rate = (v: unknown, minimum: number) => v === null ||
    (typeof v === "number" && Number.isFinite(v) && v >= minimum);
  return typeof row.signal === "string" && /^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$/.test(row.signal) &&
    typeof row.group === "string" && typeof row.value_type === "string" &&
    typeof row.repeated === "boolean" && row.schema_version === version &&
    nullableString(row.module) && nullableString(row.unit) &&
    rate(row.rate_hz, 0) &&
    ["telemetry", "event", "health"].includes(String(row.channel_type));
}

export async function readSignalCatalog(car: string, version: string | null) {
  // Only Orion has a generated catalog; never substitute it for another car.
  if (car !== "orion") throw new CatalogError(404, "unknown_car", "No signal catalog for this car.");
  if (version !== null && !HASH.test(version)) {
    throw new CatalogError(400, "invalid_schema_version", "schema_version must be a lowercase SHA-256 hash.");
  }
  const directory = process.env.SIGNAL_CATALOG_DIR || path.resolve(
    process.cwd(), "../../../../../bazel-bin/lib/codegen/telemetry",
  );
  const load = async (name: string) => {
    const content: unknown = JSON.parse(await readFile(path.join(directory, name), "utf8"));
    if (!content || typeof content !== "object") throw new Error("Invalid signal catalog.");
    const catalog = content as Record<string, unknown>;
    if (typeof catalog.schema_version !== "string" || !HASH.test(catalog.schema_version) ||
        !Array.isArray(catalog.signals) ||
        !catalog.signals.every(row => isSignal(row, catalog.schema_version as string)) ||
        new Set(catalog.signals.map(row => row.signal)).size !== catalog.signals.length) {
      throw new Error("Invalid signal catalog.");
    }
    return { schema_version: catalog.schema_version, signals: catalog.signals as Signal[] };
  };
  const missing = (error: unknown) => (error as NodeJS.ErrnoException).code === "ENOENT";
  try {
    const current = await load("signals.json");
    if (version === null || current.schema_version === version) return current;
  } catch (error) {
    if (!missing(error)) throw error;
    if (version === null) {
      throw new CatalogError(503, "catalog_unavailable", "The current signal catalog is not deployed.");
    }
  }
  try {
    const archived = await load(`${version}.json`);
    if (archived.schema_version !== version) throw new Error("Archived catalog version mismatch.");
    return archived;
  } catch (error) {
    if (missing(error)) throw new CatalogError(404, "unknown_schema_version", "This schema version is not available.");
    throw error;
  }
}
