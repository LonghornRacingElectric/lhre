import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { GET } from "../../src/app/api/cars/[car]/signals/route";

const current = "a".repeat(64);
const old = "b".repeat(64);
const signal = (version: string) => ({
  signal: "dynamics.gps", group: "dynamics", value_type: "float", repeated: true,
  schema_version: version, module: "gateway", unit: "deg", rate_hz: 50,
  channel_type: "telemetry",
});
let directory: string;
const request = (car = "orion", version?: string) => GET(
  new Request(`http://localhost/api/cars/${car}/signals${version === undefined ? "" : `?schema_version=${version}`}`),
  { params: Promise.resolve({ car }) },
);
const save = (name: string, version: string) => writeFile(
  path.join(directory, name), JSON.stringify({ schema_version: version, signals: [signal(version)] }),
);

beforeEach(async () => {
  directory = await mkdtemp(path.join(os.tmpdir(), "signal-catalog-"));
  vi.stubEnv("SIGNAL_CATALOG_DIR", directory);
  await save("signals.json", current);
});
afterEach(async () => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  await rm(directory, { recursive: true, force: true });
});

describe("DISCOVER", () => {
  it("accepts the zero uplink sentinel for an aperiodic CAN signal", async () => {
    const row = { ...signal(current), module: "vcu", repeated: false, rate_hz: 0 };
    await writeFile(path.join(directory, "signals.json"), JSON.stringify({ schema_version: current, signals: [row] }));
    const response = await request();
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual([row]);
  });
  it("serves the current catalog and its exact version", async () => {
    for (const version of [undefined, current]) {
      const response = await request("orion", version);
      expect(response.status).toBe(200);
      expect(response.headers.get("X-Schema-Version")).toBe(current);
      expect(await response.json()).toEqual([signal(current)]);
    }
  });
  it("selects archived versions without substituting the current schema", async () => {
    await save(`${old}.json`, old);
    const response = await request("orion", old);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual([signal(old)]);
    expect((await request("orion", "c".repeat(64))).status).toBe(404);
  });
  it("rejects unknown cars and invalid hashes including path traversal", async () => {
    expect((await request("angelique")).status).toBe(404);
    for (const version of ["", "latest", "../../signals", "A".repeat(64)]) {
      const response = await request("orion", version);
      expect(response.status).toBe(400);
      expect(await response.json()).toMatchObject({ error: { code: "invalid_schema_version" } });
    }
  });
  it("reports an undeployed catalog and still permits an archived version", async () => {
    await rm(path.join(directory, "signals.json"));
    expect((await request()).status).toBe(503);
    await save(`${old}.json`, old);
    expect((await request("orion", old)).status).toBe(200);
  });
  it("rejects corrupt or mismatched artifacts without exposing file paths", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    for (const data of ["broken", "{}", JSON.stringify({ schema_version: current, signals: [signal(old)] }),
      JSON.stringify({ schema_version: current, signals: [signal(current), signal(current)] })]) {
      await writeFile(path.join(directory, "signals.json"), data);
      const response = await request();
      expect(response.status).toBe(500);
      expect(JSON.stringify(await response.json())).not.toContain(directory);
    }
    await save("signals.json", current);
    await save(`${old}.json`, current);
    expect((await request("orion", old)).status).toBe(500);
  });
});
