import { expect, it } from "vitest";
import { buildNormalizedSensorRoutes } from "../../src/lib/kafka/kafkaConsumer";
import { normalizeLivePayload } from "../../src/lib/motec/live";

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
