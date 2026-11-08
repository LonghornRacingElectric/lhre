import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { GET } from "../../src/app/api/cars/[car]/signals/route";

const current = "a".repeat(64);
const old = "b".repeat(64);
const signal = (version: string, rate = 50) => ({
  signal: "dynamics.gps", group: "dynamics", value_type: "float", repeated: true,
  schema_version: version, module: "gateway", unit: "deg", rate_hz: rate,
  channel_type: "telemetry",
});
let directory: string;
const request = (car = "artemis", version?: string) => GET(
  new Request(`http://localhost/api/cars/${car}/signals${version === undefined ? "" : `?schema_version=${version}`}`),
  { params: Promise.resolve({ car }) },
);
async function save(car: string, version: string, rate = 50) {
  await mkdir(path.join(directory, car), { recursive: true });
  await writeFile(path.join(directory, car, `${version}.json`),
    JSON.stringify({ car, schema_version: version, signals: [signal(version, rate)] }));
}
const select = (car: string, version: string) => writeFile(
  path.join(directory, car, "current.json"), JSON.stringify({ schema_version: version }),
);

beforeEach(async () => {
  directory = await mkdtemp(path.join(os.tmpdir(), "signal-catalog-"));
  vi.stubEnv("SIGNAL_CATALOG_DIR", directory);
  await save("artemis", current);
  await select("artemis", current);
});
afterEach(async () => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  await rm(directory, { recursive: true, force: true });
});

describe("per-car DISCOVER", () => {
  it("serves current and exact versions, including an aperiodic zero rate", async () => {
    await save("artemis", current, 0);
    for (const version of [undefined, current]) {
      const response = await request("artemis", version);
      expect(response.status).toBe(200);
      expect(response.headers.get("X-Schema-Version")).toBe(current);
      expect(await response.json()).toEqual([signal(current, 0)]);
    }
  });
  it("keeps cars and their versions isolated", async () => {
    await save("orion", old, 10);
    await select("orion", old);
    expect(await (await request("orion")).json()).toEqual([signal(old, 10)]);
    expect(await (await request()).json()).toEqual([signal(current)]);
    expect((await request("artemis", old)).status).toBe(404);
    expect((await request("orion", current)).status).toBe(404);
  });
  it("uses the current pointer, supports rollback, and ignores hash ordering", async () => {
    await save("artemis", old, 25);
    expect(await (await request("artemis", old)).json()).toEqual([signal(old, 25)]);
    expect(await (await request()).json()).toEqual([signal(current)]);
    await select("artemis", old);
    expect(await (await request()).json()).toEqual([signal(old, 25)]);
    await select("artemis", current);
    expect(await (await request()).json()).toEqual([signal(current)]);
  });
  it("rejects unknown cars and unsafe car/version paths", async () => {
    expect((await request("angelique")).status).toBe(404);
    for (const car of ["..", "Artemis", "../orion"]) expect((await request(car)).status).toBe(400);
    for (const version of ["", "latest", "../../signals", "A".repeat(64)]) {
      const response = await request("artemis", version);
      expect(response.status).toBe(400);
      expect(await response.json()).toMatchObject({ error: { code: "invalid_schema_version" } });
    }
  });
  it("allows exact version reads without a current pointer", async () => {
    await rm(path.join(directory, "artemis/current.json"));
    expect((await request()).status).toBe(503);
    expect((await request("artemis", current)).status).toBe(200);
    await select("artemis", old);
    expect((await request()).status).toBe(503);
  });
  it("rejects corrupt and mismatched artifacts without leaking paths", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const base = { car: "artemis", schema_version: current, signals: [signal(current)] };
    for (const value of ["broken", "{}", JSON.stringify({ ...base, car: "orion" }),
      JSON.stringify({ ...base, schema_version: old }),
      JSON.stringify({ ...base, signals: [signal(old)] }),
      JSON.stringify({ ...base, signals: [signal(current), signal(current)] })]) {
      await writeFile(path.join(directory, "artemis", `${current}.json`), value);
      const response = await request();
      expect(response.status).toBe(500);
      expect(JSON.stringify(await response.json())).not.toContain(directory);
    }
    await save("artemis", current);
    await writeFile(path.join(directory, "artemis/current.json"), '{"schema_version":"../orion"}');
    expect((await request()).status).toBe(500);
    expect((await request("artemis", current)).status).toBe(200);
  });
});
