import { expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { gatewayLive } from "../../src/lib/query/live";
import { gatewayRequest } from "../../src/lib/query/gateway";

vi.mock("../../src/lib/query/gateway", () => ({ gatewayRequest: vi.fn() }));
vi.mock("../../src/lib/kafka/kafkaConsumer", () => ({ buildNormalizedSensorRoutes: (payload: unknown) => ({ sensor_data: payload }) }));
vi.mock("../../src/lib/motec/live", () => ({ normalizeLivePayload: (payload: unknown) => payload }));

it("forwards freshness changes without duplicating unchanged native samples", async () => {
  const rows = [{ ts: "2026-11-12T12:00:00.000Z", sample_time_ns: "1794484800000000000", session_start_ms: "1", signal: "controls.apps", value: 0 }];
  const live = { ready: true, kafka_lag_messages: 5, latest_sample_ms: Date.parse(rows[0].ts), observed_at_ms: Date.parse(rows[0].ts) + 1000 };
  const events = [
    { type: "data", page: { data: rows, live } },
    { type: "data", page: { data: rows, live: { ...live, kafka_lag_messages: 0 } } },
    { type: "gap", message: "Kafka coverage is incomplete" },
  ];
  vi.mocked(gatewayRequest).mockResolvedValue(new Response(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join("")));
  const response = await gatewayLive(new NextRequest("http://localhost/api/kafka/stream?topics=sensor_data"), "kafka");
  const received = (await response.text()).trim().split("\n\n").map(line => JSON.parse(line.slice(6)));
  const samples = received.filter(event => event.topic === "sensor_data");
  expect(samples).toHaveLength(1);
  expect(JSON.parse(samples[0].payload).controls.apps).toBe(0);
  expect(samples[0].live.kafka_lag_messages).toBe(5);
  const statuses = received.filter(event => event.kind === "status");
  expect(statuses.map(event => event.ok)).toEqual([true, true, false]);
  expect(statuses[1].live.kafka_lag_messages).toBe(0);
});
