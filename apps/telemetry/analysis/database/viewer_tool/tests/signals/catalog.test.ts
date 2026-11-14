import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { GET } from "../../src/app/api/cars/[car]/signals/route";

const current = "a".repeat(64);
const old = "b".repeat(64);
const signal = (rate = 50) => ({
  signal: "dynamics.gps", group: "dynamics", value_type: "float", repeated: true,
  module: "gateway", unit: "deg", rate_hz: rate,
  channel_type: "telemetry",
});
let directory: string;
const request = (car = "orion", version?: string) => GET(
  new Request(`http://localhost/api/cars/${car}/signals${version === undefined ? "" : `?schema_version=${version}`}`),
  { params: Promise.resolve({ car }) },
);
async function save(car: string, version: string, rate = 50) {
  await mkdir(path.join(directory, car), { recursive: true });
  await writeFile(path.join(directory, car, `${version}.json`),
    JSON.stringify({ car, schema_version: version, signals: [signal(rate)] }));
}
const select = (car: string, version: string) => writeFile(
  path.join(directory, car, "current.json"), JSON.stringify({ schema_version: version }),
);

beforeEach(async () => {
  directory = await mkdtemp(path.join(os.tmpdir(), "signal-catalog-"));
  vi.stubEnv("SIGNAL_CATALOG_DIR", directory);
  await save("orion", current);
  await select("orion", current);
});
afterEach(async () => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  await rm(directory, { recursive: true, force: true });
});

describe("per-car DISCOVER", () => {
  it("serves current and exact versions, including an aperiodic zero rate", async () => {
    await save("orion", current, 0);
    for (const version of [undefined, current]) {
      const response = await request("orion", version);
      expect(response.status).toBe(200);
      expect(response.headers.get("X-Schema-Version")).toBe(current);
      expect(await response.json()).toEqual([signal(0)]);
    }
  });
  it("keeps cars and their versions isolated", async () => {
    await save("legacy", old, 10);
    await select("legacy", old);
    expect(await (await request("legacy")).json()).toEqual([signal(10)]);
    expect(await (await request()).json()).toEqual([signal()]);
    expect((await request("orion", old)).status).toBe(404);
    expect((await request("legacy", current)).status).toBe(404);
  });
  it("uses the current pointer, supports rollback, and ignores hash ordering", async () => {
    await save("orion", old, 25);
    expect(await (await request("orion", old)).json()).toEqual([signal(25)]);
    expect(await (await request()).json()).toEqual([signal()]);
    await select("orion", old);
    expect(await (await request()).json()).toEqual([signal(25)]);
    await select("orion", current);
    expect(await (await request()).json()).toEqual([signal()]);
  });
  it("rejects unknown cars and unsafe car/version paths", async () => {
    expect((await request("angelique")).status).toBe(404);
    for (const car of ["..", "Artemis", "../orion"]) expect((await request(car)).status).toBe(400);
    for (const version of ["", "latest", "../../signals", "A".repeat(64)]) {
      const response = await request("orion", version);
      expect(response.status).toBe(400);
      expect(await response.json()).toMatchObject({ error: { code: "invalid_schema_version" } });
    }
  });
  it("allows exact version reads without a current pointer", async () => {
    await rm(path.join(directory, "orion/current.json"));
    expect((await request()).status).toBe(503);
    expect((await request("orion", current)).status).toBe(200);
    await select("orion", old);
    expect((await request()).status).toBe(503);
  });
  it("rejects corrupt and mismatched artifacts without leaking paths", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const base = { car: "orion", schema_version: current, signals: [signal()] };
    for (const value of ["broken", "{}", JSON.stringify({ ...base, car: "legacy" }),
      JSON.stringify({ ...base, schema_version: old }),
      JSON.stringify({ ...base, signals: [{ ...signal(), schema_version: old }] }),
      JSON.stringify({ ...base, signals: [signal(), signal()] })]) {
      await writeFile(path.join(directory, "orion", `${current}.json`), value);
      const response = await request();
      expect(response.status).toBe(500);
      expect(JSON.stringify(await response.json())).not.toContain(directory);
    }
    await save("orion", current);
    await writeFile(path.join(directory, "orion/current.json"), '{"schema_version":"../orion"}');
    expect((await request()).status).toBe(500);
    expect((await request("orion", current)).status).toBe(200);
  });
});
