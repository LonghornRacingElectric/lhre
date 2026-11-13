// Port of config.py — settings driven by env, defaulting to safe "sample" mode.
import path from "path";

export type CarId = "orion" | "artemis";

export type Settings = {
  car?: CarId;
  telemetrySource: string;
  dbHost: string;
  dbPort: number;
  dbName: string;
  dbUser: string;
  dbPassword: string;
  dbSslmode: string;
  dbConnectTimeout: number;
  exportDir: string;
  trackDir: string;
  channelChartDir: string;
  displayTimezone: string;
  maxPreviewPoints: number;
  maxPreviewSeconds: number;
  maxAutoSplitSeconds: number;
  maxExportSeconds: number;
  usePostgres: boolean;
};

// Repo-local data dirs live under the viewer_tool working directory.
const ROOT = process.cwd();

function envNum(name: string, fallback: number): number {
  const v = Number(process.env[name]);
  return Number.isFinite(v) && v > 0 ? v : fallback;
}

function baseSettings(): Settings {
  const telemetrySource = (process.env.TELEMETRY_SOURCE || "sample").trim().toLowerCase();
  return {
    car: "orion",
    telemetrySource,
    dbHost: process.env.ORION_DB_HOST || "192.168.1.109",
    dbPort: envNum("ORION_DB_PORT", 5432),
    dbName: process.env.ORION_DB_NAME || "orion",
    dbUser: process.env.ORION_DB_USER || "analysis",
    dbPassword: process.env.ORION_DB_PASSWORD || "",
    dbSslmode: process.env.ORION_DB_SSLMODE || "disable",
    dbConnectTimeout: envNum("ORION_DB_CONNECT_TIMEOUT", 5),
    exportDir: process.env.EXPORT_DIR || path.join(ROOT, "exports"),
    trackDir: process.env.TRACK_DIR || path.join(ROOT, "tracks"),
    channelChartDir: process.env.CHANNEL_CHART_DIR || path.join(ROOT, "channel_charts"),
    displayTimezone: process.env.DISPLAY_TIMEZONE || "America/Chicago",
    maxPreviewPoints: envNum("MAX_PREVIEW_POINTS", 5000),
    maxPreviewSeconds: envNum("MAX_PREVIEW_SECONDS", 60 * 60 * 2),
    maxAutoSplitSeconds: envNum("MAX_AUTO_SPLIT_SECONDS", 60 * 30),
    maxExportSeconds: envNum("MAX_EXPORT_SECONDS", 60 * 30),
    usePostgres: telemetrySource === "postgres",
  };
}

export function getSettings(source?: string | null): Settings {
  const car = (source || "artemis").trim().toLowerCase();
  if (car !== "orion" && car !== "artemis") throw new Error(`Unknown telemetry source: ${source}`);
  const base = baseSettings();
  const prefix = car.toUpperCase();
  const settings: Settings = { ...base, car, usePostgres: base.usePostgres || car === "artemis" && Boolean(process.env.QUERY_API_TOKEN), dbHost: process.env[`${prefix}_DB_HOST`] || base.dbHost,
    dbPort: envNum(`${prefix}_DB_PORT`, base.dbPort), dbName: process.env[`${prefix}_DB_NAME`] || car,
    dbUser: process.env[`${prefix}_DB_USER`] || base.dbUser,
    dbPassword: process.env[`${prefix}_DB_PASSWORD`] || base.dbPassword,
    dbSslmode: process.env[`${prefix}_DB_SSLMODE`] || base.dbSslmode };
  const connection = process.env[`${prefix}_DATABASE_URL`];
  if (connection) {
    const url = new URL(connection);
    return { ...settings, dbHost: url.hostname, dbPort: Number(url.port || 5432),
      dbName: decodeURIComponent(url.pathname.slice(1)), dbUser: decodeURIComponent(url.username),
      dbPassword: decodeURIComponent(url.password), dbSslmode: url.searchParams.get("sslmode") || settings.dbSslmode };
  }
  return settings;
}
