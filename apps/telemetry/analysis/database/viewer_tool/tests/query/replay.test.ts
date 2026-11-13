import { expect, it, vi } from "vitest";
import { findReplaySampleAtOrAfter } from "../../src/lib/prisma/carPrisma";
import { gatewayJSON, gatewayRows } from "../../src/lib/query/client";

vi.mock("@/lib/prisma/orion", () => ({ default: {} }));
vi.mock("@/lib/prisma/telemetry", () => ({ default: {} }));
vi.mock("../../.prisma/orion-client", () => ({ Prisma: {} }));
vi.mock("../../src/lib/query/client", () => ({ gatewayJSON: vi.fn(), gatewayRows: vi.fn(), gatewaySessions: vi.fn() }));
vi.mock("../../src/lib/motec/artemisStorage", () => ({ storageContract: () => ({ pools: [
  { table: "controls_333hz", group: "controls" }, { table: "dynamics_100hz", group: "dynamics" },
] }) }));

it("replays raw peaks and sparse fields without combining nanosecond samples or sessions", async () => {
  const stamp = 1_700_000_000_000;
  const first = { ts: new Date(stamp).toISOString(), sample_time_ns: "1700000000000000001", session_start_ms: "42", signal: "controls.apps1_travel", value: 80 };
  vi.mocked(gatewayJSON).mockResolvedValue({ data: [first] });
  vi.mocked(gatewayRows).mockResolvedValue([
    first,
    { ...first, signal: "controls.enable", value: false },
    { ...first, signal: "dynamics.gps", value: [0, 1] },
    { ...first, sample_time_ns: "1700000000000000002", value: 10 },
    { ...first, session_start_ms: "43", value: 20 },
  ]);
  const sample = await findReplaySampleAtOrAfter("artemis", stamp, stamp + 1000, stamp);
  expect(sample).toEqual({ time: BigInt(stamp), session_start_ms: 42n, controls: { apps1_travel: 80, enable: false }, dynamics: { gps: [0, 1] } });
  expect(gatewayRows).toHaveBeenCalledWith({ signals: ["*"], pools: ["controls_333hz", "dynamics_100hz"], agg: "raw", t0: new Date(stamp).toISOString(), t1: new Date(stamp + 1).toISOString() });
});
