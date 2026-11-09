import { afterEach, describe, expect, it } from "vitest";
import prisma from "../../src/lib/prisma/artemis";
import { bucketWidth, readBuckets } from "../../src/lib/motec/artemisStorage";

// Only opt in with the URL of a disposable Timescale instance.
describe.skipIf(!process.env.ARTEMIS_STORAGE_TEST)("Artemis time-based database reads", () => {
  const start = Math.floor(Date.now() / 1000) * 1000;
  const key = BigInt(start);

  afterEach(async () => {
    await prisma.controls_333hz.deleteMany({ where: { session_start_ms: key } });
    await prisma.dynamics_333hz.deleteMany({ where: { session_start_ms: key } });
    await prisma.pack_1hz.deleteMany({ where: { session_start_ms: key } });
    await prisma.recording_session.deleteMany({ where: { session_start_ms: key } });
  });

  it("reads mixed rates through Prisma without duplicating joined rows", async () => {
    await prisma.$transaction([
      prisma.recording_session.create({ data: { session_start_ms: key, start_time: key, end_time: key + 30n } }),
      prisma.controls_333hz.createMany({ data: [
        { session_start_ms: key, time: key + 10n, apps1_travel: 10 },
        { session_start_ms: key, time: key + 20n, apps1_travel: 20 },
      ] }),
      prisma.dynamics_333hz.create({ data: { session_start_ms: key, time: key + 15n, steer_col_angle: 2 } }),
      prisma.pack_1hz.create({ data: { session_start_ms: key, time: key + 30n, cells_v: [3.7, 3.8] } }),
    ]);
    const rows = await readBuckets(["controls", "dynamics", "pack"], start, start + 1000);
    expect(rows).toHaveLength(1);
    expect(rows[0].controls).toMatchObject({ apps1_travel: 15 });
    expect(rows[0].pack).toMatchObject({ cells_v: [3.7, 3.8] });
    expect(await prisma.controls_333hz.count({ where: { session_start_ms: key } })).toBe(2);
    const peak = await readBuckets(["controls"], start, start + 100, 100, "max");
    expect(peak[0].controls).toMatchObject({ apps1_travel: 20 });
  });

  it("uses the slowest pool and rejects unregistered identifiers", async () => {
    expect(bucketWidth(["controls", "pack"], 100)).toBe(1000);
    expect(bucketWidth(["pack"], 10, ["dc_bus_v"])).toBe(10);
    await expect(readBuckets(["unregistered"], start, start + 1000)).rejects.toThrow("Unknown telemetry group");
  });
});
