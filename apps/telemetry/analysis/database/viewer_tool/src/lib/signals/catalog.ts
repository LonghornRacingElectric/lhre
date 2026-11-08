import { readFile, stat } from "node:fs/promises";
import path from "node:path";

export type Signal = {
  signal: string;
  group: string;
  value_type: string;
  repeated: boolean;
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
const CAR = /^[a-z][a-z0-9_-]*$/;

function isSignal(value: unknown): value is Signal {
  if (!value || typeof value !== "object") return false;
  const row = value as Record<string, unknown>;
  if ("schema_version" in row) return false;
  const nullableString = (v: unknown) => v === null || typeof v === "string";
  const rate = (v: unknown, minimum: number) => v === null ||
    (typeof v === "number" && Number.isFinite(v) && v >= minimum);
  return typeof row.signal === "string" && /^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$/.test(row.signal) &&
    typeof row.group === "string" && typeof row.value_type === "string" &&
    typeof row.repeated === "boolean" &&
    nullableString(row.module) && nullableString(row.unit) &&
    rate(row.rate_hz, 0) &&
    ["telemetry", "event", "health"].includes(String(row.channel_type));
}

export async function readSignalCatalog(car: string, version: string | null) {
  if (!CAR.test(car)) throw new CatalogError(400, "invalid_car", "car must be a lowercase slug.");
  if (version !== null && !HASH.test(version)) {
    throw new CatalogError(400, "invalid_schema_version", "schema_version must be a lowercase SHA-256 hash.");
  }
  const root = process.env.SIGNAL_CATALOG_DIR || path.resolve(process.cwd(), "../../../catalogs");
  const directory = path.join(root, car);
  const missing = (error: unknown) => (error as NodeJS.ErrnoException).code === "ENOENT";
  try {
    if (!(await stat(directory)).isDirectory()) {
      throw new CatalogError(404, "unknown_car", "No signal catalog for this car.");
    }
  } catch (error) {
    if (missing(error)) throw new CatalogError(404, "unknown_car", "No signal catalog for this car.");
    throw error;
  }
  let selected = version;
  if (selected === null) {
    try {
      const pointer: unknown = JSON.parse(await readFile(path.join(directory, "current.json"), "utf8"));
      if (!pointer || typeof pointer !== "object" || !("schema_version" in pointer) ||
          typeof pointer.schema_version !== "string" || !HASH.test(pointer.schema_version)) {
        throw new Error("Invalid current catalog pointer.");
      }
      selected = pointer.schema_version;
    } catch (error) {
      if (missing(error)) throw new CatalogError(503, "catalog_unavailable", "No current signal catalog is published for this car.");
      throw error;
    }
  }
  try {
    const content: unknown = JSON.parse(await readFile(path.join(directory, `${selected}.json`), "utf8"));
    if (!content || typeof content !== "object") throw new Error("Invalid signal catalog.");
    const catalog = content as Record<string, unknown>;
    if (catalog.car !== car || catalog.schema_version !== selected ||
        !Array.isArray(catalog.signals) ||
        !catalog.signals.every(isSignal) ||
        new Set(catalog.signals.map(row => row.signal)).size !== catalog.signals.length) {
      throw new Error("Invalid signal catalog.");
    }
    return { car, schema_version: selected, signals: catalog.signals as Signal[] };
  } catch (error) {
    if (missing(error)) {
      if (version === null) throw new CatalogError(503, "catalog_unavailable", "The current catalog version is missing.");
      throw new CatalogError(404, "unknown_schema_version", "This schema version is not available for this car.");
    }
    throw error;
  }
}
