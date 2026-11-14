import { beforeEach, expect, it, vi } from "vitest";
import { buildNormalizedSensorRoutes } from "../../src/lib/kafka/kafkaConsumer";
import { normalizeLivePayload } from "../../src/lib/motec/live";
import { TelemetryService } from "../../src/lib/motec/telemetry";
import { getSettings } from "../../src/lib/motec/config";
import { gatewayJSON, gatewayRows } from "../../src/lib/query/client";

vi.mock("../../src/lib/query/client", () => ({ gatewayJSON: vi.fn(), gatewayRows: vi.fn() }));
beforeEach(() => vi.clearAllMocks());

it("maps native Artemis fields into live widgets and keeps speed units correct", () => {
  const payload = { time: 1794484800000, controls: { apps1_travel: 40, commanded_torque: 60 }, pack: { hv_pack_v: 420, hv_c: 50 }, dynamics: { gps_speed: 20, gps: [30.28, -97.73] } };
  const snake = buildNormalizedSensorRoutes(payload, "artemis");
  const camel = buildNormalizedSensorRoutes({ time: payload.time, controls: { apps1Travel: 40, commandedTorque: 60 }, pack: { hvPackV: 420, hvC: 50 }, dynamics: { gpsSpeed: 20, gps: [30.28, -97.73] } }, "artemis");
  expect(snake).toEqual(camel);
  const sample = normalizeLivePayload(payload, "artemis")!;
  expect(sample.speed).toBeCloseTo(10.2888889);
  expect(sample.power_kw).toBe(21);
  expect(sample.lat).toBe(30.28);
  expect(normalizeLivePayload({ dynamics: { fl_wheel_speed: 100 } }, "artemis")?.speed).toBeNull();
});

it("uses precise Artemis positions in live maps and falls back after the fix clears", () => {
  const precise = [30.28123456789, -97.73123456789, 250, .01, 2, 1794484800];
  const payload = { dynamics: { gps: [30.28, -97.73], gps_precise: precise } };
  expect(normalizeLivePayload(payload, "artemis")).toMatchObject({ lat: precise[0], lon: precise[1] });
  expect(buildNormalizedSensorRoutes(payload, "artemis").map).toMatchObject({ dynamics: { gps: precise.slice(0, 2) } });
  const cleared = { dynamics: { ...payload.dynamics, gps_precise: [] } };
  expect(normalizeLivePayload(cleared, "artemis")).toMatchObject({ lat: 30.28, lon: -97.73 });
  expect(buildNormalizedSensorRoutes(cleared, "artemis").map).toMatchObject({ dynamics: { gps: [30.28, -97.73] } });
});

it("projects GPS history without fetching unrelated dynamics channels", async () => {
  const stamp = 1_700_000_000_000;
  vi.mocked(gatewayJSON).mockResolvedValue({ signals: [
    { signal: "dynamics.gps", group: "dynamics", repeated: true, aggregates: ["last"] },
    { signal: "dynamics.gps_precise", group: "dynamics", repeated: true, aggregates: ["last"] },
    { signal: "dynamics.gps_speed", group: "dynamics", aggregates: ["mean", "last"] },
  ] });
  vi.mocked(gatewayRows).mockResolvedValue([
    { ts: new Date(stamp).toISOString(), sample_time_ns: String(BigInt(stamp) * 1_000_000n), session_start_ms: "1", signal: "dynamics.gps", value: [30.28, -97.73] },
    { ts: new Date(stamp).toISOString(), sample_time_ns: String(BigInt(stamp) * 1_000_000n), session_start_ms: "1", signal: "dynamics.gps_precise", value: [30.28123456789, -97.73123456789] },
  ]);
  const gps = await new TelemetryService(getSettings("artemis")).gps(stamp, stamp + 1000);
  expect(gps.points).toEqual([{ t: stamp, lat: 30.28123456789, lon: -97.73123456789 }]);
  expect(gatewayRows).toHaveBeenCalledTimes(1);
  expect(vi.mocked(gatewayRows).mock.calls[0][0].signals).toEqual(["dynamics.gps", "dynamics.gps_precise"]);
});

it("includes newly discovered Artemis channels in the default export", async () => {
  const stamp = 1_700_000_000_000;
  vi.mocked(gatewayJSON).mockResolvedValue({ signals: [
    { signal: "controls.new_sensor", group: "controls", value_type: "float", unit: "%", aggregates: ["mean", "last"] },
  ] });
  vi.mocked(gatewayRows).mockResolvedValue([
    { ts: new Date(stamp).toISOString(), sample_time_ns: String(BigInt(stamp) * 1_000_000n), session_start_ms: "1", signal: "controls.new_sensor", value: 17.5 },
  ]);
  const log = await new TelemetryService(getSettings("artemis")).datalog([], stamp, stamp + 1000);
  expect(log.channels.get("controls_new_sensor")?.samples).toEqual([{ t: 0, value: 17.5 }]);
});
