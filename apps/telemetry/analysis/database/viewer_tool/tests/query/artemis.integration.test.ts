import { mkdtemp, readFile, rm, writeFile, mkdir } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { PrismaClient } from "../../.prisma/artemis-client";
import { getSettings } from "../../src/lib/motec/config";
import { ReadOnlyDatabase } from "../../src/lib/motec/db";
import { ArtemisSource } from "../../src/lib/query/artemis";
import { CursorCodec } from "../../src/lib/query/cursor";
import { createQueryHandler } from "../../src/lib/query/http";
import { createQueryService } from "../../src/lib/query/service";
import { readSignalCatalog } from "../../src/lib/signals/catalog";
import type { QueryPlan } from "../../src/lib/query/types";

const url = process.env.ARTEMIS_QUERY_TEST_DATABASE_URL;
const start = Date.parse("2026-11-08T12:00:00Z");
const key = BigInt(start - 1000), second = key + 1n;
const names = ["controls.apps1_travel", "controls.enable", "dynamics.gps_precise", "pack.cells_v", "board_status.hvc_last_seen_s"];

describe.runIf(Boolean(url))("Artemis gateway against generated Timescale pools", () => {
  let db: PrismaClient;
  let source: ArtemisSource;
  let plan: QueryPlan;
  let root: string;

  beforeAll(async () => {
    if (!new URL(url!).pathname.slice(1).startsWith("telemetry_query_test")) throw new Error("Use an initialized disposable telemetry_query_test database.");
    db = new PrismaClient({ datasources: { db: { url: url! } } });
    vi.stubEnv("ARTEMIS_DATABASE_URL", url!);
    source = new ArtemisSource(() => new ReadOnlyDatabase(getSettings("artemis")));
    const catalog = JSON.parse(await readFile(path.resolve(process.cwd(), "../../../../../bazel-bin/lib/codegen/telemetry/signals.json"), "utf8"));
    expect(catalog.car).toBe("artemis");
    root = await mkdtemp(path.join(os.tmpdir(), "artemis-query-"));
    await mkdir(path.join(root, "artemis"));
    await writeFile(path.join(root, "artemis", catalog.schema_version + ".json"), JSON.stringify(catalog));
    await writeFile(path.join(root, "artemis", "current.json"), JSON.stringify({ schema_version: catalog.schema_version }));
    vi.stubEnv("SIGNAL_CATALOG_DIR", root);
    plan = { car: "artemis", signals: catalog.signals.filter((signal: { signal: string }) => names.includes(signal.signal)),
      t0: start, t1: start + 1000, rate: null, agg: "raw" };
    await db.$transaction([
      db.recording_session.createMany({ data: [key, second].map(session_start_ms => ({ session_start_ms, start_time: BigInt(start - 1), end_time: BigInt(start + 1000) })) }),
      db.controls_333hz.createMany({ data: [[-1, -1], [0, 0], [10, 10], [30, 20], [1000, 999]].map(([offset, value]) =>
        ({ session_start_ms: key, time: BigInt(start + offset), apps1_travel: value, enable: offset === 10 })) }),
      db.controls_333hz.create({ data: { session_start_ms: second, time: BigInt(start + 10), apps1_travel: 42, enable: true } }),
      db.dynamics_external.createMany({ data: [
        { session_start_ms: key, time: BigInt(start + 5), gps_precise: [30.123456789, -97.123456789] },
        { session_start_ms: key, time: BigInt(start + 15), gps_precise: [] },
        { session_start_ms: key, time: BigInt(start + 25) },
      ] }),
      db.pack_1hz.create({ data: { session_start_ms: key, time: BigInt(start + 500), cells_v: [3.7, 3.8] } }),
      db.board_status_external.create({ data: { session_start_ms: key, time: BigInt(start + 7), hvc_last_seen_s: 0 } }),
    ]);
  });

  afterAll(async () => {
    if (db) {
      const where = { session_start_ms: { in: [key, second] } };
      await db.$transaction([db.controls_333hz.deleteMany({ where }), db.dynamics_external.deleteMany({ where }),
        db.pack_1hz.deleteMany({ where }), db.board_status_external.deleteMany({ where }), db.recording_session.deleteMany({ where })]);
      await db.$disconnect();
    }
    if (root) await rm(root, { recursive: true, force: true });
    vi.unstubAllEnvs();
  });

  it("reads unequal rates without multiplying rows and preserves session ties", async () => {
    const rows = await source.read(plan, null, 100);
    expect(rows).toHaveLength(12);
    expect(rows.filter(row => row.position.signal === names[0]).map(row => row.value)).toEqual([0, 10, 42, 20]);
    expect(rows.filter(row => row.position.signal === names[2]).map(row => row.value)).toEqual([[30.123456789, -97.123456789], []]);
    expect(rows.some(row => row.value === false)).toBe(true);
    expect(rows.every(row => !("packet" in row.position))).toBe(true);
  });

  it("pages the HTTP gateway using the published Artemis catalog without losing same-time signals", async () => {
    const handler = createQueryHandler(createQueryService(source, readSignalCatalog, new CursorCodec("integration")));
    const all: unknown[] = [];
    let token: string | null = null;
    do {
      const response = await handler(new Request("http://localhost/api/query", { method: "POST", body: JSON.stringify({
        car: "artemis", signals: names, t0: new Date(start).toISOString(), t1: new Date(start + 1000).toISOString(), page_size: 1, page_token: token,
      }) }));
      expect(response.status).toBe(200);
      const page = await response.json();
      all.push(...page.data);
      token = page.next_page_token;
      if (token) {
        const position = JSON.parse(Buffer.from(token.split(".")[0], "base64url").toString()).after;
        expect(position).toHaveProperty("session");
        expect(position).not.toHaveProperty("packet");
      }
    } while (token);
    const expected = (await source.read(plan, null, 100)).map(row => ({ ts: new Date(row.position.ts).toISOString(), signal: row.position.signal, value: row.value }));
    expect(all).toEqual(expected);
  });

  it("continues correctly beyond the database batch size", async () => {
    try {
      await db.controls_333hz.createMany({ data: Array.from({ length: 70 }, (_, i) =>
        ({ session_start_ms: key, time: BigInt(start + 100 + i), apps1_travel: i })) });
      const selected = { ...plan, signals: plan.signals.filter(signal => signal.signal === names[0]), t0: start + 100, t1: start + 170 };
      const rows = await source.read(selected, null, 100);
      expect(rows.map(row => row.value)).toEqual(Array.from({ length: 70 }, (_, i) => i));
      expect((await source.read(selected, rows[31].position, 100)).map(row => row.value)).toEqual(Array.from({ length: 38 }, (_, i) => i + 32));
    } finally {
      await db.controls_333hz.deleteMany({ where: { session_start_ms: key, time: { gte: BigInt(start + 100), lt: BigInt(start + 170) } } });
    }
  });
});
